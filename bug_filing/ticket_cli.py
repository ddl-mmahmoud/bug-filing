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
import json
import os
import sys

import yaml

from bug_filing.issue_field_index import IssueFieldIndex
from bug_filing.jira_session import jira_base_url, jira_requests_session
from bug_filing.jira_users import get_jira_user_ids, UserHandler
from bug_filing.jira_sprints import get_jira_sprints, SprintHandler
from bug_filing.ticket_yaml import build_ticket_payload, ticket_template, validate_ticket_yaml
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

    # ------------------------------------------------------------------ #
    # hydrate                                                              #
    # ------------------------------------------------------------------ #
    p_hydrate = sub.add_parser(
        "hydrate",
        help="Interpolate variables into a YAML template read from STDIN.",
    )
    mode = p_hydrate.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--variables", "--vars", metavar="FILE",
        help="Path to a YAML file whose values are interpolated into the template.",
    )
    mode.add_argument(
        "--requirements", action="store_true", default=False,
        help="Emit a stub YAML listing the variables the template requires.",
    )

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
    yaml_text = sys.stdin.read()
    _extract_yaml_defaults(yaml_text, args)
    _require_project_and_issuetype(args)
    _, index = _make_index(args)
    result = validate_ticket_yaml(index, yaml_text)
    print(json.dumps(result, indent=2))
    if result != {"ok": True}:
        raise RuntimeError(result)


def _cmd_submit(args):
    yaml_text = sys.stdin.read()
    _extract_yaml_defaults(yaml_text, args)
    _require_project_and_issuetype(args)
    session, index = _make_index(args)

    result = validate_ticket_yaml(index, yaml_text)
    if result != {"ok": True}:
        print(json.dumps(result, indent=2), file=sys.stderr)
        raise ValueError("Ticket YAML failed validation")

    payload = build_ticket_payload(index, yaml_text)

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return

    response = session.post(f"{jira_base_url()}/rest/api/3/issue", json=payload)
    if response.status_code == 201:
        key = response.json()["key"]
        print(f"Created: {jira_base_url()}/browse/{key}")
    else:
        raise RuntimeError(f"Jira API error {response.status_code}: {response.text}")


def _cmd_hydrate(args):
    template_text = sys.stdin.read()
    if args.requirements:
        stub = required_variables(template_text)
        print(yaml.dump(stub, default_flow_style=False), end="")
    else:
        variables = load_variables(args.variables)
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
