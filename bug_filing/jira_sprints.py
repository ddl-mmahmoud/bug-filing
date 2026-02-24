import json
import logging
import os
import tempfile
import time

from bug_filing.fuzzy_matcher import FuzzyMatcher
from bug_filing.issue_field_index import FieldTypeHandler
from bug_filing.jira_session import jira_base_url

_CACHE_PATH = os.path.join(tempfile.gettempdir(), "jira_sprints_cache.json")
_CACHE_TTL = 24 * 60 * 60  # 1 day

# Simple module-level cache: {sprint_name: sprint_id}
_jira_sprints_cache = None


def get_jira_sprints(session):
    global _jira_sprints_cache
    if _jira_sprints_cache is not None:
        return _jira_sprints_cache

    if os.path.exists(_CACHE_PATH):
        age = time.time() - os.path.getmtime(_CACHE_PATH)
        if age < _CACHE_TTL:
            logging.info(f"Loading Jira sprint cache from {_CACHE_PATH}")
            with open(_CACHE_PATH) as f:
                _jira_sprints_cache = json.load(f)
            return _jira_sprints_cache
        logging.info(f"Jira sprint cache expired ({age / 3600:.1f}h old), re-fetching")

    # 1. Fetch all boards (paginate; page size capped at 50 by the API)
    boards = []
    start = 0
    while True:
        response = session.request(
            "GET",
            url=f"{jira_base_url()}/rest/agile/1.0/board",
            params={"maxResults": 50, "startAt": start},
        )
        batch = json.loads(response.text)
        boards.extend(batch["values"])
        logging.info(f"jira get boards startAt {start}, got {len(batch['values'])}")
        if batch.get("isLast", True) or len(boards) >= batch["total"]:
            break
        start += len(batch["values"])

    # 2. Query active+future sprints for each scrum board; deduplicate by sprint id,
    #    preferring the entry where the board is the sprint's originBoardId.
    seen = {}  # sprint_id -> sprint object
    for board in boards:
        if board["type"] != "scrum":
            continue
        response = session.request(
            "GET",
            url=f"{jira_base_url()}/rest/agile/1.0/board/{board['id']}/sprint",
            params={"state": "active,future", "maxResults": 50},
        )
        if response.status_code != 200:
            logging.warning(
                f"Could not fetch sprints for board {board['id']} ({board['name']}): "
                f"HTTP {response.status_code}"
            )
            continue
        for sprint in json.loads(response.text).get("values", []):
            sid = sprint["id"]
            if sid not in seen or board["id"] == sprint.get("originBoardId"):
                seen[sid] = sprint

    # 3. Build name -> id mapping, sorted by name for readability
    sprints = {
        sprint["name"]: sprint["id"]
        for sprint in sorted(seen.values(), key=lambda s: s["name"].lower())
    }

    _jira_sprints_cache = sprints
    with open(_CACHE_PATH, "w") as f:
        json.dump(_jira_sprints_cache, f)
    logging.info(f"Saved Jira sprint cache to {_CACHE_PATH} ({len(sprints)} sprints)")
    return _jira_sprints_cache


class SprintHandler(FieldTypeHandler):
    """Type handler for Jira sprint (gh-sprint) array fields."""

    tag = "sprint"

    def __init__(self, sprints):
        self._sprints = sprints  # {sprint_name: sprint_id}

    def detect(self, meta):
        return meta["schema"].get("custom", "").endswith(":gh-sprint")

    def matcher(self, meta):
        return FuzzyMatcher(self._sprints.keys())

    def envelope(self, value, meta):
        sprint_id = self._sprints.get(value)
        if not sprint_id:
            raise ValueError(f"Sprint {value!r} not found among active/future sprints")
        return {"id": sprint_id}

    def allowed(self, meta):
        return "SPRINT"
