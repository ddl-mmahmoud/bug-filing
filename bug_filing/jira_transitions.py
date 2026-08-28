"""
Transition a Jira issue to a different status after creation.

Jira's create-issue API has no "status" field — a new issue always starts
at its workflow's initial status (see references/dom-bug-createmeta.json,
which lists no status field at all). Reaching any other status ("In
Progress", "Won't Do", "No Repro", ...) means calling the separate
transitions endpoint, and which transitions are legal depends on the
issue's *current* status in its workflow.

Reference
---------
GET  /rest/api/3/issue/{issueIdOrKey}/transitions
POST /rest/api/3/issue/{issueIdOrKey}/transitions
https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-transitions/
"""

from bug_filing.fuzzy_matcher import FuzzyMatcher
from bug_filing.jira_session import jira_base_url, jira_requests_session


def get_transitions(issue_key, session):
    """Return the list of transitions currently available for issue_key."""
    url = f"{jira_base_url()}/rest/api/3/issue/{issue_key}/transitions"
    response = session.get(url)
    response.raise_for_status()
    return response.json()["transitions"]


def transition_issue(issue_key, status_name, username=None, password=None, session=None):
    """Move an issue to the named status.

    Parameters
    ----------
    issue_key:
        The issue key or numeric ID, e.g. ``"DOM-123"``.
    status_name:
        The desired destination status, e.g. ``"In Progress"`` or
        ``"Won't Do"``. Fuzzy-matched against the destination status of
        each transition currently available for `issue_key` (each
        transition's ``to.name``), since that's how a caller thinks of the
        change ("move it to Verification") rather than the workflow's
        action name for getting there ("Send for Verification").
    username, password:
        Atlassian account email / API token. Defaults to the
        ``JIRA_API_USERNAME`` / ``JIRA_API_PASSWORD`` environment variables.
        Ignored if `session` is provided.
    session:
        An optional pre-configured ``requests.Session``.

    Returns
    -------
    dict
        The transition that was applied (id, name, and destination status).

    Raises
    ------
    ValueError
        If `status_name` doesn't match exactly one of the transitions
        currently available for `issue_key`.
    requests.HTTPError
        If Jira returns a non-2xx status code.
    """
    if session is None:
        session = jira_requests_session(username=username, password=password)

    transitions = get_transitions(issue_key, session)
    by_status = {t["to"]["name"]: t for t in transitions}
    matches = FuzzyMatcher(list(by_status.keys())).lookup(status_name)
    if len(matches) != 1:
        available = ", ".join(sorted(by_status.keys())) or "(none)"
        raise ValueError(
            f"Status {status_name!r} matched {len(matches)} of the transitions "
            f"available for {issue_key} (available: {available}): {matches}"
        )

    transition = by_status[matches[0]]
    url = f"{jira_base_url()}/rest/api/3/issue/{issue_key}/transitions"
    response = session.post(url, json={"transition": {"id": transition["id"]}})
    response.raise_for_status()
    return transition
