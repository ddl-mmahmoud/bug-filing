"""
Add a comment to an existing Jira issue.

The Jira Cloud REST API v3 stores comment bodies as ADF (Atlassian Document
Format), the same rich-text representation used for issue descriptions and
textarea custom fields.  Accordingly, `add_comment` converts the caller's
Markdown string to ADF and posts it as the comment body.

Reference
---------
POST /rest/api/3/issue/{issueIdOrKey}/comment
https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/
"""

from bug_filing.adf import from_markdown
from bug_filing.jira_session import jira_base_url, jira_requests_session


def add_comment(
    issue_key,
    markdown_text,
    username=None,
    password=None,
    session=None,
):
    """Add a comment to a Jira issue.

    Parameters
    ----------
    issue_key:
        The issue key or numeric ID, e.g. ``"DOM-123"``.
    markdown_text:
        Comment body as Markdown.  Converted to ADF before submission so
        the comment benefits from Jira's full rich-text rendering (bold,
        italic, code blocks, headings, lists, links, …).
    username:
        Atlassian account email to authenticate as.  Defaults to the
        ``JIRA_API_USERNAME`` environment variable.  Pass your own email
        and API token (``password``) to post the comment as yourself
        rather than the default bot account.
    password:
        Atlassian API token for ``username``.  Defaults to the
        ``JIRA_API_PASSWORD`` environment variable.
    session:
        An optional pre-configured ``requests.Session``.  When provided,
        ``username`` and ``password`` are ignored.

    Returns
    -------
    dict
        The parsed JSON response from Jira, which includes at minimum the
        new comment's ``id``, ``self`` (URL), ``body``, ``author``, and
        ``created`` fields.

    Raises
    ------
    requests.HTTPError
        If Jira returns a non-2xx status code.
    """
    if session is None:
        session = jira_requests_session(username=username, password=password)

    adf_body = from_markdown(markdown_text)
    url = f"{jira_base_url()}/rest/api/3/issue/{issue_key}/comment"
    response = session.post(url, json={"body": adf_body})
    response.raise_for_status()
    return response.json()
