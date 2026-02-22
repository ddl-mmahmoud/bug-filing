import pytest
from unittest.mock import MagicMock

from bug_filing.issue_field_index import IssueFieldIndex


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(data):
    """Return a mock requests.Session whose .get() returns data as JSON."""
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    session = MagicMock()
    session.get.return_value = resp
    return session


def _createmeta(fields):
    return {"projects": [{"issuetypes": [{"fields": fields}]}]}


def _str_field(name, *, required=False, system=None, custom=None):
    schema = {"type": "string"}
    if system:
        schema["system"] = system
    if custom:
        schema["custom"] = custom
    return {"name": name, "required": required, "schema": schema}


def _choice_field(name, allowed, *, required=False):
    return {
        "name": name,
        "required": required,
        "schema": {"type": "string"},
        "allowedValues": allowed,
    }


def _array_field(name, *, allowed=None, required=False):
    f = {"name": name, "required": required, "schema": {"type": "array"}}
    if allowed is not None:
        f["allowedValues"] = allowed
    return f


def _user_field(name, *, required=False):
    return {"name": name, "required": required, "schema": {"type": "user"}}


def _array_user_field(name, *, required=False):
    return {"name": name, "required": required, "schema": {"type": "array", "items": "user"}}


def _make_index(fields):
    return IssueFieldIndex(_make_session(_createmeta(fields)), "P", "T")


# ---------------------------------------------------------------------------
# __init__ — error paths
# ---------------------------------------------------------------------------

def test_project_not_found():
    session = _make_session({"projects": []})
    with pytest.raises(ValueError, match="not found"):
        IssueFieldIndex(session, "MISSING", "Bug")


def test_issuetype_not_found():
    session = _make_session({"projects": [{"issuetypes": []}]})
    with pytest.raises(ValueError, match="not found"):
        IssueFieldIndex(session, "DOM", "Ghost")


def test_init_populates_name_to_key_and_required():
    fields = {
        "summary": _str_field("Summary", required=True),
        "desc":    _str_field("Description", required=False),
    }
    idx = _make_index(fields)
    assert idx.name_to_key["Summary"] == "summary"
    assert idx.name_to_key["Description"] == "desc"
    assert "Summary" in idx.required
    assert "Description" not in idx.required


# ---------------------------------------------------------------------------
# _item_type / _field_type → types property
# ---------------------------------------------------------------------------

def test_types_plain_string():
    idx = _make_index({"f": _str_field("My Field")})
    assert idx.types["My Field"] == ("string",)


def test_types_adf_by_system_description():
    idx = _make_index({"f": _str_field("Description", system="description")})
    assert idx.types["Description"] == ("adf",)


def test_types_adf_by_system_environment():
    idx = _make_index({"f": _str_field("Environment", system="environment")})
    assert idx.types["Environment"] == ("adf",)


def test_types_adf_by_custom_textarea():
    idx = _make_index({"f": _str_field("Notes", custom="com.atlassian:textarea")})
    assert idx.types["Notes"] == ("adf",)


def test_types_choice():
    av = [{"id": "1", "name": "High"}]
    idx = _make_index({"f": _choice_field("Priority", av)})
    tag, keys = idx.types["Priority"]
    assert tag == "choice"
    assert "name" in keys and "id" in keys


def test_types_array_of_choice():
    av = [{"id": "1", "name": "Frontend"}]
    idx = _make_index({"f": _array_field("Components", allowed=av)})
    tag, inner = idx.types["Components"]
    assert tag == "array"
    assert inner[0] == "choice"


def test_types_array_of_string():
    idx = _make_index({"f": _array_field("Labels")})
    assert idx.types["Labels"] == ("array", ("string",))


def test_types_user():
    idx = _make_index({"f": _user_field("Assignee")})
    assert idx.types["Assignee"] == ("user",)


def test_types_array_of_user():
    idx = _make_index({"f": _array_user_field("Watchers")})
    assert idx.types["Watchers"] == ("array", ("user",))


def test_types_is_cached():
    idx = _make_index({"f": _str_field("F")})
    assert idx.types is idx.types


# ---------------------------------------------------------------------------
# unambiguous property
# ---------------------------------------------------------------------------

def test_unambiguous_single_allowed_value():
    av = [{"name": "Bug"}]
    fields = {"it": _choice_field("Issue Type", av, required=True)}
    idx = _make_index(fields)
    assert "Issue Type" in idx.unambiguous


def test_unambiguous_uses_first_identifier_key():
    # value > key > name > id — "name" is first present here
    av = [{"name": "Bug", "id": "10001"}]
    fields = {"it": _choice_field("Issue Type", av, required=True)}
    idx = _make_index(fields)
    assert idx.unambiguous["Issue Type"] == {"name": "Bug"}


def test_unambiguous_excluded_when_multiple_allowed():
    av = [{"name": "High"}, {"name": "Low"}]
    fields = {"p": _choice_field("Priority", av, required=True)}
    idx = _make_index(fields)
    assert "Priority" not in idx.unambiguous


def test_unambiguous_excluded_when_not_required():
    av = [{"name": "Bug"}]
    fields = {"it": _choice_field("Type", av, required=False)}
    idx = _make_index(fields)
    assert "Type" not in idx.unambiguous


def test_unambiguous_is_cached():
    idx = _make_index({"f": _str_field("F")})
    assert idx.unambiguous is idx.unambiguous


# ---------------------------------------------------------------------------
# user_required
# ---------------------------------------------------------------------------

def test_user_required_excludes_unambiguous():
    av_single = [{"name": "Bug"}]
    av_multi  = [{"name": "High"}, {"name": "Low"}]
    fields = {
        "it":  _choice_field("Issue Type", av_single, required=True),
        "pri": _choice_field("Priority",   av_multi,  required=True),
        "sum": _str_field("Summary", required=True),
    }
    idx = _make_index(fields)
    ur = idx.user_required
    assert "Issue Type" not in ur   # unambiguous
    assert "Priority"   in ur
    assert "Summary"    in ur


# ---------------------------------------------------------------------------
# allowed_fields
# ---------------------------------------------------------------------------

def test_allowed_fields_returns_all_names():
    fields = {"a": _str_field("Alpha"), "b": _str_field("Beta")}
    idx = _make_index(fields)
    assert set(idx.allowed_fields()) == {"Alpha", "Beta"}


# ---------------------------------------------------------------------------
# allowed_values / _allowed_for_type
# ---------------------------------------------------------------------------

def test_allowed_values_string_returns_scalar():
    idx = _make_index({"s": _str_field("Summary")})
    assert idx.allowed_values("Summary") == "SCALAR"


def test_allowed_values_adf_returns_adf():
    idx = _make_index({"d": _str_field("Description", system="description")})
    assert idx.allowed_values("Description") == "ADF"


def test_allowed_values_choice_returns_list():
    av = [{"id": "1", "name": "High"}, {"id": "2", "name": "Low"}]
    idx = _make_index({"p": _choice_field("Priority", av)})
    vals = idx.allowed_values("Priority")
    assert isinstance(vals, list)
    assert "High" in vals and "Low" in vals


def test_allowed_values_array_choice_returns_list():
    av = [{"id": "1", "name": "Frontend"}]
    idx = _make_index({"c": _array_field("Components", allowed=av)})
    vals = idx.allowed_values("Components")
    assert isinstance(vals, list)


def test_allowed_values_array_string_returns_scalar():
    idx = _make_index({"l": _array_field("Labels")})
    assert idx.allowed_values("Labels") == "SCALAR"


def test_allowed_values_user_returns_user():
    idx = _make_index({"a": _user_field("Assignee")})
    assert idx.allowed_values("Assignee") == "USER"


def test_allowed_for_type_unknown_tag_raises():
    idx = _make_index({"f": _str_field("F")})
    with pytest.raises(ValueError, match="Unknown"):
        idx._allowed_for_type({}, ("unknown_tag",))


# ---------------------------------------------------------------------------
# value_matcher
# ---------------------------------------------------------------------------

def test_value_matcher_none_for_scalar_field():
    idx = _make_index({"s": _str_field("Summary")})
    assert idx.value_matcher("Summary") is None


def test_value_matcher_returns_fuzzy_matcher_for_choice():
    av = [{"name": "High"}]
    idx = _make_index({"p": _choice_field("Priority", av)})
    matcher = idx.value_matcher("Priority")
    assert matcher is not None


def test_value_matcher_is_cached():
    av = [{"name": "High"}]
    idx = _make_index({"p": _choice_field("Priority", av)})
    assert idx.value_matcher("Priority") is idx.value_matcher("Priority")


# ---------------------------------------------------------------------------
# _enveloped
# ---------------------------------------------------------------------------

def test_enveloped_string_plain_value():
    idx = _make_index({"f": _str_field("F")})
    assert idx._enveloped("hello", ("string",)) == "hello"


def test_enveloped_string_rejects_dict():
    idx = _make_index({"f": _str_field("F")})
    with pytest.raises(ValueError, match="plain string"):
        idx._enveloped({"k": "v"}, ("string",))


def test_enveloped_choice_wraps_in_first_key():
    idx = _make_index({"f": _str_field("F")})
    assert idx._enveloped("High", ("choice", ["name", "id"])) == {"name": "High"}


def test_enveloped_choice_rejects_dict():
    idx = _make_index({"f": _str_field("F")})
    with pytest.raises(ValueError, match="scalar"):
        idx._enveloped({"name": "High"}, ("choice", ["name", "id"]))


def test_enveloped_adf_passthrough_dict():
    idx = _make_index({"f": _str_field("F")})
    doc = {"version": 1, "type": "doc", "content": []}
    assert idx._enveloped(doc, ("adf",)) == doc


def test_enveloped_adf_string_renders_markdown():
    idx = _make_index({"f": _str_field("F")})
    result = idx._enveloped("**bold**", ("adf",))
    assert result["type"] == "doc"
    assert result["version"] == 1


def test_enveloped_adf_rejects_non_string_non_dict():
    idx = _make_index({"f": _str_field("F")})
    with pytest.raises(ValueError, match="ADF"):
        idx._enveloped(42, ("adf",))


def test_enveloped_array_wraps_each_element():
    idx = _make_index({"f": _str_field("F")})
    result = idx._enveloped(["a", "b"], ("array", ("string",)))
    assert result == ["a", "b"]


def test_enveloped_array_requires_list():
    idx = _make_index({"f": _str_field("F")})
    with pytest.raises(ValueError, match="list"):
        idx._enveloped("not a list", ("array", ("string",)))


def test_enveloped_list_for_non_array_raises():
    idx = _make_index({"f": _str_field("F")})
    with pytest.raises(ValueError):
        idx._enveloped(["a", "b"], ("string",))


def test_enveloped_unknown_tag_raises():
    idx = _make_index({"f": _str_field("F")})
    with pytest.raises(ValueError, match="Unknown"):
        idx._enveloped("v", ("bogus",))


def test_enveloped_user_returns_id_dict():
    user_ids = {"Alice Example": "abc123"}
    envelope_fns = {"user": lambda v, ft: {"id": user_ids[v]}}
    idx = IssueFieldIndex(
        _make_session(_createmeta({"a": _user_field("Assignee")})),
        "P", "T",
        envelope_fns=envelope_fns,
    )
    assert idx._enveloped("Alice Example", ("user",)) == {"id": "abc123"}


def test_enveloped_user_missing_raises():
    def envelope_user(value, field_type):
        raise ValueError(f"User {value!r} not found")
    idx = IssueFieldIndex(
        _make_session(_createmeta({"a": _user_field("Assignee")})),
        "P", "T",
        envelope_fns={"user": envelope_user},
    )
    with pytest.raises(ValueError, match="not found"):
        idx._enveloped("Nobody Real", ("user",))


# ---------------------------------------------------------------------------
# field
# ---------------------------------------------------------------------------

def test_field_returns_api_key_and_enveloped_value():
    idx = _make_index({"summary": _str_field("Summary")})
    key, val = idx.field("Summary", "My title")
    assert key == "summary"
    assert val == "My title"


# ---------------------------------------------------------------------------
# fuzzy_field
# ---------------------------------------------------------------------------

def test_fuzzy_field_unrecognised_name_raises():
    idx = _make_index({"summary": _str_field("Summary")})
    with pytest.raises(ValueError, match="matched 0"):
        idx.fuzzy_field("nonexistent", "value")


def test_fuzzy_field_ambiguous_name_raises():
    fields = {"f1": _str_field("foo alpha"), "f2": _str_field("foo beta")}
    idx = _make_index(fields)
    with pytest.raises(ValueError, match="matched 2"):
        idx.fuzzy_field("foo", "value")


def test_fuzzy_field_scalar_field():
    idx = _make_index({"summary": _str_field("Summary")})
    key, val = idx.fuzzy_field("summary", "Hello")
    assert key == "summary" and val == "Hello"


def test_fuzzy_field_choice_value():
    av = [{"name": "High"}, {"name": "Low"}]
    idx = _make_index({"pri": _choice_field("Priority", av)})
    key, val = idx.fuzzy_field("priority", "High")
    assert key == "pri" and val == {"name": "High"}


def test_fuzzy_field_list_value():
    av = [{"name": "Frontend"}, {"name": "Backend"}]
    idx = _make_index({"comp": _array_field("Components", allowed=av)})
    key, val = idx.fuzzy_field("components", ["Frontend"])
    assert key == "comp"
    assert isinstance(val, list)


def test_fuzzy_field_ambiguous_value_same_canonical_resolves():
    # key="DOM" and name="Domino" come from the same entry; a short query
    # matches both, but they canonicalize to the same preferred identifier.
    av = [{"key": "DOM", "name": "Domino"}]
    idx = _make_index({"proj": _choice_field("Project", av)})
    key, val = idx.fuzzy_field("project", "d")   # "d" matches DOM and Domino
    assert key == "proj"
    assert val == {"key": "DOM"}


def test_fuzzy_field_ambiguous_value_different_canonicals_raises():
    av = [{"id": "1", "name": "High"}, {"id": "2", "name": "Higher"}]
    idx = _make_index({"pri": _choice_field("Priority", av)})
    with pytest.raises(ValueError, match="matched 2"):
        idx.fuzzy_field("priority", "hi")   # matches "High" and "Higher"


# ---------------------------------------------------------------------------
# fuzzy_payload
# ---------------------------------------------------------------------------

def test_fuzzy_payload_builds_fields_dict():
    idx = _make_index({"summary": _str_field("Summary")})
    payload = idx.fuzzy_payload({"summary": "Hello"})
    assert payload == {"fields": {"summary": "Hello"}}


# ---------------------------------------------------------------------------
# _canonical_value
# ---------------------------------------------------------------------------

def test_canonical_value_choice_returns_first_identifier():
    av = [{"name": "High", "id": "1"}]
    idx = _make_index({"p": _choice_field("Priority", av)})
    # _IDENTIFIER_KEYS order: value, key, name, id — "name" is first present
    assert idx._canonical_value("Priority", "High") == "High"


def test_canonical_value_returns_input_when_not_found():
    av = [{"name": "High"}]
    idx = _make_index({"p": _choice_field("Priority", av)})
    assert idx._canonical_value("Priority", "unknown") == "unknown"


def test_canonical_value_string_field_returns_value_unchanged():
    # string field → id_keys is None → pass-through
    idx = _make_index({"s": _str_field("Summary")})
    assert idx._canonical_value("Summary", "anything") == "anything"


def test_canonical_value_array_field():
    av = [{"name": "Frontend", "id": "1"}]
    idx = _make_index({"c": _array_field("Components", allowed=av)})
    assert idx._canonical_value("Components", "Frontend") == "Frontend"
