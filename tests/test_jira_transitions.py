import pytest
from unittest.mock import MagicMock

from bug_filing.jira_transitions import get_transitions, transition_issue


def _transitions(*name_pairs):
    """Build a transitions list from (transition_name, to_status_name) pairs."""
    return [
        {"id": str(i), "name": tname, "to": {"name": sname}}
        for i, (tname, sname) in enumerate(name_pairs, start=1)
    ]


def _session(transitions, post_status=204):
    session = MagicMock()
    get_response = MagicMock(status_code=200)
    get_response.json.return_value = {"transitions": transitions}
    get_response.raise_for_status = MagicMock()
    session.get.return_value = get_response

    post_response = MagicMock(status_code=post_status)
    post_response.raise_for_status = MagicMock()
    session.post.return_value = post_response
    return session


# ---------------------------------------------------------------------------
# get_transitions
# ---------------------------------------------------------------------------

def test_get_transitions_returns_list():
    transitions = _transitions(("Start Progress", "In Progress"))
    session = _session(transitions)
    result = get_transitions("DOM-1", session)
    assert result == transitions


def test_get_transitions_calls_expected_url(monkeypatch):
    monkeypatch.setattr("bug_filing.jira_transitions.jira_base_url", lambda: "https://jira.example.com")
    session = _session(_transitions(("Start Progress", "In Progress")))
    get_transitions("DOM-1", session)
    session.get.assert_called_once_with("https://jira.example.com/rest/api/3/issue/DOM-1/transitions")


# ---------------------------------------------------------------------------
# transition_issue — success
# ---------------------------------------------------------------------------

def test_transition_issue_matches_destination_status(monkeypatch):
    monkeypatch.setattr("bug_filing.jira_transitions.jira_base_url", lambda: "https://jira.example.com")
    transitions = _transitions(("Start Progress", "In Progress"), ("Close", "Done"))
    session = _session(transitions)

    result = transition_issue("DOM-1", "In Progress", session=session)

    assert result["id"] == "1"
    session.post.assert_called_once_with(
        "https://jira.example.com/rest/api/3/issue/DOM-1/transitions",
        json={"transition": {"id": "1"}},
    )


def test_transition_issue_fuzzy_matches_status_name():
    transitions = _transitions(("Won't Fix", "Won't Do"), ("Reopen", "To Do"))
    session = _session(transitions)

    result = transition_issue("DOM-1", "wont do", session=session)

    assert result["to"]["name"] == "Won't Do"


# ---------------------------------------------------------------------------
# transition_issue — failure
# ---------------------------------------------------------------------------

def test_transition_issue_no_match_raises():
    transitions = _transitions(("Start Progress", "In Progress"))
    session = _session(transitions)

    with pytest.raises(ValueError, match="matched 0"):
        transition_issue("DOM-1", "Nonexistent Status", session=session)

    session.post.assert_not_called()


def test_transition_issue_ambiguous_match_raises():
    transitions = _transitions(("A", "Verification A"), ("B", "Verification B"))
    session = _session(transitions)

    with pytest.raises(ValueError, match="matched 2"):
        transition_issue("DOM-1", "Verification", session=session)

    session.post.assert_not_called()


def test_transition_issue_error_lists_available_statuses():
    transitions = _transitions(("Start Progress", "In Progress"), ("Close", "Done"))
    session = _session(transitions)

    with pytest.raises(ValueError, match="In Progress") as exc_info:
        transition_issue("DOM-1", "Nonexistent", session=session)
    assert "Done" in str(exc_info.value)


def test_transition_issue_no_transitions_available_raises():
    session = _session([])

    with pytest.raises(ValueError, match="none"):
        transition_issue("DOM-1", "In Progress", session=session)
