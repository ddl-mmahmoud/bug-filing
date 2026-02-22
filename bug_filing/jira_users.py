import json
import logging

from bug_filing.jira_session import JIRA_BASE_URL

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


# Simple module-level cache
_jira_user_ids_cache = None


def get_jira_user_ids(session):
    global _jira_user_ids_cache
    if _jira_user_ids_cache is not None:
        return _jira_user_ids_cache

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
