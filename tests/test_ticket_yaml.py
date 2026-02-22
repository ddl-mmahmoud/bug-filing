import pytest
from unittest.mock import MagicMock

from bug_filing.fuzzy_matcher import FuzzyMatcher
from bug_filing.ticket_yaml import (
    _format_options,
    _yaml_key,
    ticket_template,
    validate_ticket_yaml,
    build_ticket_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_index(
    *,
    allowed_fields=None,
    user_required=None,
    types=None,
    allowed_values_map=None,
    value_matcher_map=None,
    canonical_fn=None,
    unambiguous=None,
    name_to_key=None,
    fuzzy_payload_result=None,
):
    """Build a mock IssueFieldIndex with controllable behaviour."""
    idx = MagicMock()
    idx.allowed_fields.return_value = list(allowed_fields or [])
    idx.user_required = list(user_required or [])
    idx.types = dict(types or {})
    idx.unambiguous = dict(unambiguous or {})
    idx.name_to_key = dict(name_to_key or {})

    if allowed_values_map is not None:
        idx.allowed_values.side_effect = lambda n: allowed_values_map.get(n, "SCALAR")
    else:
        idx.allowed_values.return_value = "SCALAR"

    if value_matcher_map is not None:
        idx.value_matcher.side_effect = lambda n: value_matcher_map.get(n)
    else:
        idx.value_matcher.return_value = None

    if canonical_fn is not None:
        idx._canonical_value.side_effect = canonical_fn
    else:
        # Default: identity — the match string is its own canonical
        idx._canonical_value.side_effect = lambda field, val: val

    if fuzzy_payload_result is not None:
        idx.fuzzy_payload.return_value = fuzzy_payload_result

    return idx


# ---------------------------------------------------------------------------
# _format_options
# ---------------------------------------------------------------------------

def test_format_options_filters_numeric_when_non_numeric_present():
    result = _format_options(["1", "High", "2", "Medium"])
    assert "High" in result and "Medium" in result
    assert "1" not in result and "2" not in result


def test_format_options_keeps_all_when_all_numeric():
    result = _format_options(["1", "2", "3"])
    assert "1" in result and "2" in result and "3" in result


def test_format_options_truncates_at_10_with_ellipsis():
    values = [f"opt{i}" for i in range(15)]
    result = _format_options(values)
    assert "..." in result
    assert "opt10" not in result  # 11th element not shown


def test_format_options_no_ellipsis_when_10_or_fewer():
    result = _format_options([f"opt{i}" for i in range(10)])
    assert "..." not in result


def test_format_options_empty_list():
    assert _format_options([]) == ""


def test_format_options_exactly_10_no_ellipsis():
    result = _format_options([f"x{i}" for i in range(10)])
    assert "..." not in result


# ---------------------------------------------------------------------------
# _yaml_key
# ---------------------------------------------------------------------------

def test_yaml_key_lowercases():
    assert _yaml_key("Summary") == "summary"


def test_yaml_key_spaces_become_underscores():
    assert _yaml_key("Issue Type") == "issue_type"


def test_yaml_key_strips_special_chars():
    assert _yaml_key("Eng. Team") == "eng_team"


def test_yaml_key_strips_leading_trailing_underscores():
    # Leading/trailing spaces produce underscores that get stripped
    assert _yaml_key("  Field  ") == "field"


def test_yaml_key_multiple_spaces_collapse():
    assert _yaml_key("foo  bar") == "foo_bar"


# ---------------------------------------------------------------------------
# ticket_template — field selection
# ---------------------------------------------------------------------------

def test_template_default_uses_user_required():
    idx = _make_index(
        user_required=["Priority"],
        types={"Priority": ("string",)},
    )
    output = ticket_template(idx)
    assert "priority:" in output
    # allowed_fields not called in default mode
    idx.allowed_fields.assert_not_called()


def test_template_maximal_uses_allowed_fields():
    idx = _make_index(
        allowed_fields=["Summary", "Notes"],
        types={"Summary": ("string",), "Notes": ("string",)},
    )
    output = ticket_template(idx, maximal=True)
    assert "summary:" in output and "notes:" in output


def test_template_summary_sorted_first_in_default():
    idx = _make_index(
        user_required=["Priority", "Summary"],
        types={"Priority": ("string",), "Summary": ("string",)},
    )
    output = ticket_template(idx)
    assert output.index("summary:") < output.index("priority:")


def test_template_summary_sorted_first_in_maximal():
    idx = _make_index(
        allowed_fields=["Priority", "Summary"],
        types={"Priority": ("string",), "Summary": ("string",)},
    )
    output = ticket_template(idx, maximal=True)
    assert output.index("summary:") < output.index("priority:")


# ---------------------------------------------------------------------------
# ticket_template — field types
# ---------------------------------------------------------------------------

def test_template_adf_field():
    idx = _make_index(
        user_required=["Description"],
        types={"Description": ("adf",)},
    )
    output = ticket_template(idx)
    assert "description: |" in output
    assert "Enter markdown content here" in output


def test_template_string_field():
    idx = _make_index(
        user_required=["Summary"],
        types={"Summary": ("string",)},
    )
    output = ticket_template(idx)
    assert "summary:" in output


def test_template_choice_field_with_options_comment():
    idx = _make_index(
        user_required=["Priority"],
        types={"Priority": ("choice", ["name"])},
        allowed_values_map={"Priority": ["High", "Low"]},
    )
    output = ticket_template(idx)
    assert "# options:" in output
    assert "High" in output


def test_template_array_choice_field_with_options_comment():
    idx = _make_index(
        user_required=["Components"],
        types={"Components": ("array", ("choice", ["name"]))},
        allowed_values_map={"Components": ["Frontend", "Backend"]},
    )
    output = ticket_template(idx)
    assert "# options:" in output
    assert "  -" in output


def test_template_array_non_choice_no_options_comment():
    idx = _make_index(
        user_required=["Labels"],
        types={"Labels": ("array", ("string",))},
    )
    output = ticket_template(idx)
    assert "labels:" in output
    assert "  -" in output
    assert "# options:" not in output


# ---------------------------------------------------------------------------
# ticket_template — minimal flag
# ---------------------------------------------------------------------------

def test_template_minimal_omits_comments():
    idx = _make_index(
        user_required=["Priority"],
        types={"Priority": ("choice", ["name"])},
        allowed_values_map={"Priority": ["High", "Low"]},
    )
    output = ticket_template(idx, minimal=True)
    assert "#" not in output


def test_template_minimal_omits_blank_lines():
    idx = _make_index(
        user_required=["Summary", "Priority"],
        types={"Summary": ("string",), "Priority": ("string",)},
    )
    output = ticket_template(idx, minimal=True)
    assert "\n\n" not in output


def test_template_non_minimal_includes_blank_lines():
    # A blank separator line appears between fields only when there are 2+ fields
    idx = _make_index(
        user_required=["Summary", "Priority"],
        types={"Summary": ("string",), "Priority": ("string",)},
    )
    output = ticket_template(idx, minimal=False)
    assert "\n\n" in output


def test_template_minimal_array_choice_no_options():
    idx = _make_index(
        user_required=["Components"],
        types={"Components": ("array", ("choice", ["name"]))},
        allowed_values_map={"Components": ["Frontend"]},
    )
    output = ticket_template(idx, minimal=True)
    assert "# options:" not in output
    assert "  -" in output


# ---------------------------------------------------------------------------
# validate_ticket_yaml — parse errors
# ---------------------------------------------------------------------------

def test_validate_yaml_parse_error():
    idx = _make_index(allowed_fields=["Summary"])
    result = validate_ticket_yaml(idx, "{invalid yaml: [")
    assert "parse_error" in result


def test_validate_non_mapping_yaml():
    idx = _make_index(allowed_fields=["Summary"])
    result = validate_ticket_yaml(idx, "- item1\n- item2")
    assert result.get("parse_error") == "Expected a YAML mapping at the top level"


# ---------------------------------------------------------------------------
# validate_ticket_yaml — valid input
# ---------------------------------------------------------------------------

def test_validate_valid_yaml_returns_ok():
    idx = _make_index(
        allowed_fields=["Summary"],
        user_required=["Summary"],
        value_matcher_map={"Summary": None},
    )
    result = validate_ticket_yaml(idx, "summary: Hello world")
    assert result == {"ok": True}


def test_validate_extra_unrecognised_fields_ignored():
    idx = _make_index(
        allowed_fields=["Summary"],
        user_required=["Summary"],
    )
    result = validate_ticket_yaml(idx, "summary: Hello\nunknown_field: ignored")
    assert result == {"ok": True}


# ---------------------------------------------------------------------------
# validate_ticket_yaml — missing fields
# ---------------------------------------------------------------------------

def test_validate_missing_required_field():
    idx = _make_index(
        allowed_fields=["Summary", "Priority"],
        user_required=["Summary"],
    )
    result = validate_ticket_yaml(idx, "priority: High")
    assert result["ok"] is False
    assert "Summary" in result["missing_fields"]


def test_validate_none_value_treated_as_missing():
    idx = _make_index(
        allowed_fields=["Summary"],
        user_required=["Summary"],
    )
    result = validate_ticket_yaml(idx, "summary:")
    assert "Summary" in result.get("missing_fields", [])


def test_validate_list_with_none_treated_as_missing():
    # YAML "summary:\n  -" parses as {"summary": [None]}
    idx = _make_index(
        allowed_fields=["Summary"],
        user_required=["Summary"],
    )
    result = validate_ticket_yaml(idx, "summary:\n  -")
    assert "Summary" in result.get("missing_fields", [])


# ---------------------------------------------------------------------------
# validate_ticket_yaml — ambiguous fields
# ---------------------------------------------------------------------------

def test_validate_ambiguous_field_name():
    # Two fields share a token so the yaml key matches both
    idx = _make_index(
        allowed_fields=["foo alpha", "foo beta"],
        user_required=[],
    )
    result = validate_ticket_yaml(idx, "foo: value")
    assert result["ok"] is False
    assert "ambiguous_fields" in result


# ---------------------------------------------------------------------------
# validate_ticket_yaml — value validation
# ---------------------------------------------------------------------------

def test_validate_scalar_field_skips_value_check():
    # value_matcher returns None → value is accepted as-is
    idx = _make_index(
        allowed_fields=["Summary"],
        user_required=["Summary"],
        value_matcher_map={"Summary": None},
    )
    result = validate_ticket_yaml(idx, "summary: anything")
    assert result == {"ok": True}


def test_validate_choice_field_unambiguous_value():
    matcher = FuzzyMatcher(["High", "Low"])
    idx = _make_index(
        allowed_fields=["Priority"],
        user_required=["Priority"],
        value_matcher_map={"Priority": matcher},
    )
    result = validate_ticket_yaml(idx, "priority: High")
    assert result == {"ok": True}


def test_validate_list_value_checked_per_element():
    matcher = FuzzyMatcher(["Frontend", "Backend"])
    idx = _make_index(
        allowed_fields=["Components"],
        user_required=["Components"],
        value_matcher_map={"Components": matcher},
    )
    result = validate_ticket_yaml(idx, "components:\n  - Frontend\n  - Backend")
    assert result == {"ok": True}


def test_validate_ambiguous_value_different_canonicals():
    # "hi" matches "High" and "Higher" — two different entries
    matcher = FuzzyMatcher(["High", "Higher"])
    idx = _make_index(
        allowed_fields=["Priority"],
        user_required=[],
        value_matcher_map={"Priority": matcher},
        # identity canonical: each match is its own canonical
        canonical_fn=lambda field, val: val,
    )
    result = validate_ticket_yaml(idx, "priority: hi")
    assert result["ok"] is False
    assert "ambiguous_values" in result


def test_validate_ambiguous_value_same_canonical_is_ok():
    # "d" matches "DOM" and "Domino", but canonical maps both to "DOM"
    matcher = FuzzyMatcher(["DOM", "Domino"])
    idx = _make_index(
        allowed_fields=["Project"],
        user_required=[],
        value_matcher_map={"Project": matcher},
        canonical_fn=lambda field, val: "DOM",   # both resolve to same
    )
    result = validate_ticket_yaml(idx, "project: d")
    assert result == {"ok": True}


def test_validate_value_matches_nothing_is_ambiguous():
    # A matcher that never matches (empty list) triggers the != 1 branch
    matcher = FuzzyMatcher([])   # no candidates → lookup always returns []
    idx = _make_index(
        allowed_fields=["Priority"],
        user_required=[],
        value_matcher_map={"Priority": matcher},
    )
    result = validate_ticket_yaml(idx, "priority: nonexistent")
    assert result["ok"] is False
    assert "ambiguous_values" in result


# ---------------------------------------------------------------------------
# validate_ticket_yaml — combined errors
# ---------------------------------------------------------------------------

def test_validate_returns_all_error_categories():
    matcher = FuzzyMatcher(["High", "Higher"])
    idx = _make_index(
        allowed_fields=["Summary", "foo alpha", "foo beta", "Priority"],
        user_required=["Summary"],
        value_matcher_map={"Priority": matcher},
        canonical_fn=lambda field, val: val,
    )
    yaml_text = (
        "foo: bar\n"          # ambiguous field
        "priority: hi\n"      # ambiguous value
        # Summary missing
    )
    result = validate_ticket_yaml(idx, yaml_text)
    assert result["ok"] is False
    assert "missing_fields" in result
    assert "ambiguous_fields" in result
    assert "ambiguous_values" in result


# ---------------------------------------------------------------------------
# build_ticket_payload
# ---------------------------------------------------------------------------

def test_build_payload_calls_fuzzy_payload():
    idx = _make_index(
        fuzzy_payload_result={"fields": {"summary": "Hello"}},
        unambiguous={},
        name_to_key={},
    )
    payload = build_ticket_payload(idx, "summary: Hello")
    assert payload == {"fields": {"summary": "Hello"}}


def test_build_payload_merges_unambiguous_fields():
    idx = _make_index(
        fuzzy_payload_result={"fields": {}},
        unambiguous={"Issue Type": {"name": "Bug"}},
        name_to_key={"Issue Type": "issuetype"},
    )
    payload = build_ticket_payload(idx, "{}")
    assert payload["fields"]["issuetype"] == {"name": "Bug"}


def test_build_payload_setdefault_does_not_overwrite():
    # If fuzzy_payload already set a field, unambiguous must not overwrite it
    idx = _make_index(
        fuzzy_payload_result={"fields": {"issuetype": {"name": "Story"}}},
        unambiguous={"Issue Type": {"name": "Bug"}},
        name_to_key={"Issue Type": "issuetype"},
    )
    payload = build_ticket_payload(idx, "{}")
    assert payload["fields"]["issuetype"] == {"name": "Story"}
