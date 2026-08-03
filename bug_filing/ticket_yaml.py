import re
import yaml
from bug_filing.fuzzy_matcher import FuzzyMatcher


def _format_options(allowed_values):
    non_numeric = [v for v in allowed_values if not str(v).isdigit()]
    display = non_numeric if non_numeric else allowed_values
    truncated = display[:10]
    suffix = ", ..." if len(display) > 10 else ""
    return ", ".join(str(v) for v in truncated) + suffix


def _yaml_key(field_name):
    """Convert a human-readable field name to an underscore_case YAML key."""
    s = re.sub(r'[^a-zA-Z0-9\s]', '', field_name).lower()
    return re.sub(r'\s+', '_', s).strip('_')


# YAML keys handled outside the Jira create-issue payload: Jira has no
# "status" field to set at creation (new issues always start at their
# workflow's initial status — see references/dom-bug-createmeta.json), and
# comments are added via a separate endpoint. Both are applied after the
# issue is created (see ticket_cli._submit_one).
POST_CREATION_KEYS = ("status", "add_comments")


def _normalize_post_creation_key(key):
    """Like `_yaml_key`, but keeps underscores as word separators instead of
    stripping them — needed since POST_CREATION_KEYS are already underscore_case."""
    s = re.sub(r'[^a-zA-Z0-9\s_]', '', str(key)).lower()
    return re.sub(r'[\s_]+', '_', s).strip('_')


def split_post_creation_fields(data):
    """
    Split a parsed ticket YAML mapping into (create_fields, post_creation_fields).

    Keys that normalise to one of POST_CREATION_KEYS are pulled into
    `post_creation_fields`; everything else is left in `create_fields` for
    the normal Jira field-matching path.
    """
    create_fields, post_fields = {}, {}
    for key, value in data.items():
        normalized = _normalize_post_creation_key(key)
        if normalized in POST_CREATION_KEYS:
            post_fields[normalized] = value
        else:
            create_fields[key] = value
    return create_fields, post_fields


def post_creation_fields(yaml_string):
    """Return the post-creation pseudo-fields parsed from a ticket YAML string."""
    data = yaml.safe_load(yaml_string)
    if not isinstance(data, dict):
        return {}
    _, post_fields = split_post_creation_fields(data)
    return post_fields


def ticket_template(index, minimal=False, maximal=False):
    """
    Generate a YAML template string for a ticket.

    By default, user-required fields are included plus any unambiguous fields
    (required fields with exactly one allowed value) pre-filled with that value.
    With maximal=True, all fields are included; unambiguous ones are still
    pre-filled. ADF fields use block scalar (|) format. Choice fields include a
    comment listing valid options.  The YAML keys are underscore_case versions
    of the human-readable field names.

    If minimal=True, comments and inter-field blank lines are omitted.
    """
    if maximal:
        all_fields = [f for f in index.allowed_fields()]
        fields = sorted(all_fields, key=lambda f: (0 if f == "Summary" else 1))
    else:
        user_fields = sorted(index.user_required, key=lambda f: (0 if f == "Summary" else 1, f))
        fields = user_fields + sorted(index.unambiguous)

    lines = []
    for field_name in fields:
        key = _yaml_key(field_name)

        if not minimal:
            lines.append(f"# {field_name}")

        if field_name in index.unambiguous:
            value = next(iter(index.unambiguous[field_name].values()))
            lines.append(f"{key}: {value}")

        else:
            tag = index.field_tag(field_name)
            is_array = index.field_is_array(field_name)

            if is_array:
                if not minimal and tag == "choice":
                    av = index.allowed_values(field_name)
                    lines.append(f"# options: {_format_options(av)}")
                lines.append(f"{key}:")
                lines.append(f"  -")

            elif tag == "adf":
                lines.append(f"{key}: |")
                lines.append(f"  Enter markdown content here")

            elif tag == "choice":
                if not minimal:
                    av = index.allowed_values(field_name)
                    lines.append(f"# options: {_format_options(av)}")
                lines.append(f"{key}:")

            else:  # string, user, sprint, etc.
                lines.append(f"{key}:")

        if not minimal:
            lines.append("")

    if minimal and lines:
        lines.append("")
    return "\n".join(lines)


def validate_ticket_yaml(index, yaml_string):
    """
    Validate a YAML ticket string against an IssueFieldIndex.

    Returns {"ok": True} if the YAML is valid and all required fields are
    present and unambiguous. Otherwise returns a dict with any of:
      - "parse_error":      string describing a YAML parse failure
      - "missing_fields":   list of required field names absent from the YAML
      - "ambiguous_fields": {yaml_key: [candidate field names]}
      - "ambiguous_values": {field_name: {raw_value: [candidate values]}}
      - "invalid_values":   {field_name: error string} for envelope-backed fields
                            (sprint, user) where the lookup failed
    """
    try:
        data = yaml.safe_load(yaml_string)
    except yaml.YAMLError as e:
        return {"parse_error": str(e)}

    if not isinstance(data, dict):
        return {"parse_error": "Expected a YAML mapping at the top level"}

    data, post_fields = split_post_creation_fields(data)

    field_matcher = FuzzyMatcher(index.allowed_fields())

    resolved = {}        # field_name -> raw value from YAML
    ambiguous_fields = {}
    unknown_fields = []

    for yaml_key, value in data.items():
        matches = field_matcher.lookup(str(yaml_key))
        if len(matches) == 1:
            resolved[matches[0]] = value
        elif len(matches) > 1:
            ambiguous_fields[yaml_key] = matches
        else:
            unknown_fields.append(yaml_key)

    def _is_blank(value):
        return not value or value == [None]

    missing_fields = [f for f in index.user_required if _is_blank(resolved.get(f))]

    ambiguous_values = {}
    invalid_values = {}
    for field_name, value in resolved.items():
        if _is_blank(value):
            continue
        matcher = index.value_matcher(field_name)
        if matcher is not None:
            for v in (value if isinstance(value, list) else [value]):
                matches = matcher.lookup(str(v))
                if len(matches) != 1:
                    canonicals = {index._canonical_value(field_name, m) for m in matches}
                    if len(canonicals) != 1:
                        ambiguous_values.setdefault(field_name, {})[str(v)] = matches
        else:
            # Envelope-backed type (sprint, user, …): trial-run the envelope so
            # that lookup errors surface here rather than only at build time.
            try:
                index.field(field_name, value)
            except ValueError as e:
                invalid_values[field_name] = str(e)

    if "status" in post_fields:
        status_value = post_fields["status"]
        if not isinstance(status_value, str) or not status_value.strip():
            invalid_values["status"] = "must be a non-empty string"

    if "add_comments" in post_fields:
        comments_value = post_fields["add_comments"]
        if not isinstance(comments_value, list) or not all(isinstance(c, str) for c in comments_value):
            invalid_values["add_comments"] = "must be a list of markdown strings"

    if not (missing_fields or ambiguous_fields or ambiguous_values or unknown_fields or invalid_values):
        return {"ok": True}

    errors = {"ok": False}
    if missing_fields:
        errors["missing_fields"] = missing_fields
    if ambiguous_fields:
        errors["ambiguous_fields"] = ambiguous_fields
    if ambiguous_values:
        errors["ambiguous_values"] = ambiguous_values
    if unknown_fields:
        errors["unknown_fields"] = unknown_fields
    if invalid_values:
        errors["invalid_values"] = invalid_values
    return errors


def split_yaml_documents(text):
    """Split a YAML text into a list of per-document strings.

    Splits on document-start markers (``---`` on its own line).  Returns a
    list with one element per non-empty document.  A single-document input
    (no ``---`` separators) returns a one-element list.
    """
    parts = re.split(r'(?m)^---[ \t]*$', text)
    docs = [p for p in parts if p.strip()]
    return docs if docs else [text]


def build_ticket_payload(index, yaml_string):
    """
    Build a Jira issue create payload dict from a YAML ticket string.

    Raises ValueError if any field or value cannot be resolved unambiguously.
    The returned dict has the shape {"fields": {...}} ready for the Jira API.
    """
    data = yaml.safe_load(yaml_string)
    data, _ = split_post_creation_fields(data)
    payload = index.fuzzy_payload(data)
    for field_name, enveloped_value in index.unambiguous.items():
        payload["fields"].setdefault(index.name_to_key[field_name], enveloped_value)
    return payload
