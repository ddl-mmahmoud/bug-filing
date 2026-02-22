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

from bug_filing.jira_client import JIRA_BASE_URL, JIRA_ISSUE_URL, IssueFieldIndex, jira_requests_session
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
    def add_common(p):
        p.add_argument("--project", required=True, metavar="KEY",
                       help="Jira project key (e.g. DOM).")
        p.add_argument("--issuetype", required=True, metavar="NAME",
                       help="Issue type name (e.g. Bug).")

    # ------------------------------------------------------------------ #
    # template                                                             #
    # ------------------------------------------------------------------ #
    p_template = sub.add_parser(
        "template",
        help="Emit a YAML template for the given project and issue type.",
    )
    add_common(p_template)
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
    add_common(p_validate)

    # ------------------------------------------------------------------ #
    # submit                                                               #
    # ------------------------------------------------------------------ #
    p_submit = sub.add_parser(
        "submit",
        help="Validate and file a YAML ticket from STDIN as a Jira issue.",
    )
    add_common(p_submit)
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


def _make_index(args):
    session = jira_requests_session()
    return IssueFieldIndex(session, args.project, args.issuetype)


def _cmd_template(args):
    index = _make_index(args)
    print(ticket_template(index, minimal=args.minimal, maximal=args.maximal), end="")


def _cmd_validate(args):
    index = _make_index(args)
    yaml_text = sys.stdin.read()
    result = validate_ticket_yaml(index, yaml_text)
    print(json.dumps(result, indent=2))
    if result != {"ok": True}:
        raise RuntimeError(result)


def _cmd_submit(args):
    session = jira_requests_session()
    index = IssueFieldIndex(session, args.project, args.issuetype)
    yaml_text = sys.stdin.read()

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
    except (ValueError, RuntimeError) as e:
        raise


if __name__ == "__main__":
    main()
