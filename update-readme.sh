#!/bin/bash

cat <<EOF > README.md
# Ticket YAML
Create JIRA tickets from the command line using YAML while fuzzy matching identifiers.

Very vibe coded.

## Usage

\`\`\`
$("$(dirname -- "$(readlink -- -f "$0")")/ticket-yaml" --help)
\`\`\`
EOF
