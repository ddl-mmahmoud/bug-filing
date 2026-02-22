import json
import logging

from bug_filing.jira_session import JIRA_BASE_URL


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
            if user.get("displayName") and user.get("accountId")
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
