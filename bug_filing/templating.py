"""
Template hydration using Jinja2.

Loads a variables YAML file (a flat or one-level-nested mapping) and
renders a Jinja2 template string with those variables.  Missing variables
are left as their original placeholder text in the output.
"""

import yaml
from jinja2 import ChainableUndefined, Environment
from jinja2 import meta as jinja2_meta


def load_variables(path):
    """Load a YAML file and return its top-level mapping."""
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Variables file {path!r} must be a YAML mapping")
    return data


def required_variables(template_text):
    """Return a nested stub dict of every variable referenced in *template_text*.

    Simple references (``{{ foo }}``) produce ``{"foo": None}``.
    Dotted references (``{{ team.name }}``) produce ``{"team": {"name": None}}``.
    """
    from jinja2.nodes import Getattr, Name

    env = Environment()
    ast = env.parse(template_text)
    undeclared = jinja2_meta.find_undeclared_variables(ast)

    # Resolve a Getattr chain down to its root Name, returning the full path
    # as a tuple only when the root is an undeclared variable.
    def _resolve(node):
        if isinstance(node, Name):
            return (node.name,) if node.name in undeclared else None
        if isinstance(node, Getattr):
            parent = _resolve(node.node)
            return parent + (node.attr,) if parent is not None else None
        return None

    paths = set()
    names_with_dotted_access = set()
    for node in ast.find_all(Getattr):
        path = _resolve(node)
        if path:
            paths.add(path)
            names_with_dotted_access.add(path[0])

    # Add undeclared names that are only ever used as plain references.
    for name in undeclared - names_with_dotted_access:
        paths.add((name,))

    # Drop any path that is a strict prefix of a longer path so that
    # ``{{ a.b.c }}`` doesn't also generate a spurious ``a.b: null`` entry.
    maximal = {p for p in paths if not any(q != p and q[:len(p)] == p for q in paths)}

    # Build nested dict scaffold from the maximal paths.
    result = {}
    for path in sorted(maximal):
        d = result
        for part in path[:-1]:
            d = d.setdefault(part, {})
        d.setdefault(path[-1], None)

    return result


def hydrate(template_text, variables):
    """Render *template_text* with *variables*.

    Missing variables are left as Jinja2 placeholders in the output,
    e.g. ``{{ summary }}`` remains if ``summary`` is not supplied.
    """

    class _PassthroughUndefined(ChainableUndefined):
        """Renders missing variables as their original {{ name }} placeholder."""

        def _dotted_path(self):
            try:
                return object.__getattribute__(self, "_stored_path")
            except AttributeError:
                return object.__getattribute__(self, "_undefined_name") or ""

        def __str__(self):
            path = self._dotted_path()
            return "{{ " + path + " }}" if path else ""

        def __getattr__(self, attr):
            path = self._dotted_path()
            child = self.__class__()
            object.__setattr__(child, "_stored_path", f"{path}.{attr}" if path else attr)
            return child

        __getitem__ = __getattr__

    env = Environment(undefined=_PassthroughUndefined, keep_trailing_newline=True)
    return env.from_string(template_text).render(variables)
