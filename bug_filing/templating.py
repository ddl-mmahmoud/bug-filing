"""
Template hydration using Jinja2.

Loads a variables YAML file (a flat or one-level-nested mapping) and
renders a Jinja2 template string with those variables.  Missing variables
raise an UndefinedError so mistakes are caught early.
"""

import yaml
from jinja2 import Environment, StrictUndefined, UndefinedError
from jinja2 import meta as jinja2_meta


def load_variables(path: str) -> dict:
    """Load a YAML file and return its top-level mapping."""
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Variables file {path!r} must be a YAML mapping")
    return data


def required_variables(template_text: str) -> list[str]:
    """Return the variable names referenced in *template_text*, sorted."""
    env = Environment()
    ast = env.parse(template_text)
    return sorted(jinja2_meta.find_undeclared_variables(ast))


def hydrate(template_text: str, variables: dict) -> str:
    """Render *template_text* with *variables*, raising on undefined names."""
    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    try:
        return env.from_string(template_text).render(variables)
    except UndefinedError as e:
        raise ValueError(f"Template variable error: {e}") from e
