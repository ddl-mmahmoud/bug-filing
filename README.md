# Ticket YAML
Create JIRA tickets from the command line using YAML while fuzzy matching identifiers.

Very vibe coded.

## Usage

```
usage: ticket-yaml [-h] [--jira-username EMAIL] [--jira-password TOKEN]
                   {template,validate,submit} ...

YAML-based Jira ticket authoring tool.

positional arguments:
  {template,validate,submit}
    template            Emit a YAML template for the given project and issue
                        type.
    validate            Validate a YAML ticket read from STDIN.
    submit              Validate and file a YAML ticket from STDIN as a Jira
                        issue.

options:
  -h, --help            show this help message and exit

credential overrides:
  Each of these overrides the corresponding environment variable. If neither is set the tool will exit with an error.

  --jira-username EMAIL
                        Overrides JIRA_API_USERNAME.
  --jira-password TOKEN
                        Overrides JIRA_API_PASSWORD.

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
```
