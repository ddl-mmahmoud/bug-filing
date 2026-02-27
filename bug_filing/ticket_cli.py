#!/usr/bin/env python3
"""
CLI for YAML-based Jira ticket authoring.

Subcommands
-----------
template   Emit a YAML template for the given project / issue type on STDOUT.
validate   Read a YAML ticket from STDIN and report any errors as JSON.
submit     Validate a YAML ticket from STDIN and file it as a Jira issue.
           Use --dry-run to emit the JSON payload instead of submitting.
hydrate    Interpolate variables from a YAML file into a template on STDIN
           and emit the result on STDOUT.

Required environment variables
-------------------------------
  JIRA_URL
  JIRA_API_USERNAME
  JIRA_API_PASSWORD

Example usage
-------------
  ticket-yaml template --project DOM --issuetype Bug
  ticket-yaml validate --project DOM --issuetype Bug < my-ticket.yaml
  ticket-yaml submit   --project DOM --issuetype Bug < my-ticket.yaml
  ticket-yaml submit   --project DOM --issuetype Bug --dry-run < my-ticket.yaml
  ticket-yaml hydrate  --variables vars.yaml < my-template.yaml
"""

import argparse
import glob
import json
import os
import sys

import yaml

from bug_filing.issue_field_index import IssueFieldIndex
from bug_filing.jira_session import jira_base_url, jira_requests_session
from bug_filing.jira_users import get_jira_user_ids, UserHandler
from bug_filing.jira_sprints import get_jira_sprints, SprintHandler
from bug_filing.ticket_yaml import (
    build_ticket_payload,
    split_yaml_documents,
    ticket_template,
    validate_ticket_yaml,
)
from bug_filing.templating import hydrate, load_variables, required_variables


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="ticket-yaml",
        description="YAML-based Jira ticket authoring tool.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ------------------------------------------------------------------ #
    # Access overrides (fall back to environment variables)              #
    # ------------------------------------------------------------------ #
    creds = parser.add_argument_group(
        "access overrides",
        "Each of these overrides the corresponding environment variable. "
        "If neither is set the tool will exit with an error.",
    )
    creds.add_argument(
        "--jira-url",
        default=None,
        metavar="URL",
        help="Overrides JIRA_URL",
    )
    creds.add_argument(
        "--jira-username",
        default=None,
        metavar="EMAIL",
        help="Overrides JIRA_API_USERNAME.",
    )
    creds.add_argument(
        "--jira-password",
        default=None,
        metavar="TOKEN",
        help="Overrides JIRA_API_PASSWORD.",
    )

    sub = parser.add_subparsers(dest="subcommand", required=True)

    # ------------------------------------------------------------------ #
    # Shared arguments                                                     #
    # ------------------------------------------------------------------ #
    def add_common(p, required=True):
        p.add_argument("--project", required=required, default=None, metavar="KEY",
                       help="Jira project key (e.g. DOM). For validate/submit, may be read from the YAML instead.")
        p.add_argument("--issuetype", required=required, default=None, metavar="NAME",
                       help="Issue type name (e.g. Bug). For validate/submit, may be read from the YAML instead.")

    def add_infile(p):
        p.add_argument(
            "infile", nargs="?", type=argparse.FileType("r"), default=sys.stdin,
            metavar="FILE", help="Input file (default: stdin).",
        )

    # ------------------------------------------------------------------ #
    # template                                                             #
    # ------------------------------------------------------------------ #
    p_template = sub.add_parser(
        "template",
        help="Emit a YAML template for the given project and issue type.",
    )
    add_common(p_template, required=True)
    verbosity = p_template.add_mutually_exclusive_group()
    verbosity.add_argument(
        "--minimal", action="store_true", default=False,
        help="Omit comments and blank lines from the template.",
    )
    verbosity.add_argument(
        "--maximal", action="store_true", default=False,
        help="Include all fields, not just required ones.",
    )

    # ------------------------------------------------------------------ #
    # validate                                                             #
    # ------------------------------------------------------------------ #
    p_validate = sub.add_parser(
        "validate",
        help="Validate a YAML ticket read from STDIN.",
    )
    add_common(p_validate, required=False)
    add_infile(p_validate)

    # ------------------------------------------------------------------ #
    # submit                                                               #
    # ------------------------------------------------------------------ #
    p_submit = sub.add_parser(
        "submit",
        help="Validate and file a YAML ticket from STDIN as a Jira issue.",
    )
    add_common(p_submit, required=False)
    p_submit.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Emit the JSON payload instead of submitting to Jira.",
    )
    p_submit.add_argument(
        "--invalid-stash", metavar="FILEPATH", default=None,
        help="Write invalid documents (with commented validation errors) to FILEPATH "
             "as a multi-document YAML; valid documents are submitted as normal.",
    )
    add_infile(p_submit)

    # ------------------------------------------------------------------ #
    # hydrate                                                              #
    # ------------------------------------------------------------------ #
    p_hydrate = sub.add_parser(
        "hydrate",
        help="Interpolate variables into a YAML template read from STDIN.",
    )
    mode = p_hydrate.add_mutually_exclusive_group(required=False)
    mode.add_argument(
        "--variables", "--vars", metavar="FILE",
        help="Path to a YAML file whose values are interpolated into the template.",
    )
    mode.add_argument(
        "--requirements", action="store_true", default=False,
        help="Emit a stub YAML listing the variables the template requires.",
    )
    mode.add_argument(
        "--list", action="store_true", default=False,
        help="List available templates in the tpl/ directory.",
    )
    mode.add_argument(
        "--template", metavar="FILE",
        help="Read the template from FILE and variables from STDIN.",
    )
    p_hydrate.add_argument(
        "--absolute", action="store_true", default=False,
        help="With --list, print absolute paths instead of tpl/-relative paths.",
    )
    add_infile(p_hydrate)

    return parser


def _apply_access_overrides(args):
    """Copy access CLI args into environment variables."""
    overrides = {
        "jira_url":      "JIRA_URL",
        "jira_username": "JIRA_API_USERNAME",
        "jira_password": "JIRA_API_PASSWORD",
    }
    for attr, env_var in overrides.items():
        value = getattr(args, attr, None)
        if value is not None:
            os.environ[env_var] = value


def _check_required_env_vars():
    """Verify that the mandatory Jira environment variables are present."""
    required = ["JIRA_URL", "JIRA_API_USERNAME", "JIRA_API_PASSWORD"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise RuntimeError(
            "the following required access vars are not set "
            "(provide them as environment variables or CLI arguments):\n"
            + "\n".join(f"  {v}" for v in missing)
        )


def _extract_yaml_defaults(yaml_text, args):
    """
    If --project or --issuetype were omitted, attempt to read them from the
    YAML text.  The expected YAML keys are 'project' (the project key, e.g.
    'DOM') and 'issue_type' or 'issuetype' (the issue type name, e.g. 'Bug').
    """
    if args.project is not None and args.issuetype is not None:
        return
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return
    if not isinstance(data, dict):
        return
    if args.project is None:
        args.project = data.get("project")
    if args.issuetype is None:
        args.issuetype = data.get("issue_type") or data.get("issuetype")


def _require_project_and_issuetype(args):
    """Raise a clear error if project or issuetype are still unset."""
    missing = []
    if not args.project:
        missing.append("--project (or 'project' key in YAML)")
    if not args.issuetype:
        missing.append("--issuetype (or 'issue_type' key in YAML)")
    if missing:
        raise RuntimeError(
            "the following required arguments are missing:\n"
            + "\n".join(f"  {m}" for m in missing)
        )


def _make_index(args):
    session = jira_requests_session()
    user_ids = get_jira_user_ids(session)
    sprints = get_jira_sprints(session)
    type_handlers = [UserHandler(user_ids), SprintHandler(sprints)]
    index = IssueFieldIndex(session, args.project, args.issuetype,
                            type_handlers=type_handlers)
    return session, index


def _cmd_template(args):
    _, index = _make_index(args)
    print(ticket_template(index, minimal=args.minimal, maximal=args.maximal), end="")


def _cmd_validate(args):
    yaml_text = args.infile.read()
    _extract_yaml_defaults(yaml_text, args)
    _require_project_and_issuetype(args)
    _, index = _make_index(args)
    result = validate_ticket_yaml(index, yaml_text)
    print(json.dumps(result, indent=2))
    if result != {"ok": True}:
        raise RuntimeError(result)


def _format_validation_comments(result):
    """Convert a validation error dict into a block of YAML comment lines."""
    lines = ["# Validation errors:"]
    if "parse_error" in result:
        for line in result["parse_error"].splitlines():
            lines.append(f"#   {line}")
    if "missing_fields" in result:
        lines.append(f"#   missing_fields: {', '.join(result['missing_fields'])}")
    if "unknown_fields" in result:
        lines.append(f"#   unknown_fields: {', '.join(str(f) for f in result['unknown_fields'])}")
    if "ambiguous_fields" in result:
        lines.append("#   ambiguous_fields:")
        for key, candidates in result["ambiguous_fields"].items():
            lines.append(f"#     {key}: {candidates}")
    if "ambiguous_values" in result:
        lines.append("#   ambiguous_values:")
        for field, vals in result["ambiguous_values"].items():
            for raw, matches in vals.items():
                lines.append(f"#     {field}: {raw!r} matches {matches}")
    if "invalid_values" in result:
        lines.append("#   invalid_values:")
        for field, err in result["invalid_values"].items():
            lines.append(f"#     {field}: {err}")
    return "\n".join(lines)


def _format_stash(invalid_docs):
    """Render invalid documents with validation error comments as a multi-doc YAML string."""
    parts = []
    for doc_text, _, _, result in invalid_docs:
        comments = _format_validation_comments(result)
        parts.append("---\n" + comments + "\n" + doc_text.strip("\n"))
    return "\n".join(parts) + "\n"


def _submit_one(session, index, doc_text, dry_run):
    """Validate-then-submit (or dry-run) a single document. Returns the created URL or None."""
    payload = build_ticket_payload(index, doc_text)
    if dry_run:
        print(json.dumps(payload, indent=2))
        return None
    response = session.post(f"{jira_base_url()}/rest/api/3/issue", json=payload)
    if response.status_code == 201:
        key = response.json()["key"]
        url = f"{jira_base_url()}/browse/{key}"
        print(f"Created: {url}")
        return url
    raise RuntimeError(f"Jira API error {response.status_code}: {response.text}")


def _cmd_submit(args):
    yaml_text = args.infile.read()
    docs = split_yaml_documents(yaml_text)

    if len(docs) == 1:
        # Single-document path — original behaviour.
        _extract_yaml_defaults(yaml_text, args)
        _require_project_and_issuetype(args)
        session, index = _make_index(args)

        result = validate_ticket_yaml(index, yaml_text)
        if result != {"ok": True}:
            print(json.dumps(result, indent=2), file=sys.stderr)
            raise ValueError("Ticket YAML failed validation")

        _submit_one(session, index, yaml_text, args.dry_run)
        return

    # Multi-document path.
    index_cache = {}   # (project, issuetype) -> (session, index)
    docs_info = []     # (doc_text, session|None, index|None, result)

    for doc_text in docs:
        doc_args = argparse.Namespace(project=args.project, issuetype=args.issuetype)
        _extract_yaml_defaults(doc_text, doc_args)
        try:
            _require_project_and_issuetype(doc_args)
        except RuntimeError as e:
            docs_info.append((doc_text, None, None, {"ok": False, "parse_error": str(e)}))
            continue

        cache_key = (doc_args.project, doc_args.issuetype)
        if cache_key not in index_cache:
            index_cache[cache_key] = _make_index(doc_args)
        session, index = index_cache[cache_key]

        result = validate_ticket_yaml(index, doc_text)
        docs_info.append((doc_text, session, index, result))

    valid   = [(t, s, i, r) for t, s, i, r in docs_info if r == {"ok": True}]
    invalid = [(t, s, i, r) for t, s, i, r in docs_info if r != {"ok": True}]

    if invalid and not args.invalid_stash:
        all_results = [r for _, _, _, r in docs_info]
        print(json.dumps(all_results, indent=2), file=sys.stderr)
        raise ValueError(
            f"Batch validation failed: {len(invalid)} of {len(docs_info)} documents invalid"
        )

    for doc_text, session, index, _ in valid:
        _submit_one(session, index, doc_text, args.dry_run)

    if invalid:
        with open(args.invalid_stash, "w") as f:
            f.write(_format_stash(invalid))
        print(
            f"{len(valid)} valid ticket(s) submitted, "
            f"{len(invalid)} invalid ticket(s) saved to {args.invalid_stash}",
            file=sys.stderr,
        )


_DEFAULT_VARIABLES_FILE = "default_variables.yaml"
_TEMPLATES_DIR = "tpl"


def _cmd_hydrate(args):
    if args.list:
        paths = sorted(glob.glob(os.path.join(_TEMPLATES_DIR, "**", "*"), recursive=True))
        paths = [p for p in paths if os.path.isfile(p)]
        for p in paths:
            print(os.path.abspath(p) if args.absolute else p)
        return

    if args.template:
        with open(args.template) as f:
            template_text = f.read()
        variables = {}
        if os.path.exists(_DEFAULT_VARIABLES_FILE):
            variables = load_variables(_DEFAULT_VARIABLES_FILE)
        stdin_vars = yaml.safe_load(args.infile.read()) or {}
        if not isinstance(stdin_vars, dict):
            raise ValueError("Variables on stdin must be a YAML mapping")
        variables = {**variables, **stdin_vars}
        print(hydrate(template_text, variables), end="")
        return

    template_text = args.infile.read()
    if args.requirements:
        stub = required_variables(template_text)
        print(yaml.dump(stub, default_flow_style=False), end="")
    else:
        variables = {}
        if os.path.exists(_DEFAULT_VARIABLES_FILE):
            variables = load_variables(_DEFAULT_VARIABLES_FILE)
        if args.variables:
            variables = {**variables, **load_variables(args.variables)}
        print(hydrate(template_text, variables), end="")


_COMMANDS = {
    "template": _cmd_template,
    "validate": _cmd_validate,
    "submit":   _cmd_submit,
    "hydrate":  _cmd_hydrate,
}

# Subcommands that do not require a Jira connection.
_NO_JIRA_COMMANDS = {"hydrate"}


def main():
    parser = _build_parser()
    args = parser.parse_args()
    try:
        _apply_access_overrides(args)
        if args.subcommand not in _NO_JIRA_COMMANDS:
            _check_required_env_vars()
        _COMMANDS[args.subcommand](args)
        return 0

    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    main()
