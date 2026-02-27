import pytest

from bug_filing.templating import hydrate, load_variables, required_variables


# ---------------------------------------------------------------------------
# hydrate
# ---------------------------------------------------------------------------

def test_hydrate_single_variable():
    assert hydrate("summary: {{ title }}", {"title": "My Bug"}) == "summary: My Bug"


def test_hydrate_multiple_variables():
    result = hydrate(
        "summary: {{ title }}\nreporter: {{ author }}",
        {"title": "Crash", "author": "Alice"},
    )
    assert result == "summary: Crash\nreporter: Alice"


def test_hydrate_nested_variable():
    result = hydrate("components: [{{ team.component }}]", {"team": {"component": "Auth"}})
    assert result == "components: [Auth]"


def test_hydrate_unused_variables_are_ignored():
    # Extra keys in the variables dict should not cause an error.
    assert hydrate("summary: {{ title }}", {"title": "X", "extra": "ignored"}) == "summary: X"


def test_hydrate_missing_variable_is_empty():
    assert hydrate("summary: {{ missing }}", {}) == "summary: "


def test_hydrate_missing_dotted_variable_is_empty():
    assert hydrate("components: {{ team.component }}", {}) == "components: "


def test_hydrate_preserves_trailing_newline():
    assert hydrate("summary: {{ title }}\n", {"title": "X"}) == "summary: X\n"


def test_hydrate_no_placeholders_returns_unchanged():
    text = "summary:\nreporter: Alice\n"
    assert hydrate(text, {}) == text


# ---------------------------------------------------------------------------
# required_variables
# ---------------------------------------------------------------------------

def test_required_variables_single():
    assert required_variables("summary: {{ title }}") == {"title": None}


def test_required_variables_multiple():
    assert required_variables("{{ a }}\n{{ b }}\n{{ c }}") == {"a": None, "b": None, "c": None}


def test_required_variables_no_placeholders():
    assert required_variables("summary:\nreporter: Alice\n") == {}


def test_required_variables_dotted_access():
    assert required_variables("components: [{{ team.component }}]") == {
        "team": {"component": None}
    }


def test_required_variables_multiple_dotted_accesses_same_root():
    assert required_variables("{{ team.component }}\n{{ team.label }}") == {
        "team": {"component": None, "label": None}
    }


def test_required_variables_mixed_flat_and_dotted():
    result = required_variables("{{ summary }}\n{{ team.component }}")
    assert result == {"summary": None, "team": {"component": None}}


def test_required_variables_deep_chain_suppresses_prefixes():
    # {{ a.b.c }} generates Getattr nodes for a.b and a.b.c; only the
    # deepest path should appear in the output.
    assert required_variables("{{ a.b.c }}") == {"a": {"b": {"c": None}}}


def test_required_variables_template_defined_names_excluded():
    # Variables introduced by a {% set %} block are not requirements.
    assert required_variables("{% set x = 1 %}{{ x }}") == {}


# ---------------------------------------------------------------------------
# load_variables
# ---------------------------------------------------------------------------

def test_load_variables_flat(tmp_path):
    f = tmp_path / "vars.yaml"
    f.write_text("title: My Bug\nauthor: Alice\n")
    assert load_variables(str(f)) == {"title": "My Bug", "author": "Alice"}


def test_load_variables_nested(tmp_path):
    f = tmp_path / "vars.yaml"
    f.write_text("team:\n  component: Auth\n  label: identity\n")
    assert load_variables(str(f)) == {"team": {"component": "Auth", "label": "identity"}}


def test_load_variables_non_mapping_raises(tmp_path):
    f = tmp_path / "vars.yaml"
    f.write_text("- item1\n- item2\n")
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_variables(str(f))


def test_load_variables_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_variables(str(tmp_path / "nonexistent.yaml"))
