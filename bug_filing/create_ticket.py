#!/usr/bin/env python3
"""
CLI tool for creating a Jira bug ticket from a TestRail test failure.

All credentials can be supplied as environment variables or overridden via
the corresponding command-line arguments listed below.

Required environment variables (or CLI equivalents):
  JIRA_API_USERNAME            --jira-username
  JIRA_API_PASSWORD            --jira-password
  TESTRAIL_USERNAME            --testrail-username
  TESTRAIL_API_KEY             --testrail-api-key
  FLEETCOMMAND_USER_API_TOKEN  --fleetcommand-token

Example usage:
  create-ticket \\
      --test-id 12345 \\
      --summary "Bug: login page crashes on submit" \\
      --severity S2 \\
      --engteam "UI" \\
      --component "UI - Other" \\
      --failure-reason "Product Bug" \\
      --blocking \\
      --assignee "Jane Smith"
"""

import argparse
import logging
import os
import sys

VALID_SEVERITIES = ["S0", "S1", "S2", "S3"]

VALID_FAILURE_REASONS = [
    "Product Bug",
    "Test Needs Refactor",
    "Test Framework Bug",
    "Test Dependency Missing",
    "System Issues",
    "Other",
    "Untriaged",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="create-ticket",
        description="Create a Jira bug ticket from a TestRail test failure.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ------------------------------------------------------------------ #
    # Required ticket fields                                               #
    # ------------------------------------------------------------------ #
    ticket = parser.add_argument_group("ticket fields")

    ticket.add_argument(
        "--test-id",
        required=True,
        metavar="ID",
        help="TestRail test ID associated with the failure (e.g. 12345).",
    )
    ticket.add_argument(
        "--summary",
        required=True,
        metavar="TEXT",
        help="Title of the Jira bug ticket.",
    )
    ticket.add_argument(
        "--severity",
        required=True,
        choices=VALID_SEVERITIES,
        metavar="LEVEL",
        help=f"Bug severity. One of: {', '.join(VALID_SEVERITIES)}.",
    )
    ticket.add_argument(
        "--engteam",
        required=True,
        metavar="TEAM",
        help=(
            "Engineering team responsible for the bug "
            "(e.g. 'Develop', 'UI', 'Compute')."
        ),
    )
    ticket.add_argument(
        "--component",
        required=True,
        metavar="NAME",
        help="Jira component name (e.g. 'Executions - Jobs', 'UI - Other').",
    )
    ticket.add_argument(
        "--failure-reason",
        required=True,
        choices=VALID_FAILURE_REASONS,
        metavar="REASON",
        help=f"Failure categorisation. One of: {', '.join(VALID_FAILURE_REASONS)}.",
    )

    # ------------------------------------------------------------------ #
    # Optional ticket fields                                               #
    # ------------------------------------------------------------------ #
    ticket.add_argument(
        "--blocking",
        action="store_true",
        default=False,
        help="Label the ticket as blocking (default: not-blocking).",
    )
    ticket.add_argument(
        "--add-description",
        default="",
        metavar="TEXT",
        help="Extra text appended to the ticket description.",
    )
    ticket.add_argument(
        "--assignee",
        default="",
        metavar="NAME",
        help="Display name of the Jira user to assign the ticket to.",
    )
    ticket.add_argument(
        "--reporter",
        default="QE Automation",
        metavar="NAME",
        help="Display name of the Jira reporter (default: 'QE Automation').",
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
    creds.add_argument(
        "--testrail-username",
        default=None,
        metavar="EMAIL",
        help="Overrides TESTRAIL_USERNAME.",
    )
    creds.add_argument(
        "--testrail-api-key",
        default=None,
        metavar="KEY",
        help="Overrides TESTRAIL_API_KEY.",
    )
    creds.add_argument(
        "--fleetcommand-token",
        default=None,
        metavar="TOKEN",
        help="Overrides FLEETCOMMAND_USER_API_TOKEN.",
    )

    # ------------------------------------------------------------------ #
    # Logging                                                              #
    # ------------------------------------------------------------------ #
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: WARNING).",
    )

    return parser


def apply_credential_overrides(args: argparse.Namespace) -> None:
    """
    Copy any credential CLI arguments into the corresponding environment
    variables so the rest of the code can read them via os.environ as usual.
    """
    overrides = {
        "jira_username": "JIRA_API_USERNAME",
        "jira_password": "JIRA_API_PASSWORD",
        "testrail_username": "TESTRAIL_USERNAME",
        "testrail_api_key": "TESTRAIL_API_KEY",
        "fleetcommand_token": "FLEETCOMMAND_USER_API_TOKEN",
    }
    for attr, env_var in overrides.items():
        value = getattr(args, attr, None)
        if value is not None:
            os.environ[env_var] = value


def check_required_env_vars() -> None:
    """
    Verify that the mandatory environment variables are present after
    credential overrides have been applied.
    """
    required = [
        "JIRA_API_USERNAME",
        "JIRA_API_PASSWORD",
        "TESTRAIL_USERNAME",
        "TESTRAIL_API_KEY",
        "FLEETCOMMAND_USER_API_TOKEN",
    ]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        print(
            f"Error: the following required credentials are not set "
            f"(provide them as environment variables or CLI arguments):\n"
            + "\n".join(f"  {v}" for v in missing),
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s: %(message)s",
    )

    apply_credential_overrides(args)
    check_required_env_vars()

    # Import here so that testrail_client (which reads env vars on init)
    # is not imported until after credentials have been applied.
    from bug_filing.jira_client import create_jira_bug

    is_blocking = "on" if args.blocking else "off"

    print(f"Creating Jira ticket for TestRail test {args.test_id} …")

    ticket_key = create_jira_bug(
        test_id=args.test_id,
        summary=args.summary,
        severity=args.severity,
        is_blocking=is_blocking,
        add_description=args.add_description,
        failure_reason=args.failure_reason,
        engteam=args.engteam,
        component=args.component,
        assignee=args.assignee,
        reporter=args.reporter,
    )

    print(f"Created: https://dominodatalab.atlassian.net/browse/{ticket_key}")


if __name__ == "__main__":
    main()
