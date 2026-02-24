#!/usr/bin/env python3
"""
CLI for YAML-based Jira ticket authoring.

Subcommands
-----------
template   Emit a YAML template for the given project / issue type on STDOUT.
validate   Read a YAML ticket from STDIN and report any errors as JSON.
submit     Validate a YAML ticket from STDIN and file it as a Jira issue.
           Use --dry-run to emit the JSON payload instead of submitting.

Required environment variables
-------------------------------
  JIRA_API_USERNAME
  JIRA_API_PASSWORD

Example usage
-------------
  ticket-yaml template --project DOM --issuetype Bug
  ticket-yaml validate --project DOM --issuetype Bug < my-ticket.yaml
  ticket-yaml submit   --project DOM --issuetype Bug < my-ticket.yaml
  ticket-yaml submit   --project DOM --issuetype Bug --dry-run < my-ticket.yaml
"""

import argparse
import json
import os
import sys

import yaml

from bug_filing.issue_field_index import IssueFieldIndex
from bug_filing.jira_session import JIRA_BASE_URL, JIRA_ISSUE_URL, jira_requests_session
from bug_filing.fuzzy_matcher import FuzzyMatcher
from bug_filing.jira_users import make_user_envelope_fn
from bug_filing.jira_sprints import get_jira_sprints, make_sprint_envelope_fn
from bug_filing.ticket_yaml import build_ticket_payload, ticket_template, validate_ticket_yaml


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="ticket-yaml",
        description="YAML-based Jira ticket authoring tool.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ------------------------------------------------------------------ #
    # Credential overrides (fall back to environment variables)           #
    # ------------------------------------------------------------------ #
    creds = parser.add_argument_group(
        "credential overrides",
        "Each of these overrides the corresponding environment variable. "
        "If neither is set the tool will exit with an error.",
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

    return parser


def _apply_credential_overrides(args):
    """Copy credential CLI args into environment variables."""
    overrides = {
        "jira_username": "JIRA_API_USERNAME",
        "jira_password": "JIRA_API_PASSWORD",
    }
    for attr, env_var in overrides.items():
        value = getattr(args, attr, None)
        if value is not None:
            os.environ[env_var] = value


def _check_required_env_vars():
    """Verify that the mandatory Jira environment variables are present."""
    required = ["JIRA_API_USERNAME", "JIRA_API_PASSWORD"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise RuntimeError(
            "the following required credentials are not set "
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
    sprints = get_jira_sprints(session)
    envelope_fns = {
        "user": make_user_envelope_fn(session),
        "sprint": make_sprint_envelope_fn(session),
    }
    value_matchers = {"Sprint": FuzzyMatcher(sprints.keys())}
    return IssueFieldIndex(session, args.project, args.issuetype,
                           envelope_fns=envelope_fns, value_matchers=value_matchers)


def _cmd_template(args):
    index = _make_index(args)
    print(ticket_template(index, minimal=args.minimal, maximal=args.maximal), end="")


def _cmd_validate(args):
    yaml_text = sys.stdin.read()
    _extract_yaml_defaults(yaml_text, args)
    _require_project_and_issuetype(args)
    index = _make_index(args)
    result = validate_ticket_yaml(index, yaml_text)
    print(json.dumps(result, indent=2))
    if result != {"ok": True}:
        raise RuntimeError(result)


def _cmd_submit(args):
    yaml_text = sys.stdin.read()
    _extract_yaml_defaults(yaml_text, args)
    _require_project_and_issuetype(args)
    session = jira_requests_session()
    sprints = get_jira_sprints(session)
    envelope_fns = {
        "user": make_user_envelope_fn(session),
        "sprint": make_sprint_envelope_fn(session),
    }
    value_matchers = {"Sprint": FuzzyMatcher(sprints.keys())}
    index = IssueFieldIndex(session, args.project, args.issuetype,
                            envelope_fns=envelope_fns, value_matchers=value_matchers)

    result = validate_ticket_yaml(index, yaml_text)
    if result != {"ok": True}:
        print(json.dumps(result, indent=2), file=sys.stderr)
        raise ValueError("Ticket YAML failed validation")

    payload = build_ticket_payload(index, yaml_text)

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return

    response = session.post(JIRA_ISSUE_URL, json=payload)
    if response.status_code == 201:
        key = response.json()["key"]
        print(f"Created: {JIRA_BASE_URL}/browse/{key}")
    else:
        raise RuntimeError(f"Jira API error {response.status_code}: {response.text}")


_COMMANDS = {
    "template": _cmd_template,
    "validate": _cmd_validate,
    "submit":   _cmd_submit,
}


def main():
    parser = _build_parser()
    args = parser.parse_args()
    try:
        _apply_credential_overrides(args)
        _check_required_env_vars()
        _COMMANDS[args.subcommand](args)
        return 0

    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    main()
