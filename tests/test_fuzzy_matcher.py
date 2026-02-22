import pytest
from bug_filing.fuzzy_matcher import FuzzyMatcher


# ---------------------------------------------------------------------------
# sanitize
# ---------------------------------------------------------------------------

def test_sanitize_lowercases():
    assert FuzzyMatcher.sanitize("HELLO") == "hello"


def test_sanitize_strips_special_chars():
    assert FuzzyMatcher.sanitize("Hello, World!") == "hello world"


def test_sanitize_collapses_whitespace():
    assert FuzzyMatcher.sanitize("  foo   bar  ") == "foo bar"


def test_sanitize_strips_punctuation_mid_word():
    assert FuzzyMatcher.sanitize("it's") == "its"


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------

def test_tokenize_splits_on_whitespace():
    assert FuzzyMatcher._tokenize("Hello World") == ["hello", "world"]


def test_tokenize_strips_special_chars_before_splitting():
    # hyphen is stripped, leaving one token
    assert FuzzyMatcher._tokenize("foo-bar") == ["foobar"]


# ---------------------------------------------------------------------------
# lookup — zero / one match (no pruning path)
# ---------------------------------------------------------------------------

def test_empty_candidate_list():
    fm = FuzzyMatcher([])
    assert fm.lookup("anything") == []


def test_no_match_returns_empty():
    fm = FuzzyMatcher(["foo", "bar"])
    assert fm.lookup("xyz") == []


def test_single_exact_match():
    fm = FuzzyMatcher(["foo", "bar", "baz"])
    assert fm.lookup("foo") == ["foo"]


def test_single_candidate_matched():
    fm = FuzzyMatcher(["only one"])
    assert fm.lookup("one") == ["only one"]


# ---------------------------------------------------------------------------
# lookup — case insensitivity
# ---------------------------------------------------------------------------

def test_case_insensitive_query():
    fm = FuzzyMatcher(["Hello World", "Goodbye"])
    assert fm.lookup("HELLO") == ["Hello World"]


def test_case_insensitive_candidate():
    fm = FuzzyMatcher(["SUMMARY", "priority"])
    assert fm.lookup("summary") == ["SUMMARY"]


# ---------------------------------------------------------------------------
# lookup — token substring matching
# ---------------------------------------------------------------------------

def test_token_substring_match():
    # "iss" is a substring of the "issue" token
    fm = FuzzyMatcher(["Issue Type", "Priority"])
    assert fm.lookup("iss") == ["Issue Type"]


def test_query_special_chars_stripped_before_matching():
    fm = FuzzyMatcher(["foo bar"])
    assert fm.lookup("foo-bar") == ["foo bar"]


# ---------------------------------------------------------------------------
# lookup — collapsed-form matching
# ---------------------------------------------------------------------------

def test_collapsed_matches_multiword_field():
    # "issuetype" has no space; "Issue Type" collapsed is "issuetype"
    fm = FuzzyMatcher(["Issue Type", "Summary"])
    assert fm.lookup("issuetype") == ["Issue Type"]


def test_collapsed_substring_match():
    # "suetype" is a substring of collapsed "issuetype"
    fm = FuzzyMatcher(["Issue Type", "Summary"])
    assert fm.lookup("suetype") == ["Issue Type"]


# ---------------------------------------------------------------------------
# lookup — prefix pruning
# ---------------------------------------------------------------------------

def test_prefix_pruning_over_interior_substring():
    # "pre" starts "prefix" token; it is only an interior substring of "depress"
    fm = FuzzyMatcher(["prefix", "depress"])
    assert fm.lookup("pre") == ["prefix"]


# ---------------------------------------------------------------------------
# lookup — full-prefix pruning
# ---------------------------------------------------------------------------

def test_full_prefix_pruning_on_sanitized_form():
    # "alpha b" starts "alpha beta" but not "alpha gamma"
    fm = FuzzyMatcher(["alpha beta", "alpha gamma"])
    assert fm.lookup("alpha b") == ["alpha beta"]


def test_full_prefix_pruning_via_collapsed():
    # "alphab" is a prefix of "alphabeta" collapsed form but not "alphagamma"
    fm = FuzzyMatcher(["alpha beta", "alpha gamma"])
    assert fm.lookup("alphab") == ["alpha beta"]


# ---------------------------------------------------------------------------
# lookup — exact collapsed pruning
# ---------------------------------------------------------------------------

def test_exact_collapsed_beats_prefix_collapsed():
    # "dom" collapsed == "dom" exactly for "DOM"; "Domino" collapsed is "domino"
    fm = FuzzyMatcher(["DOM", "Domino"])
    assert fm.lookup("dom") == ["DOM"]


# ---------------------------------------------------------------------------
# lookup — ambiguous (multiple survivors after all pruning)
# ---------------------------------------------------------------------------

def test_ambiguous_returns_all_matches():
    fm = FuzzyMatcher(["alpha foo", "alpha bar"])
    result = fm.lookup("alpha")
    assert set(result) == {"alpha foo", "alpha bar"}


def test_ambiguous_does_not_include_non_matches():
    fm = FuzzyMatcher(["alpha foo", "alpha bar", "gamma"])
    result = fm.lookup("alpha")
    assert "gamma" not in result


# ---------------------------------------------------------------------------
# lookup — original insertion order preserved in output
# ---------------------------------------------------------------------------

def test_result_order_matches_original_insertion():
    strings = ["alpha foo", "alpha bar", "gamma"]
    fm = FuzzyMatcher(strings)
    result = fm.lookup("alpha")
    assert result == ["alpha foo", "alpha bar"]
