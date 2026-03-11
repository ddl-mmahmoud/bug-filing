#!/usr/bin/env python3
"""
Fetch a Jira ticket and emit it as YAML.

Usage
-----
  get-ticket-yaml DOM-12345

Required environment variables
-------------------------------
  JIRA_URL
  JIRA_API_USERNAME
  JIRA_API_PASSWORD

ADF fields (description, environment, custom textareas) are converted to
Markdown.  User, sprint, and choice fields are unwrapped to their human-
readable scalar form.  Fields that cannot be cleanly simplified are emitted
with their raw JSON value.
"""

import argparse
import json
import os
import sys

from bug_filing.jira_session import jira_requests_session
from bug_filing.read_ticket import get_ticket, ticket_to_yaml, ticket_to_yaml_dict


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="get-ticket-yaml",
        description="Fetch a Jira ticket and emit it as YAML.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    creds = parser.add_argument_group(
        "access overrides",
        "Each overrides the corresponding environment variable.",
    )
    creds.add_argument("--jira-url", default=None, metavar="URL",
                       help="Overrides JIRA_URL")
    creds.add_argument("--jira-username", default=None, metavar="EMAIL",
                       help="Overrides JIRA_API_USERNAME")
    creds.add_argument("--jira-password", default=None, metavar="TOKEN",
                       help="Overrides JIRA_API_PASSWORD")
    parser.add_argument("issue_key", metavar="TICKET-KEY",
                        help="Jira issue key, e.g. DOM-12345")
    parser.add_argument("--json", action="store_true", default=False,
                        help="Emit JSON instead of YAML.")
    return parser


def _apply_access_overrides(args):
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
    required = ["JIRA_URL", "JIRA_API_USERNAME", "JIRA_API_PASSWORD"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise RuntimeError(
            "the following required access vars are not set "
            "(provide them as environment variables or CLI arguments):\n"
            + "\n".join(f"  {v}" for v in missing)
        )


def main():
    parser = _build_parser()
    args = parser.parse_args()
    try:
        _apply_access_overrides(args)
        _check_required_env_vars()
        session = jira_requests_session()
        data = get_ticket(session, args.issue_key)
        if args.json:
            print(json.dumps(ticket_to_yaml_dict(data), indent=2))
        else:
            print(ticket_to_yaml(data), end="")
        return 0
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
