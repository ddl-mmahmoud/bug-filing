import re
import yaml
from bug_filing.jira_client import FuzzyMatcher


def _format_options(allowed_values):
    non_numeric = [v for v in allowed_values if not str(v).isdigit()]
    display = non_numeric if non_numeric else allowed_values
    truncated = display[:10]
    suffix = ", ..." if len(display) > 10 else ""
    return ", ".join(str(v) for v in truncated) + suffix


def _yaml_key(field_name):
    """Convert a human-readable field name to an underscore_case YAML key."""
    s = re.sub(r'[^a-zA-Z0-9\s]', '', field_name).lower()
    return re.sub(r'\s+', '_', s).strip('_')


def ticket_template(index, minimal=False):
    """
    Generate a YAML template string for a ticket, populated with all
    user-required fields.

    ADF fields use block scalar (|) format. Choice fields include a comment
    listing valid options. The YAML keys are underscore_case versions of the
    human-readable field names.

    If minimal=True, comments and inter-field blank lines are omitted.
    """
    lines = []
    for field_name in index.user_required:
        key = _yaml_key(field_name)
        field_type = index.types[field_name]
        tag = field_type[0]

        if not minimal:
            lines.append(f"# {field_name}")

        if tag == "adf":
            lines.append(f"{key}: |")
            lines.append(f"  Enter markdown content here")

        elif tag == "choice":
            if not minimal:
                av = index.allowed_values(field_name)
                lines.append(f"# options: {_format_options(av)}")
            lines.append(f"{key}:")

        elif tag == "array":
            if not minimal:
                inner = field_type[1]
                if inner[0] == "choice":
                    av = index.allowed_values(field_name)
                    lines.append(f"# options: {_format_options(av)}")
            lines.append(f"{key}:")
            lines.append(f"  -")

        else:  # string
            lines.append(f"{key}:")

        if not minimal:
            lines.append("")

    return "\n".join(lines)


def validate_ticket_yaml(index, yaml_string):
    """
    Validate a YAML ticket string against an IssueFieldIndex.

    Returns {"ok": True} if the YAML is valid and all required fields are
    present and unambiguous. Otherwise returns a dict with any of:
      - "parse_error":      string describing a YAML parse failure
      - "missing_fields":   list of required field names absent from the YAML
      - "ambiguous_fields": {yaml_key: [candidate field names]}
      - "ambiguous_values": {field_name: {raw_value: [candidate values]}}
    """
    try:
        data = yaml.safe_load(yaml_string)
    except yaml.YAMLError as e:
        return {"parse_error": str(e)}

    if not isinstance(data, dict):
        return {"parse_error": "Expected a YAML mapping at the top level"}

    field_matcher = FuzzyMatcher(index.allowed_fields())

    resolved = {}        # field_name -> raw value from YAML
    ambiguous_fields = {}

    for yaml_key, value in data.items():
        matches = field_matcher.lookup(str(yaml_key))
        if len(matches) == 1:
            resolved[matches[0]] = value
        elif len(matches) > 1:
            ambiguous_fields[yaml_key] = matches
        # len == 0: unrecognised field; silently ignored

    def _is_blank(value):
        return not value or value == [None]

    missing_fields = [f for f in index.user_required if _is_blank(resolved.get(f))]

    ambiguous_values = {}
    for field_name, value in resolved.items():
        if _is_blank(value):
            continue
        matcher = index.value_matcher(field_name)
        if matcher is None:
            continue
        for v in (value if isinstance(value, list) else [value]):
            matches = matcher.lookup(str(v))
            if len(matches) != 1:
                canonicals = {index._canonical_value(field_name, m) for m in matches}
                if len(canonicals) != 1:
                    ambiguous_values.setdefault(field_name, {})[str(v)] = matches

    if not (missing_fields or ambiguous_fields or ambiguous_values):
        return {"ok": True}

    errors = {"ok": False}
    if missing_fields:
        errors["missing_fields"] = missing_fields
    if ambiguous_fields:
        errors["ambiguous_fields"] = ambiguous_fields
    if ambiguous_values:
        errors["ambiguous_values"] = ambiguous_values
    return errors


def build_ticket_payload(index, yaml_string):
    """
    Build a Jira issue create payload dict from a YAML ticket string.

    Raises ValueError if any field or value cannot be resolved unambiguously.
    The returned dict has the shape {"fields": {...}} ready for the Jira API.
    """
    data = yaml.safe_load(yaml_string)
    payload = index.fuzzy_payload(data)
    for field_name, enveloped_value in index.unambiguous.items():
        payload["fields"].setdefault(index.name_to_key[field_name], enveloped_value)
    return payload
