import json
import logging
import os
import tempfile

from bug_filing.fuzzy_matcher import FuzzyMatcher
from bug_filing.issue_field_index import FieldTypeHandler
from bug_filing.jira_session import jira_base_url

_CACHE_PATH = os.path.join(tempfile.gettempdir(), "jira_users_cache.json")

# Simple module-level cache
_jira_user_ids_cache = None


def get_jira_user_ids(session):
    global _jira_user_ids_cache
    if _jira_user_ids_cache is not None:
        return _jira_user_ids_cache

    if os.path.exists(_CACHE_PATH):
        logging.info(f"Loading Jira user cache from {_CACHE_PATH}")
        with open(_CACHE_PATH) as f:
            _jira_user_ids_cache = json.load(f)
        return _jira_user_ids_cache

    offset = 0
    limit = 1000
    jira_user_ids = {}

    while True:
        params = {"startAt": offset, "maxResults": limit}
        response = session.request(
            "GET",
            url=f"{jira_base_url()}/rest/api/3/users/search",
            params=params,
        )
        logging.info(f"jira get users offset {offset}")

        raw_batch = json.loads(response.text)
        batch_jira_user_ids = {
            user["displayName"]: user["accountId"]
            for user in raw_batch
            if (
                user.get("displayName")
                and user.get("accountId")
                and user["active"]
                and user["accountType"] == "atlassian"
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
    with open(_CACHE_PATH, "w") as f:
        json.dump(_jira_user_ids_cache, f)
    logging.info(f"Saved Jira user cache to {_CACHE_PATH}")
    return _jira_user_ids_cache


class UserHandler(FieldTypeHandler):
    """Type handler for Jira user and array-of-user fields."""

    tag = "user"

    def __init__(self, user_ids):
        self._user_ids = user_ids  # {display_name: account_id}

    def detect(self, meta):
        schema = meta["schema"]
        return schema.get("type") == "user" or schema.get("items") == "user"

    def matcher(self, meta):
        return FuzzyMatcher(self._user_ids.keys())

    def envelope(self, value, meta):
        account_id = self._user_ids.get(value)
        if not account_id:
            raise ValueError(f"User {value!r} not found in Jira user directory")
        return {"id": account_id}

    def allowed(self, meta):
        return "USER"
