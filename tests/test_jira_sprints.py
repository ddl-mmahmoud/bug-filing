import json
import os
import time
from unittest.mock import MagicMock

import pytest

import bug_filing.jira_sprints as jira_sprints_module
from bug_filing.jira_sprints import get_jira_sprints, SprintHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _board(board_id, *, name="Test Board", type_="scrum"):
    return {"id": board_id, "name": name, "type": type_}


def _sprint(sprint_id, name, *, state="active", origin_board_id=None):
    s = {"id": sprint_id, "name": name, "state": state}
    if origin_board_id is not None:
        s["originBoardId"] = origin_board_id
    return s


def _boards_page(boards, *, is_last=True):
    return {"values": boards, "total": len(boards), "isLast": is_last}


def _sprints_page(sprints):
    return {"values": sprints}


def _make_session(*calls):
    """Build a mock session from a sequence of data dicts or (data, status) tuples."""
    session = MagicMock()
    responses = []
    for call in calls:
        if isinstance(call, tuple):
            data, status = call
        else:
            data, status = call, 200
        m = MagicMock()
        m.text = json.dumps(data)
        m.status_code = status
        responses.append(m)
    session.request.side_effect = responses
    return session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_memory_cache(monkeypatch):
    monkeypatch.setattr(jira_sprints_module, "_jira_sprints_cache", None)


@pytest.fixture
def cache_path(tmp_path, monkeypatch):
    path = str(tmp_path / "jira_sprints_cache.json")
    monkeypatch.setattr(jira_sprints_module, "_CACHE_PATH", path)
    return path


# ---------------------------------------------------------------------------
# get_jira_sprints — caching behaviour
# ---------------------------------------------------------------------------

def test_fetches_from_api_when_no_cache(cache_path):
    session = _make_session(
        _boards_page([_board(1)]),
        _sprints_page([_sprint(10, "Sprint One", origin_board_id=1)]),
    )
    result = get_jira_sprints(session)
    assert result == {"Sprint One": 10}
    session.request.assert_called()


def test_api_result_written_to_file(cache_path):
    session = _make_session(
        _boards_page([_board(1)]),
        _sprints_page([_sprint(10, "Sprint One", origin_board_id=1)]),
    )
    get_jira_sprints(session)
    with open(cache_path) as f:
        assert json.load(f) == {"Sprint One": 10}


def test_file_cache_used_without_calling_api(cache_path):
    with open(cache_path, "w") as f:
        json.dump({"Cached Sprint": 99}, f)
    session = MagicMock()
    result = get_jira_sprints(session)
    assert result == {"Cached Sprint": 99}
    session.request.assert_not_called()


def test_memory_cache_takes_priority_over_file(cache_path, monkeypatch):
    monkeypatch.setattr(jira_sprints_module, "_jira_sprints_cache", {"Mem Sprint": 42})
    with open(cache_path, "w") as f:
        json.dump({"Wrong Sprint": 0}, f)
    session = MagicMock()
    result = get_jira_sprints(session)
    assert result == {"Mem Sprint": 42}
    session.request.assert_not_called()


def test_file_cache_populates_memory_cache(cache_path):
    with open(cache_path, "w") as f:
        json.dump({"Cached Sprint": 99}, f)
    get_jira_sprints(MagicMock())
    assert jira_sprints_module._jira_sprints_cache == {"Cached Sprint": 99}


def test_expired_file_cache_triggers_api_fetch(cache_path):
    with open(cache_path, "w") as f:
        json.dump({"Old Sprint": 0}, f)
    two_days_ago = time.time() - 2 * 24 * 60 * 60
    os.utime(cache_path, (two_days_ago, two_days_ago))

    session = _make_session(
        _boards_page([_board(1)]),
        _sprints_page([_sprint(10, "New Sprint", origin_board_id=1)]),
    )
    result = get_jira_sprints(session)
    assert "New Sprint" in result
    assert "Old Sprint" not in result
    session.request.assert_called()


def test_fresh_file_cache_is_not_expired(cache_path):
    with open(cache_path, "w") as f:
        json.dump({"Fresh Sprint": 99}, f)
    twelve_hours_ago = time.time() - 12 * 60 * 60
    os.utime(cache_path, (twelve_hours_ago, twelve_hours_ago))

    session = MagicMock()
    result = get_jira_sprints(session)
    assert result == {"Fresh Sprint": 99}
    session.request.assert_not_called()


# ---------------------------------------------------------------------------
# get_jira_sprints — board filtering
# ---------------------------------------------------------------------------

def test_skips_kanban_boards(cache_path):
    session = _make_session(
        _boards_page([_board(1, type_="kanban"), _board(2)]),
        _sprints_page([_sprint(10, "Sprint One", origin_board_id=2)]),
    )
    result = get_jira_sprints(session)
    assert result == {"Sprint One": 10}
    # One request for boards, one for the single scrum board — kanban skipped.
    assert session.request.call_count == 2


def test_board_with_http_error_is_skipped(cache_path):
    # Board 1 returns 403; board 2 succeeds. Only board 2's sprints appear.
    session = _make_session(
        _boards_page([_board(1), _board(2)]),
        ({}, 403),
        _sprints_page([_sprint(20, "Good Sprint", origin_board_id=2)]),
    )
    result = get_jira_sprints(session)
    assert result == {"Good Sprint": 20}


# ---------------------------------------------------------------------------
# get_jira_sprints — deduplication
# ---------------------------------------------------------------------------

def test_deduplicates_sprints_across_boards(cache_path):
    # The same sprint ID appears on two boards; only one entry should result.
    session = _make_session(
        _boards_page([_board(1), _board(2)]),
        _sprints_page([_sprint(10, "Shared Sprint", origin_board_id=1)]),
        _sprints_page([_sprint(10, "Shared Sprint", origin_board_id=1)]),
    )
    result = get_jira_sprints(session)
    assert result == {"Shared Sprint": 10}


def test_origin_board_entry_wins(cache_path):
    # Board 1 sees sprint 10 first (not its origin); board 2 is the origin board
    # and should overwrite board 1's entry.
    session = _make_session(
        _boards_page([_board(1), _board(2)]),
        _sprints_page([_sprint(10, "Sprint (non-origin view)", origin_board_id=2)]),
        _sprints_page([_sprint(10, "Sprint (origin view)",     origin_board_id=2)]),
    )
    result = get_jira_sprints(session)
    assert "Sprint (origin view)" in result
    assert "Sprint (non-origin view)" not in result


# ---------------------------------------------------------------------------
# SprintHandler — detection
# ---------------------------------------------------------------------------

def test_sprint_handler_detects_gh_sprint_field():
    handler = SprintHandler({})
    meta = {"schema": {"type": "array", "items": "json",
                       "custom": "com.pyxis.greenhopper.jira:gh-sprint"}}
    assert handler.detect(meta) is True


def test_sprint_handler_does_not_detect_plain_array():
    handler = SprintHandler({})
    assert handler.detect({"schema": {"type": "array", "items": "string"}}) is False


def test_sprint_handler_does_not_detect_other_custom_field():
    handler = SprintHandler({})
    assert handler.detect({"schema": {"type": "string",
                                      "custom": "com.atlassian:textarea"}}) is False


# ---------------------------------------------------------------------------
# SprintHandler — matcher (fuzzy, case-insensitive)
# ---------------------------------------------------------------------------

def test_sprint_handler_matcher_is_fuzzy():
    handler = SprintHandler({"Sprint Alpha": 1})
    matcher = handler.matcher({})
    assert matcher is not None
    assert matcher.lookup("sprint alpha") == ["Sprint Alpha"]


def test_sprint_handler_matcher_case_insensitive():
    handler = SprintHandler({"Sprint Alpha": 1})
    matcher = handler.matcher({})
    assert matcher.lookup("SPRINT ALPHA") == ["Sprint Alpha"]


# ---------------------------------------------------------------------------
# SprintHandler — envelope
# ---------------------------------------------------------------------------

def test_sprint_handler_envelope_exact_key():
    handler = SprintHandler({"Sprint One": 10})
    assert handler.envelope("Sprint One", {}) == {"id": 10}


def test_sprint_handler_envelope_missing_raises():
    handler = SprintHandler({"Sprint One": 10})
    with pytest.raises(ValueError, match="not found"):
        handler.envelope("Nonexistent Sprint", {})


# ---------------------------------------------------------------------------
# SprintHandler — allowed
# ---------------------------------------------------------------------------

def test_sprint_handler_allowed_returns_sprint_sentinel():
    handler = SprintHandler({"Sprint One": 10})
    assert handler.allowed({}) == "SPRINT"
