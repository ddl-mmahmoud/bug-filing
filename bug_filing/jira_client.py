import os
import re
import html
import requests
import logging
from requests.auth import HTTPBasicAuth
import json

from bug_filing.testrail_client import get_result_info, get_test_info
from bug_filing.fleetcommand_client import fetch_domino_version


JIRA_BASE_URL = "https://dominodatalab.atlassian.net"
JIRA_ISSUE_URL = "https://dominodatalab.atlassian.net/rest/api/3/issue"

JIRA_USER_BLACKLIST = [
    "AP Domino",
    "aws -owner",
    "BCP Customers",
    "BCP Internal",
    "billing finance",
    "Catalys Integration",
    "CS Reporting",
    "domino 360",
    "Domino Academy",
    "Domino Customer Marketing",
    "Domino Partner Network",
    "Domino Podcast",
    "Domino Trial",
    "Employee Experience",
    "eng-platform sfdc",
    "Finance Integration",
    "HR Benefits",
    "HR Leave",
    "HR Operations",
    "HR Team",
    "HR1 HR",
    "HR2 HR",
    "HR3 HR",
    "InsideView Integration",
    "Intacct Integration",
    "integration admin",
    "Integration Test",
    "Jira Domino 360",
    "Jira GitHub",
    "Jira Integration",
    "New Relic",
    "One Password",
    "People AI Integration",
    "Platform Service",
    "Pract User",
    "QE Platform Service Account",
    "qe service",
    "Rev Registrations",
    "Rev Speakers",
    "Rev Sponsors",
    "rfqa domino",
    "Security Ops",
    "Selenium Test",
    "System Tasks",
    "system test",
    "teleportcloud admin",
    "Whistleblower Reporting",
    "zendesk integration",
    "Zoom Room",
]


def _text(text, href=None):
    node = {"type": "text", "text": text}
    if href:
        node["marks"] = [{"type": "link", "attrs": {"href": href}}]
    return node


def _para(*content):
    return {"type": "paragraph", "content": list(content)}


def _doc(*content):
    return {
        "version": 1,
        "type": "doc",
        "content": list(content),
    }


class FuzzyMatcher:
    @staticmethod
    def _sanitize(s):
        s = re.sub(r'[^a-zA-Z0-9\s]', '', s).lower()
        return re.sub(r'\s+', ' ', s).strip()

    @staticmethod
    def _tokenize(s):
        return FuzzyMatcher._sanitize(s).split()

    def __init__(self, strings):
        self._originals = list(strings)
        self._token_to_originals = {}
        self._original_to_tokens = {}
        self._original_to_sanitized = {}
        self._original_to_collapsed = {}
        for orig in self._originals:
            tokens = self._tokenize(orig)
            sanitized = self._sanitize(orig)
            self._original_to_tokens[orig] = tokens
            self._original_to_sanitized[orig] = sanitized
            self._original_to_collapsed[orig] = sanitized.replace(' ', '')
            for token in tokens:
                self._token_to_originals.setdefault(token, set()).add(orig)

    def lookup(self, s):
        query_tokens = self._tokenize(s)
        collapsed_query = self._sanitize(s).replace(' ', '')

        matched = set()
        for qt in query_tokens:
            for token, originals in self._token_to_originals.items():
                if qt in token:
                    matched.update(originals)
        for orig, collapsed in self._original_to_collapsed.items():
            if collapsed_query in collapsed:
                matched.add(orig)

        if len(matched) > 1:
            # Prefer candidates where any query token is a direct substring of any of their tokens,
            # or the collapsed query is a substring of the collapsed candidate
            substring = {
                orig for orig in matched
                if any(qt in token for qt in query_tokens for token in self._original_to_tokens[orig])
                or collapsed_query in self._original_to_collapsed[orig]
            }
            if substring and substring != matched:
                matched = substring

        if len(matched) > 1:
            # Further prefer candidates where any query token matches from the start of a token
            prefix = {
                orig for orig in matched
                if any(token.startswith(qt) for qt in query_tokens for token in self._original_to_tokens[orig])
            }
            if prefix and prefix != matched:
                matched = prefix

        if len(matched) > 1:
            # Further prefer candidates whose full sanitized form starts with the sanitized query,
            # or whose collapsed form starts with the collapsed query
            sanitized_query = self._sanitize(s)
            full_prefix = {
                orig for orig in matched
                if self._original_to_sanitized[orig].startswith(sanitized_query)
                or self._original_to_collapsed[orig].startswith(collapsed_query)
            }
            if full_prefix and full_prefix != matched:
                matched = full_prefix

        if len(matched) > 1:
            # Prefer candidates whose collapsed form exactly equals the collapsed query
            exact = {
                orig for orig in matched
                if self._original_to_collapsed[orig] == collapsed_query
            }
            if exact and exact != matched:
                matched = exact

        return [orig for orig in self._originals if orig in matched]


class IssueFieldIndex:
    """
    Queries the Jira createmeta endpoint for a given project and issue type,
    caches the raw field definitions, and provides a name-to-key index for
    looking up a field's API key by its human-readable name.
    """

    def __init__(self, session, project, issuetype):
        url = f"{JIRA_BASE_URL}/rest/api/3/issue/createmeta"
        params = {
            "projectKeys": project,
            "issuetypeNames": issuetype,
            "expand": "projects.issuetypes.fields",
        }
        response = session.get(url, params=params)
        response.raise_for_status()

        data = response.json()
        projects = data.get("projects", [])
        if not projects:
            raise ValueError(f"Project {project!r} not found")
        issuetypes = projects[0].get("issuetypes", [])
        if not issuetypes:
            raise ValueError(f"Issue type {issuetype!r} not found in project {project!r}")

        self.fields = issuetypes[0]["fields"]
        self.name_to_key = {meta["name"]: key for key, meta in self.fields.items()}
        self.required = [meta["name"] for meta in self.fields.values() if meta["required"]]
        self._types = None
        self._unambiguous = None
        self._matchers = {}

    _IDENTIFIER_KEYS = ["value", "key", "name", "id"]
    _ADF_SYSTEMS = {"description", "environment"}

    def _item_type(self, meta):
        if meta.get("allowedValues"):
            keys = [k for k in self._IDENTIFIER_KEYS if k in meta["allowedValues"][0]]
            return ("choice", keys)
        if meta["schema"].get("custom", "").endswith(":textarea") or meta["schema"].get("system") in self._ADF_SYSTEMS:
            return ("adf",)
        return ("string",)

    def _field_type(self, meta):
        if meta["schema"]["type"] == "array":
            return ("array", self._item_type(meta))
        return self._item_type(meta)

    @property
    def user_required(self):
        return [name for name in self.required if name not in self.unambiguous]

    @property
    def unambiguous(self):
        if self._unambiguous is None:
            result = {}
            for meta in self.fields.values():
                if not meta["required"]:
                    continue
                allowed = meta.get("allowedValues", [])
                if len(allowed) != 1:
                    continue
                for k in self._IDENTIFIER_KEYS:
                    if k in allowed[0]:
                        result[meta["name"]] = {k: allowed[0][k]}
                        break
            self._unambiguous = result
        return self._unambiguous

    @property
    def types(self):
        if self._types is None:
            self._types = {meta["name"]: self._field_type(meta) for meta in self.fields.values()}
        return self._types

    def field(self, name, value):
        return (self.name_to_key[name], self._enveloped(value, self.types[name]))

    def value_matcher(self, field_name):
        if field_name not in self._matchers:
            av = self.allowed_values(field_name)
            self._matchers[field_name] = FuzzyMatcher(av) if isinstance(av, list) else None
        return self._matchers[field_name]

    def fuzzy_payload(self, fields):
        result = {}
        for name, value in fields.items():
            key, enveloped = self.fuzzy_field(name, value)
            result[key] = enveloped
        return {"fields": result}

    def fuzzy_field(self, name, value):
        field_matches = FuzzyMatcher(self.allowed_fields()).lookup(name)
        if len(field_matches) != 1:
            raise ValueError(f"Field {name!r} matched {len(field_matches)} candidates: {field_matches}")
        resolved_name = field_matches[0]

        matcher = self.value_matcher(resolved_name)
        if matcher is not None:
            def resolve_value(v):
                matches = matcher.lookup(str(v))
                if len(matches) != 1:
                    canonicals = {self._canonical_value(resolved_name, m) for m in matches}
                    if len(canonicals) == 1:
                        return canonicals.pop()
                    raise ValueError(f"Value {v!r} for field {resolved_name!r} matched {len(matches)} candidates: {matches}")
                return matches[0]
            resolved_value = [resolve_value(v) for v in value] if isinstance(value, list) else resolve_value(value)
        else:
            resolved_value = value

        return self.field(resolved_name, resolved_value)

    def _canonical_value(self, field_name, value_string):
        """Return the preferred identifier value for the allowedValues entry that contains value_string."""
        field_key = self.name_to_key[field_name]
        meta = self.fields[field_key]
        field_type = self.types[field_name]
        id_keys = field_type[1] if field_type[0] == "choice" else field_type[1][1] if field_type[0] == "array" else None
        if id_keys is None:
            return value_string
        for entry in meta.get("allowedValues", []):
            if any(entry.get(k) == value_string for k in id_keys):
                for k in id_keys:
                    if k in entry:
                        return entry[k]
        return value_string

    def allowed_values(self, field_name):
        field_key = self.name_to_key[field_name]
        meta = self.fields[field_key]
        return self._allowed_for_type(meta, self.types[field_name])

    def allowed_fields(self):
        return list(self.name_to_key.keys())

    def _allowed_for_type(self, meta, field_type):
        tag = field_type[0]
        if tag == "array":
            return self._allowed_for_type(meta, field_type[1])
        if tag == "choice":
            return [
                av[k]
                for av in meta.get("allowedValues", [])
                for k in field_type[1]
                if k in av
            ]
        if tag == "string":
            return "SCALAR"
        if tag == "adf":
            return "ADF"
        raise ValueError(f"Unknown field type: {tag!r}")

    def _enveloped(self, value, field_type):
        tag = field_type[0]

        if tag == "array":
            if not isinstance(value, list):
                raise ValueError(f"Expected a list for array field, got {type(value).__name__}")
            return [self._enveloped(v, field_type[1]) for v in value]

        if isinstance(value, list):
            raise ValueError(f"Expected {tag}, got a list")

        if tag == "adf":
            if isinstance(value, str):
                from bug_filing.adf import from_markdown
                return from_markdown(value)
            if not isinstance(value, dict):
                raise ValueError(f"Expected ADF doc or markdown string, got {type(value).__name__}")
            return value

        if tag == "string":
            if isinstance(value, dict):
                raise ValueError(f"Expected plain string, got dict")
            return value

        if tag == "choice":
            if isinstance(value, dict):
                raise ValueError(f"Expected scalar for choice field, got dict")
            return {field_type[1][0]: value}

        raise ValueError(f"Unknown field type: {tag!r}")


def generate_jira_message_with_links(original_text):
    link_pattern = r"\[([^\]]+)\]\(([^)]+)\)"
    links = re.findall(link_pattern, original_text)
    failure_message = html.unescape(original_text.split("\n\n")[0])
    content = [_para(_text(failure_message))]
    for link_text, link_url in links:
        content.append(_para(_text(link_text, href=link_url)))
    return _doc(*content)


def jira_requests_session():
    session = requests.Session()
    auth = HTTPBasicAuth(
        os.environ["JIRA_API_USERNAME"], os.environ["JIRA_API_PASSWORD"]
    )
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    session.auth = auth
    session.headers = headers
    return session


# Simple module-level cache
_jira_user_ids_cache = None


def get_jira_user_ids():
    global _jira_user_ids_cache
    if _jira_user_ids_cache is not None:
        return _jira_user_ids_cache

    session = jira_requests_session()

    offset = 0
    limit = 1000
    jira_user_ids = {}

    while True:
        params = {"startAt": offset, "maxResults": limit}
        response = session.request(
            "GET",
            url=f"{JIRA_BASE_URL}/rest/api/3/users/search",
            params=params,
        )
        logging.info(f"jira get users offset {offset}")

        raw_batch = json.loads(response.text)
        batch_jira_user_ids = {
            user["displayName"]: user["accountId"]
            for user in raw_batch
            if (
                user["active"]
                and user["accountType"] == "atlassian"
                and " " in user["displayName"]
                and "@" not in user["displayName"]
                and user["displayName"] not in JIRA_USER_BLACKLIST
            )
        }
        jira_user_ids.update(batch_jira_user_ids)
        offset += limit

        if len(raw_batch) < limit:
            break

    _jira_user_ids_cache = {
        user: jira_user_ids[user]
        for user in sorted(jira_user_ids.keys(), key=str.lower)
    }
    return _jira_user_ids_cache


def create_jira_bug(
    test_id,
    summary,
    severity,
    is_blocking,
    add_description,
    failure_reason,
    engteam,
    component,
    assignee="",
    reporter="QE Automation",
):
    """
    Creates a Jira issue of type 'Bug' in the DOM project.

    Parameters:
      test_id:         TestRail test ID associated with the failure
      summary:         Title of the Jira bug
      severity:        Bug severity (e.g. 'S0', 'S1', 'S2', 'S3')
      is_blocking:     'on' to label as blocking, anything else for not-blocking
      add_description: Extra text appended to the ticket description
      failure_reason:  Failure categorisation (e.g. 'Product Bug', 'System Issues')
      engteam:         Engineering team name (e.g. 'Develop', 'UI')
      component:       Jira component name (e.g. 'Executions - Jobs')
      assignee:        Display name of the assignee (optional)
      reporter:        Display name of the reporter (default: 'QE Automation')

    Returns:
      The created Jira ticket key (e.g. 'DOM-12345')
    """
    auth = HTTPBasicAuth(
        os.environ["JIRA_API_USERNAME"], os.environ["JIRA_API_PASSWORD"]
    )
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    result_info = get_result_info(test_id)["results"][0]
    test_info = get_test_info(test_id)

    try:
        error_message = result_info["comment"]
    except KeyError:
        logging.error(f"Unable to find 'comment' in {result_info}")
        error_message = ""

    stylized_error_message = generate_jira_message_with_links(error_message)

    label = "blocking" if is_blocking == "on" else "not-blocking"
    testrail_link = f"https://dominodatalab.testrail.io/index.php?/tests/view/{test_id}"
    testcase_link = f"https://dominodatalab.testrail.io/index.php?/cases/view/{test_info['case_id']}"

    catalog = json.loads(result_info["custom_config_text"])
    domino_version = fetch_domino_version(catalog["catalog_id"], catalog["catalog_version"])
    pr_trigger_command = (
        f"/test-e2e {catalog['test_type']} --tags=testrail({test_info['case_id']})"
    )

    add_description = add_description.strip()
    if add_description:
        add_description = f" \n\nAdditional Description:\n{add_description}"

    payload_dict = {
        "fields": {
            "project": {"key": "DOM"},
            "issuetype": {"name": "Bug"},
            "summary": summary,
            "customfield_12907": {"key": "Severity", "value": severity},
            "versions": [{"name": domino_version}],
            "customfield_13165": _doc(_para(_text("TBD"))),
            "customfield_13041": _doc(
                _para(
                    _text("Run e2e test: "),
                    _text(testcase_link, href=testcase_link),
                )
            ),
            "customfield_13042": _doc(
                _para(_text("The feature should be validated by the e2e test."))
            ),
            "customfield_13043": stylized_error_message,
            "description": _doc(
                _para(
                    _text(testrail_link, href=testrail_link),
                    _text("  \n\n"),
                    _text(f"To run this test in your PR, run this command: {pr_trigger_command}"),
                    _text("  \n\n"),
                    _text(result_info["custom_config_text"]),
                    _text(add_description),
                )
            ),
            "components": [{"name": component}],
            "customfield_12952": {
                "key": "Eng. Team",
                "value": engteam,
            },
            "customfield_12959": {"key": "Found by", "value": "Automated Testcase"},
            "labels": [label],
            "customfield_15937": {
                "key": "Failure Reason",
                "value": failure_reason,
            },
        }
    }

    user_ids = get_jira_user_ids()
    reporter_id = user_ids.get(reporter, "")
    if reporter_id:
        payload_dict["fields"]["reporter"] = {"id": reporter_id}
    assignee_id = user_ids.get(assignee, "")
    if assignee_id:
        payload_dict["fields"]["assignee"] = {"id": assignee_id}

    payload = json.dumps(payload_dict)

    response = requests.request(
        "POST", JIRA_ISSUE_URL, data=payload, headers=headers, auth=auth
    )

    if response.status_code == 201:
        response_text = json.loads(response.text)
        logging.info(f"Successfully created a Jira ticket {response_text['key']}.")
    else:
        logging.error(payload)
        raise Exception(
            f"Error: Creating a Jira ticket returned status code {response.status_code}."
        )

    return response_text["key"]
