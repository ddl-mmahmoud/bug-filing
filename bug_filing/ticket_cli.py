#!/usr/bin/env python3
"""
CLI for YAML-based Jira ticket authoring.

Subcommands
-----------
template   Emit a YAML template for the given project / issue type on STDOUT.
validate   Read a YAML ticket from STDIN and report any errors as JSON.
hydrate    Read a YAML ticket from STDIN, validate it, and emit the Jira
           issue-create JSON payload on STDOUT.  Exits non-zero on errors.

Required environment variables
-------------------------------
  JIRA_API_USERNAME
  JIRA_API_PASSWORD

Example usage
-------------
  ticket-yaml template --project DOM --issuetype Bug
  ticket-yaml validate --project DOM --issuetype Bug < my-ticket.yaml
  ticket-yaml hydrate  --project DOM --issuetype Bug < my-ticket.yaml
"""

import argparse
import json
import sys

from bug_filing.jira_client import IssueFieldIndex, jira_requests_session
from bug_filing.ticket_yaml import build_ticket_payload, ticket_template, validate_ticket_yaml


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="ticket-yaml",
        description="YAML-based Jira ticket authoring tool.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
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
    p_template.add_argument(
        "--minimal", action="store_true", default=False,
        help="Omit comments and blank lines from the template.",
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
    # hydrate                                                              #
    # ------------------------------------------------------------------ #
    p_hydrate = sub.add_parser(
        "hydrate",
        help="Validate a YAML ticket from STDIN and emit the Jira JSON payload.",
    )
    add_common(p_hydrate)

    return parser


def _make_index(args):
    session = jira_requests_session()
    return IssueFieldIndex(session, args.project, args.issuetype)


def _cmd_template(args):
    index = _make_index(args)
    print(ticket_template(index, minimal=args.minimal), end="")


def _cmd_validate(args):
    index = _make_index(args)
    yaml_text = sys.stdin.read()
    result = validate_ticket_yaml(index, yaml_text)
    print(json.dumps(result, indent=2))
    if result != {"ok": True}:
        sys.exit(1)


def _cmd_hydrate(args):
    index = _make_index(args)
    yaml_text = sys.stdin.read()
    result = validate_ticket_yaml(index, yaml_text)
    if result != {"ok": True}:
        print(json.dumps(result, indent=2), file=sys.stderr)
        sys.exit(1)
    payload = build_ticket_payload(index, yaml_text)
    print(json.dumps(payload, indent=2))


_COMMANDS = {
    "template": _cmd_template,
    "validate": _cmd_validate,
    "hydrate":  _cmd_hydrate,
}


def main():
    parser = _build_parser()
    args = parser.parse_args()
    _COMMANDS[args.subcommand](args)


if __name__ == "__main__":
    main()
