"""Read a Jira ticket and convert it to a YAML-friendly dict.

Field conversion rules:
- ADF documents (description, environment, custom textareas) → Markdown string
- User objects ({"accountId": ..., "displayName": ...}) → display name string
- Sprint lists ([{"id": N, "name": "...", "state": "..."}]) → sprint name string
- Choice-like dicts with value/name/key → that scalar
- Lists of the above → list of scalars
- Everything else → raw value (yaml.dump will serialise it as-is)

Null / empty values are dropped entirely.
"""

import re

import yaml

from bug_filing.adf import to_markdown
from bug_filing.jira_session import jira_base_url


# ------------------------------------------------------------------ #
# Field-name helpers                                                   #
# ------------------------------------------------------------------ #

def _yaml_key(field_name):
    """Convert a human-readable field name to underscore_case YAML key."""
    s = re.sub(r'[^a-zA-Z0-9\s]', '', field_name).lower()
    return re.sub(r'\s+', '_', s).strip('_')


# ------------------------------------------------------------------ #
# Value conversion                                                     #
# ------------------------------------------------------------------ #

def _is_adf(value):
    return isinstance(value, dict) and value.get("type") == "doc" and "content" in value


def _is_user(value):
    return isinstance(value, dict) and "accountId" in value


def _is_sprint_list(value):
    return (
        isinstance(value, list) and value and
        all(isinstance(v, dict) and "name" in v and "id" in v and "state" in v for v in value)
    )


def _extract_choice(obj):
    """Pull the most useful scalar out of a choice-like dict, or return None."""
    for k in ("value", "name", "key"):
        if obj.get(k):
            return obj[k]
    return None


def _convert_value(raw_value):
    """Return a YAML-friendly Python object, or None to indicate 'skip this field'."""
    if raw_value is None:
        return None
    if raw_value == "" or raw_value == [] or raw_value == {}:
        return None

    # ADF document → Markdown.
    # Strip trailing whitespace per line: YAML literal block style (|) disallows
    # trailing spaces, and PyYAML silently falls back to double-quoted style if any
    # line has trailing whitespace.
    if _is_adf(raw_value):
        md = to_markdown(raw_value)
        md = '\n'.join(line.rstrip() for line in md.splitlines()).strip()
        return md or None

    # User object → display name
    if _is_user(raw_value):
        return raw_value.get("displayName") or raw_value.get("emailAddress")

    if isinstance(raw_value, list):
        if not raw_value:
            return None

        # Sprint list → single name or list of names
        if _is_sprint_list(raw_value):
            names = [v["name"] for v in raw_value]
            return names[0] if len(names) == 1 else names

        # List of user objects
        if all(_is_user(v) for v in raw_value):
            return [v.get("displayName") or v.get("emailAddress") for v in raw_value]

        # List of ADF documents (uncommon)
        if all(_is_adf(v) for v in raw_value):
            converted = [to_markdown(v).strip() for v in raw_value]
            return [c for c in converted if c] or None

        # List of choice-like dicts (components, fix versions, labels-as-objects, …)
        if all(isinstance(v, dict) and not _is_user(v) and not _is_adf(v) for v in raw_value):
            extracted = [_extract_choice(v) for v in raw_value]
            if all(isinstance(e, str) for e in extracted):
                return extracted
            return raw_value  # fall back to raw

        # Plain scalars
        if all(isinstance(v, (str, int, float, bool)) for v in raw_value):
            return raw_value

        return raw_value  # mixed / unknown list → raw

    if isinstance(raw_value, dict):
        extracted = _extract_choice(raw_value)
        if extracted is not None:
            return extracted
        return raw_value  # opaque dict → raw

    # Scalar (str, int, float, bool)
    return raw_value


# ------------------------------------------------------------------ #
# Ticket fetching                                                      #
# ------------------------------------------------------------------ #

def get_ticket(session, issue_key):
    """Fetch a Jira issue, requesting the ``names`` expansion for field labels."""
    base_url = jira_base_url()
    url = f"{base_url}/rest/api/3/issue/{issue_key}"
    response = session.get(url, params={"expand": "names"})
    if response.status_code == 404:
        raise ValueError(f"Ticket not found: {issue_key}")
    if not response.ok:
        raise ValueError(f"Jira API error {response.status_code}: {response.text}")
    return response.json()


# ------------------------------------------------------------------ #
# Conversion                                                           #
# ------------------------------------------------------------------ #

# Fields that are redundant, aggregate, or otherwise not worth emitting.
_SKIP_FIELDS = {
    "lastViewed", "statusCategory", "watches", "votes",
    "worklog", "comment", "attachment", "subtasks",
    "aggregateprogress", "progress",
    "aggregatetimespent", "aggregatetimeoriginalestimate", "aggregatetimeestimate",
}

# Emit these well-known fields first, in this order.
_PRIORITY_KEYS = [
    "summary", "issuetype", "project", "status", "priority",
    "assignee", "reporter", "description", "environment",
]


def ticket_to_yaml_dict(data):
    """Convert a Jira issue API response dict to an ordered YAML-friendly dict."""
    fields = data.get("fields", {})
    names = data.get("names", {})  # field_key → human-readable label

    result = {}
    result["key"] = data.get("key")

    emitted = set()
    used_yaml_keys = {"key"}

    def _emit(field_key, raw_value):
        converted = _convert_value(raw_value)
        if converted is None:
            emitted.add(field_key)
            return
        human_name = names.get(field_key)
        if human_name:
            yaml_key = _yaml_key(human_name)
            # Fall back to raw field_key on collision
            if yaml_key in used_yaml_keys:
                yaml_key = field_key
        else:
            yaml_key = field_key
        result[yaml_key] = converted
        emitted.add(field_key)
        used_yaml_keys.add(yaml_key)

    for fk in _PRIORITY_KEYS:
        if fk in fields and fk not in emitted and fk not in _SKIP_FIELDS:
            _emit(fk, fields[fk])

    for fk, raw_value in fields.items():
        if fk not in emitted and fk not in _SKIP_FIELDS:
            _emit(fk, raw_value)

    return result


# ------------------------------------------------------------------ #
# YAML serialisation                                                   #
# ------------------------------------------------------------------ #

class _Dumper(yaml.Dumper):
    pass


def _literal_str_representer(dumper, data):
    if '\n' in data:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


_Dumper.add_representer(str, _literal_str_representer)


def ticket_to_yaml(data):
    """Convert a Jira issue API response to a YAML string."""
    d = ticket_to_yaml_dict(data)
    return yaml.dump(d, Dumper=_Dumper, default_flow_style=False,
                     allow_unicode=True, sort_keys=False)
