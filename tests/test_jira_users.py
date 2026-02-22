import json
from unittest.mock import MagicMock

import pytest

import bug_filing.jira_users as jira_users_module
from bug_filing.jira_users import get_jira_user_ids


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
# Tests
# ---------------------------------------------------------------------------

def test_fetches_from_api_when_no_cache(cache_path):
    session = _make_session([_active_atlassian("Alice Smith", "aaa")])
    result = get_jira_user_ids(session)
    assert result == {"alice smith": "aaa"}
    session.request.assert_called_once()


def test_api_result_written_to_file(cache_path):
    session = _make_session([_active_atlassian("Alice Smith", "aaa")])
    get_jira_user_ids(session)
    with open(cache_path) as f:
        assert json.load(f) == {"alice smith": "aaa"}


def test_display_names_are_sanitized(cache_path):
    session = _make_session([_active_atlassian("O'Brien, Pat!", "aaa")])
    result = get_jira_user_ids(session)
    assert "obrien pat" in result


def test_file_cache_used_without_calling_api(cache_path):
    with open(cache_path, "w") as f:
        json.dump({"bob jones": "bbb"}, f)
    session = MagicMock()
    result = get_jira_user_ids(session)
    assert result == {"bob jones": "bbb"}
    session.request.assert_not_called()


def test_memory_cache_takes_priority_over_file(cache_path, monkeypatch):
    monkeypatch.setattr(jira_users_module, "_jira_user_ids_cache", {"carol king": "ccc"})
    with open(cache_path, "w") as f:
        json.dump({"wrong user": "zzz"}, f)
    session = MagicMock()
    result = get_jira_user_ids(session)
    assert result == {"carol king": "ccc"}
    session.request.assert_not_called()


def test_file_cache_populates_memory_cache(cache_path, monkeypatch):
    with open(cache_path, "w") as f:
        json.dump({"bob jones": "bbb"}, f)
    get_jira_user_ids(MagicMock())
    assert jira_users_module._jira_user_ids_cache == {"bob jones": "bbb"}


def test_envelope_user_is_case_insensitive(cache_path):
    from bug_filing.jira_users import make_user_envelope_fn
    session = _make_session([_active_atlassian("Alice Smith", "aaa")])
    fn = make_user_envelope_fn(session)
    assert fn("ALICE SMITH", None) == {"id": "aaa"}
    assert fn("alice smith", None) == {"id": "aaa"}
    assert fn("Alice Smith", None) == {"id": "aaa"}


def test_envelope_user_strips_special_chars(cache_path):
    from bug_filing.jira_users import make_user_envelope_fn
    session = _make_session([_active_atlassian("O'Brien Pat", "bbb")])
    fn = make_user_envelope_fn(session)
    assert fn("O'Brien Pat", None) == {"id": "bbb"}
