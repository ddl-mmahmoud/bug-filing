"""Tests for ticket_cli multi-document submit and related helpers."""

import argparse
import io
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bug_filing.ticket_cli import (
    _cmd_submit,
    _format_validation_comments,
    _format_stash,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _args(yaml_text, *, project="DOM", issuetype="Bug", dry_run=False, invalid_stash=None):
    return argparse.Namespace(
        project=project,
        issuetype=issuetype,
        dry_run=dry_run,
        invalid_stash=invalid_stash,
        infile=io.StringIO(yaml_text),
    )


def _mock_post(keys):
    """Return a side_effect list of POST responses for the given issue keys."""
    responses = []
    for key in keys:
        r = MagicMock(status_code=201)
        r.json.return_value = {"key": key}
        responses.append(r)
    return responses


# Patch targets (functions imported into ticket_cli's namespace)
_MAKE_INDEX     = "bug_filing.ticket_cli._make_index"
_VALIDATE       = "bug_filing.ticket_cli.validate_ticket_yaml"
_BUILD_PAYLOAD  = "bug_filing.ticket_cli.build_ticket_payload"
_JIRA_BASE_URL  = "bug_filing.ticket_cli.jira_base_url"

DUMMY_PAYLOAD = {"fields": {"summary": "test"}}
DUMMY_BASE    = "https://jira.example.com"
VALID         = {"ok": True}


# ---------------------------------------------------------------------------
# _format_validation_comments
# ---------------------------------------------------------------------------

def test_format_comments_missing_fields():
    result = {"ok": False, "missing_fields": ["Summary", "Priority"]}
    out = _format_validation_comments(result)
    assert "# Validation errors:" in out
    assert "missing_fields: Summary, Priority" in out


def test_format_comments_parse_error():
    result = {"ok": False, "parse_error": "line 1\nline 2"}
    out = _format_validation_comments(result)
    assert "#   line 1" in out
    assert "#   line 2" in out


def test_format_comments_unknown_fields():
    result = {"ok": False, "unknown_fields": ["bad_key", "another"]}
    out = _format_validation_comments(result)
    assert "unknown_fields: bad_key, another" in out


def test_format_comments_ambiguous_fields():
    result = {"ok": False, "ambiguous_fields": {"proj": ["Project", "Project Category"]}}
    out = _format_validation_comments(result)
    assert "ambiguous_fields:" in out
    assert "proj:" in out


def test_format_comments_ambiguous_values():
    result = {"ok": False, "ambiguous_values": {"Priority": {"hi": ["High", "Higher"]}}}
    out = _format_validation_comments(result)
    assert "ambiguous_values:" in out
    assert "Priority:" in out
    assert "'hi'" in out


def test_format_comments_invalid_values():
    result = {"ok": False, "invalid_values": {"Assignee": "user not found"}}
    out = _format_validation_comments(result)
    assert "invalid_values:" in out
    assert "Assignee: user not found" in out


def test_format_comments_all_lines_are_comments():
    result = {"ok": False, "missing_fields": ["Summary"], "unknown_fields": ["x"]}
    for line in _format_validation_comments(result).splitlines():
        assert line.startswith("#"), f"Non-comment line: {line!r}"


# ---------------------------------------------------------------------------
# _format_stash
# ---------------------------------------------------------------------------

def test_format_stash_contains_doc_separator():
    invalid = [("project: DOM\n", None, None, {"ok": False, "missing_fields": ["Summary"]})]
    out = _format_stash(invalid)
    assert "---" in out


def test_format_stash_contains_doc_content():
    invalid = [("project: DOM\n", None, None, {"ok": False, "missing_fields": ["Summary"]})]
    out = _format_stash(invalid)
    assert "project: DOM" in out


def test_format_stash_two_docs_two_separators():
    invalid = [
        ("a: 1\n", None, None, {"ok": False, "missing_fields": ["Summary"]}),
        ("b: 2\n", None, None, {"ok": False, "unknown_fields": ["x"]}),
    ]
    out = _format_stash(invalid)
    assert out.count("---") == 2
    assert "a: 1" in out
    assert "b: 2" in out


def test_format_stash_is_parseable_yaml():
    """The stash output, split on ---, should be parseable YAML for each doc."""
    import yaml
    from bug_filing.ticket_yaml import split_yaml_documents
    invalid = [
        ("project: DOM\nsummary: hello\n", None, None, {"ok": False, "missing_fields": ["Priority"]}),
    ]
    out = _format_stash(invalid)
    docs = split_yaml_documents(out)
    assert len(docs) == 1
    parsed = yaml.safe_load(docs[0])
    assert parsed["project"] == "DOM"


# ---------------------------------------------------------------------------
# _cmd_submit — single-document path (regression)
# ---------------------------------------------------------------------------

@patch(_JIRA_BASE_URL, return_value=DUMMY_BASE)
@patch(_BUILD_PAYLOAD, return_value=DUMMY_PAYLOAD)
@patch(_VALIDATE, return_value=VALID)
@patch(_MAKE_INDEX)
def test_submit_single_valid(mock_make_index, mock_validate, mock_build, mock_base, capsys):
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=201, **{"json.return_value": {"key": "DOM-1"}})
    mock_make_index.return_value = (session, MagicMock())

    _cmd_submit(_args("project: DOM\nissuetype: Bug\nsummary: Hi\n"))

    assert "DOM-1" in capsys.readouterr().out


@patch(_JIRA_BASE_URL, return_value=DUMMY_BASE)
@patch(_VALIDATE, return_value={"ok": False, "missing_fields": ["Summary"]})
@patch(_MAKE_INDEX)
def test_submit_single_invalid_raises(mock_make_index, mock_validate, mock_base, capsys):
    mock_make_index.return_value = (MagicMock(), MagicMock())

    with pytest.raises(ValueError, match="failed validation"):
        _cmd_submit(_args("project: DOM\nissuetype: Bug\n"))

    assert "missing_fields" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _cmd_submit — multi-document, all valid
# ---------------------------------------------------------------------------

@patch(_JIRA_BASE_URL, return_value=DUMMY_BASE)
@patch(_BUILD_PAYLOAD, return_value=DUMMY_PAYLOAD)
@patch(_VALIDATE, return_value=VALID)
@patch(_MAKE_INDEX)
def test_submit_multi_all_valid(mock_make_index, mock_validate, mock_build, mock_base, capsys):
    session = MagicMock()
    session.post.side_effect = _mock_post(["DOM-1", "DOM-2"])
    mock_make_index.return_value = (session, MagicMock())

    yaml_text = "project: DOM\nissuetype: Bug\nsummary: A\n---\nproject: DOM\nissuetype: Bug\nsummary: B\n"
    _cmd_submit(_args(yaml_text))

    out = capsys.readouterr().out
    assert "DOM-1" in out
    assert "DOM-2" in out


# ---------------------------------------------------------------------------
# _cmd_submit — multi-document, some invalid, no stash
# ---------------------------------------------------------------------------

@patch(_JIRA_BASE_URL, return_value=DUMMY_BASE)
@patch(_BUILD_PAYLOAD, return_value=DUMMY_PAYLOAD)
@patch(_MAKE_INDEX)
def test_submit_multi_any_invalid_no_stash_raises(mock_make_index, mock_build, mock_base, capsys):
    mock_make_index.return_value = (MagicMock(), MagicMock())

    results = [VALID, {"ok": False, "missing_fields": ["Summary"]}]
    with patch(_VALIDATE, side_effect=results):
        with pytest.raises(ValueError, match="Batch validation failed"):
            _cmd_submit(_args(
                "project: DOM\nissuetype: Bug\nsummary: A\n"
                "---\n"
                "project: DOM\nissuetype: Bug\n"
            ))

    err = capsys.readouterr().err
    # stderr should be a JSON list
    parsed = json.loads(err)
    assert isinstance(parsed, list)
    assert len(parsed) == 2


@patch(_JIRA_BASE_URL, return_value=DUMMY_BASE)
@patch(_MAKE_INDEX)
def test_submit_multi_invalid_no_stash_nothing_submitted(mock_make_index, mock_base):
    session = MagicMock()
    mock_make_index.return_value = (session, MagicMock())

    with patch(_VALIDATE, return_value={"ok": False, "missing_fields": ["Summary"]}):
        with pytest.raises(ValueError):
            _cmd_submit(_args(
                "project: DOM\nissuetype: Bug\n"
                "---\n"
                "project: DOM\nissuetype: Bug\n"
            ))

    session.post.assert_not_called()


# ---------------------------------------------------------------------------
# _cmd_submit — multi-document, with --invalid-stash
# ---------------------------------------------------------------------------

@patch(_JIRA_BASE_URL, return_value=DUMMY_BASE)
@patch(_BUILD_PAYLOAD, return_value=DUMMY_PAYLOAD)
@patch(_MAKE_INDEX)
def test_submit_multi_stash_submits_valid_saves_invalid(mock_make_index, mock_build, mock_base, tmp_path, capsys):
    session = MagicMock()
    session.post.side_effect = _mock_post(["DOM-1"])
    mock_make_index.return_value = (session, MagicMock())

    stash = tmp_path / "stash.yaml"
    results = [VALID, {"ok": False, "missing_fields": ["Summary"]}]
    with patch(_VALIDATE, side_effect=results):
        _cmd_submit(_args(
            "project: DOM\nissuetype: Bug\nsummary: A\n"
            "---\n"
            "project: DOM\nissuetype: Bug\n",
            invalid_stash=str(stash),
        ))

    out, err = capsys.readouterr()
    assert "DOM-1" in out
    assert "1 valid" in err
    assert "1 invalid" in err
    assert str(stash) in err
    assert stash.exists()


@patch(_JIRA_BASE_URL, return_value=DUMMY_BASE)
@patch(_MAKE_INDEX)
def test_submit_multi_stash_content_has_comments_and_yaml(mock_make_index, mock_base, tmp_path):
    mock_make_index.return_value = (MagicMock(), MagicMock())

    stash = tmp_path / "stash.yaml"
    results = [VALID, {"ok": False, "missing_fields": ["Summary"]}]
    with patch(_VALIDATE, side_effect=results):
        with patch(_BUILD_PAYLOAD, return_value=DUMMY_PAYLOAD):
            session = MagicMock()
            session.post.side_effect = _mock_post(["DOM-1"])
            mock_make_index.return_value = (session, MagicMock())
            _cmd_submit(_args(
                "project: DOM\nissuetype: Bug\nsummary: A\n"
                "---\n"
                "project: DOM\nissuetype: Bug\n",
                invalid_stash=str(stash),
            ))

    content = stash.read_text()
    assert "# Validation errors:" in content
    assert "missing_fields" in content
    assert "project: DOM" in content


@patch(_JIRA_BASE_URL, return_value=DUMMY_BASE)
@patch(_MAKE_INDEX)
def test_submit_multi_all_invalid_with_stash_zero_submitted(mock_make_index, mock_base, tmp_path, capsys):
    session = MagicMock()
    mock_make_index.return_value = (session, MagicMock())

    stash = tmp_path / "stash.yaml"
    with patch(_VALIDATE, return_value={"ok": False, "missing_fields": ["Summary"]}):
        _cmd_submit(_args(
            "project: DOM\nissuetype: Bug\n"
            "---\n"
            "project: DOM\nissuetype: Bug\n",
            invalid_stash=str(stash),
        ))

    session.post.assert_not_called()
    err = capsys.readouterr().err
    assert "0 valid" in err
    assert "2 invalid" in err


@patch(_JIRA_BASE_URL, return_value=DUMMY_BASE)
@patch(_BUILD_PAYLOAD, return_value=DUMMY_PAYLOAD)
@patch(_MAKE_INDEX)
def test_submit_multi_all_valid_stash_not_written(mock_make_index, mock_build, mock_base, tmp_path):
    session = MagicMock()
    session.post.side_effect = _mock_post(["DOM-1", "DOM-2"])
    mock_make_index.return_value = (session, MagicMock())

    stash = tmp_path / "stash.yaml"
    with patch(_VALIDATE, return_value=VALID):
        _cmd_submit(_args(
            "project: DOM\nissuetype: Bug\nsummary: A\n"
            "---\n"
            "project: DOM\nissuetype: Bug\nsummary: B\n",
            invalid_stash=str(stash),
        ))

    assert not stash.exists()


# ---------------------------------------------------------------------------
# _cmd_submit — dry-run with multi-document
# ---------------------------------------------------------------------------

@patch(_JIRA_BASE_URL, return_value=DUMMY_BASE)
@patch(_BUILD_PAYLOAD, return_value=DUMMY_PAYLOAD)
@patch(_VALIDATE, return_value=VALID)
@patch(_MAKE_INDEX)
def test_submit_multi_dry_run_prints_payloads(mock_make_index, mock_validate, mock_build, mock_base, capsys):
    session = MagicMock()
    mock_make_index.return_value = (session, MagicMock())

    _cmd_submit(_args(
        "project: DOM\nissuetype: Bug\nsummary: A\n"
        "---\n"
        "project: DOM\nissuetype: Bug\nsummary: B\n",
        dry_run=True,
    ))

    session.post.assert_not_called()
    out = capsys.readouterr().out
    # Two JSON payloads should appear
    assert out.count('"fields"') == 2


# ---------------------------------------------------------------------------
# _cmd_submit — missing project/issuetype in one document
# ---------------------------------------------------------------------------

@patch(_JIRA_BASE_URL, return_value=DUMMY_BASE)
@patch(_BUILD_PAYLOAD, return_value=DUMMY_PAYLOAD)
@patch(_MAKE_INDEX)
def test_submit_multi_missing_project_treated_as_invalid(mock_make_index, mock_build, mock_base, tmp_path, capsys):
    session = MagicMock()
    session.post.side_effect = _mock_post(["DOM-1"])
    mock_make_index.return_value = (session, MagicMock())

    stash = tmp_path / "stash.yaml"
    # First doc has no project/issuetype and none on CLI — treated as invalid.
    # Second doc supplies its own project/issuetype and is valid.
    with patch(_VALIDATE, return_value=VALID):
        _cmd_submit(_args(
            "summary: No metadata here\n"
            "---\n"
            "project: DOM\nissuetype: Bug\nsummary: B\n",
            project=None,
            issuetype=None,
            invalid_stash=str(stash),
        ))

    assert stash.exists()
    content = stash.read_text()
    assert "parse_error" in content or "required" in content
