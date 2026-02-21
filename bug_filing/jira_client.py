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


def generate_jira_message_with_links(original_text):
    link_pattern = r"\[([^\]]+)\]\(([^)]+)\)"
    links = re.findall(link_pattern, original_text)
    failure_message = html.unescape(original_text.split("\n\n")[0])
    content = [_para(_text(failure_message))]
    for link_text, link_url in links:
        content.append(_para(_text(link_text, href=link_url)))
    return {"content": content}


# Simple module-level cache
_jira_user_ids_cache = None


def get_jira_user_ids():
    global _jira_user_ids_cache
    if _jira_user_ids_cache is not None:
        return _jira_user_ids_cache

    auth = HTTPBasicAuth(
        os.environ["JIRA_API_USERNAME"], os.environ["JIRA_API_PASSWORD"]
    )
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    offset = 0
    limit = 1000
    jira_user_ids = {}

    while True:
        params = {"startAt": offset, "maxResults": limit}
        response = requests.request(
            "GET",
            headers=headers,
            auth=auth,
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
            "customfield_13165": {
                "version": 1,
                "type": "doc",
                "content": [_para(_text("TBD"))],
            },
            "customfield_13041": {
                "version": 1,
                "type": "doc",
                "content": [
                    _para(
                        _text("Run e2e test: "),
                        _text(testcase_link, href=testcase_link),
                    )
                ],
            },
            "customfield_13042": {
                "version": 1,
                "type": "doc",
                "content": [
                    _para(_text("The feature should be validated by the e2e test."))
                ],
            },
            "customfield_13043": {
                "version": 1,
                "type": "doc",
                "content": stylized_error_message["content"],
            },
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    _para(
                        _text(testrail_link, href=testrail_link),
                        _text("  \n\n"),
                        _text(f"To run this test in your PR, run this command: {pr_trigger_command}"),
                        _text("  \n\n"),
                        _text(result_info["custom_config_text"]),
                        _text(add_description),
                    )
                ],
            },
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
