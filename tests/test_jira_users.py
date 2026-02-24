import json
from unittest.mock import MagicMock

import pytest

import bug_filing.jira_users as jira_users_module
from bug_filing.jira_users import get_jira_user_ids, UserHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _active_atlassian(display_name, account_id):
    return {
        "displayName": display_name,
        "accountId": account_id,
        "active": True,
        "accountType": "atlassian",
    }


def _make_session(*batches):
    """Return a mock session whose .request() yields each batch in turn."""
    session = MagicMock()
    session.request.side_effect = [
        MagicMock(text=json.dumps(b)) for b in batches
    ]
    return session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_memory_cache(monkeypatch):
    monkeypatch.setattr(jira_users_module, "_jira_user_ids_cache", None)


@pytest.fixture
def cache_path(tmp_path, monkeypatch):
    path = str(tmp_path / "jira_users_cache.json")
    monkeypatch.setattr(jira_users_module, "_CACHE_PATH", path)
    return path


# ---------------------------------------------------------------------------
# get_jira_user_ids — caching behaviour
# ---------------------------------------------------------------------------

def test_fetches_from_api_when_no_cache(cache_path):
    session = _make_session([_active_atlassian("Alice Smith", "aaa")])
    result = get_jira_user_ids(session)
    assert result == {"Alice Smith": "aaa"}
    session.request.assert_called_once()


def test_api_result_written_to_file(cache_path):
    session = _make_session([_active_atlassian("Alice Smith", "aaa")])
    get_jira_user_ids(session)
    with open(cache_path) as f:
        assert json.load(f) == {"Alice Smith": "aaa"}


def test_display_names_preserved_as_is(cache_path):
    session = _make_session([_active_atlassian("O'Brien, Pat!", "aaa")])
    result = get_jira_user_ids(session)
    assert "O'Brien, Pat!" in result


def test_file_cache_used_without_calling_api(cache_path):
    with open(cache_path, "w") as f:
        json.dump({"Bob Jones": "bbb"}, f)
    session = MagicMock()
    result = get_jira_user_ids(session)
    assert result == {"Bob Jones": "bbb"}
    session.request.assert_not_called()


def test_memory_cache_takes_priority_over_file(cache_path, monkeypatch):
    monkeypatch.setattr(jira_users_module, "_jira_user_ids_cache", {"Carol King": "ccc"})
    with open(cache_path, "w") as f:
        json.dump({"wrong user": "zzz"}, f)
    session = MagicMock()
    result = get_jira_user_ids(session)
    assert result == {"Carol King": "ccc"}
    session.request.assert_not_called()


def test_file_cache_populates_memory_cache(cache_path, monkeypatch):
    with open(cache_path, "w") as f:
        json.dump({"Bob Jones": "bbb"}, f)
    get_jira_user_ids(MagicMock())
    assert jira_users_module._jira_user_ids_cache == {"Bob Jones": "bbb"}


# ---------------------------------------------------------------------------
# UserHandler — detection
# ---------------------------------------------------------------------------

def test_user_handler_detects_user_field():
    handler = UserHandler({})
    assert handler.detect({"schema": {"type": "user"}}) is True


def test_user_handler_detects_array_of_user():
    handler = UserHandler({})
    assert handler.detect({"schema": {"type": "array", "items": "user"}}) is True


def test_user_handler_does_not_detect_string_field():
    handler = UserHandler({})
    assert handler.detect({"schema": {"type": "string"}}) is False


# ---------------------------------------------------------------------------
# UserHandler — matcher (fuzzy, case-insensitive)
# ---------------------------------------------------------------------------

def test_user_handler_matcher_is_fuzzy():
    handler = UserHandler({"Alice Smith": "aaa"})
    matcher = handler.matcher({})
    assert matcher is not None
    assert matcher.lookup("alice smith") == ["Alice Smith"]


def test_user_handler_matcher_case_insensitive(cache_path):
    handler = UserHandler({"Alice Smith": "aaa"})
    matcher = handler.matcher({})
    assert matcher.lookup("ALICE SMITH") == ["Alice Smith"]


# ---------------------------------------------------------------------------
# UserHandler — envelope
# ---------------------------------------------------------------------------

def test_user_handler_envelope_exact_key():
    handler = UserHandler({"Alice Smith": "aaa"})
    assert handler.envelope("Alice Smith", {}) == {"id": "aaa"}


def test_user_handler_envelope_missing_raises():
    handler = UserHandler({"Alice Smith": "aaa"})
    with pytest.raises(ValueError, match="not found"):
        handler.envelope("Nobody Real", {})


# ---------------------------------------------------------------------------
# UserHandler — allowed
# ---------------------------------------------------------------------------

def test_user_handler_allowed_returns_user_sentinel():
    handler = UserHandler({"Alice": "aaa"})
    assert handler.allowed({}) == "USER"
