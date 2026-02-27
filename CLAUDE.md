# CLAUDE.md

## Running things

This is a `uv` project. Use `uv run` for everything:

```bash
uv run pytest
uv run python -m bug_filing.ticket_cli hydrate --list
```

The `ticket-yaml` wrapper script at the repo root also works and delegates to `uv run`.

## Project layout

```
bug_filing/
  ticket_cli.py       # CLI entry point — subcommands: template, validate, submit, hydrate
  ticket_yaml.py      # YAML template generation and ticket payload building
  templating.py       # Jinja2 hydration (hydrate, load_variables, required_variables)
  issue_field_index.py # IssueFieldIndex + FieldTypeHandler base class
  jira_sprints.py     # SprintHandler
  jira_users.py       # UserHandler
  fuzzy_matcher.py    # FuzzyMatcher used throughout for field/value lookup
  adf.py              # Markdown → Atlassian Document Format conversion
tpl/                  # Reusable YAML ticket templates
references/           # Example Jira API responses for the DOM project:
                      #   dom-73941.json         — GET /issue example
                      #   dom-bug-createmeta.json — GET /createmeta for Bug
default_variables.yaml  # Auto-loaded as base variables for every hydrate invocation
tests/                # pytest; one test file per module
```

## Architecture: FieldTypeHandler

`IssueFieldIndex` dispatches field serialisation to `FieldTypeHandler` subclasses.
Built-in handlers (in priority order): `ChoiceHandler`, `AdfHandler`, `StringHandler`.
External handlers (`UserHandler`, `SprintHandler`) are prepended at construction time.

Key methods each handler may implement:
- `detect(meta)` — return True if this handler owns the field
- `envelope(value, meta)` — convert a resolved value to its Jira API shape
- `matcher(meta)` — return a `FuzzyMatcher` for valid value strings, or None
- `canonical(value, meta)` — canonical form of a value string (for ambiguity resolution)
- `allowed(meta)` — list of valid value strings, or a sentinel like `"SCALAR"`

**`force_scalar = True`** on a handler overrides `"type": "array"` in the createmeta schema,
preventing `_enveloped` from wrapping the value in a list. Needed for `SprintHandler`.

## Jira API quirks

- **Sprint field (`customfield_10016`)**: the createmeta schema says `"type": "array"` and
  GET responses return `[{"id": N, "name": "...", ...}]`, but the create/update API expects
  a **bare integer** sprint ID: `"customfield_10016": 10625`. Hence `SprintHandler.force_scalar = True`
  and `envelope` returning the int directly.

- The `references/` directory contains real API responses that are useful as ground truth
  when debugging field format issues.

## hydrate behaviour

- `default_variables.yaml` in CWD is always loaded as the base variable set.
- `--vars FILE` merges on top (shallow), overwriting any conflicting keys.
- `--template FILE` flips stdin to carry variables instead of the template.
- Missing variables render as empty strings (`ChainableUndefined`) — hydration is intentionally
  lenient because the output is typically reviewed before submission.
- Templates live in `tpl/`; `hydrate --list` enumerates them.
