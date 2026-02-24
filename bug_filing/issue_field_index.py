from bug_filing.fuzzy_matcher import FuzzyMatcher
from bug_filing.jira_session import jira_base_url


_IDENTIFIER_KEYS = ["value", "key", "name", "id"]
_ADF_SYSTEMS = {"description", "environment"}


class FieldTypeHandler:
    """
    Base class for field type handlers.  Each subclass encapsulates detection,
    value matching, enveloping, and allowed-value listing for one Jira field type.

    Subclasses must set the ``tag`` class attribute and implement ``detect`` and
    ``envelope``.  ``matcher``, ``canonical``, and ``allowed`` have sensible
    defaults (no matcher, identity canonical, None allowed-list).
    """

    tag = None  # short string identifier, e.g. "choice", "user", "sprint"

    def detect(self, meta: dict) -> bool:
        """Return True if this handler should be used for the given field metadata."""
        raise NotImplementedError

    def matcher(self, meta: dict):
        """Return a FuzzyMatcher over valid value strings, or None."""
        return None

    def envelope(self, value, meta: dict):
        """Convert a single resolved value to its Jira API representation."""
        raise NotImplementedError

    def canonical(self, value: str, meta: dict) -> str:
        """Return the canonical form of a value string (for ambiguity resolution)."""
        return value

    def allowed(self, meta: dict):
        """Return a list of valid value strings, or a sentinel string such as 'SCALAR'."""
        return None


class ChoiceHandler(FieldTypeHandler):
    tag = "choice"

    def detect(self, meta):
        return bool(meta.get("allowedValues"))

    def _id_keys(self, meta):
        return [k for k in _IDENTIFIER_KEYS if k in meta["allowedValues"][0]]

    def matcher(self, meta):
        return FuzzyMatcher(self.allowed(meta))

    def envelope(self, value, meta):
        if isinstance(value, dict):
            raise ValueError("Expected scalar for choice field, got dict")
        return {self._id_keys(meta)[0]: value}

    def canonical(self, value, meta):
        id_keys = self._id_keys(meta)
        for entry in meta.get("allowedValues", []):
            if any(entry.get(k) == value for k in id_keys):
                for k in id_keys:
                    if k in entry:
                        return entry[k]
        return value

    def allowed(self, meta):
        id_keys = self._id_keys(meta)
        return [av[k] for av in meta["allowedValues"] for k in id_keys if k in av]


class AdfHandler(FieldTypeHandler):
    tag = "adf"

    def detect(self, meta):
        return (meta["schema"].get("custom", "").endswith(":textarea") or
                meta["schema"].get("system") in _ADF_SYSTEMS)

    def envelope(self, value, meta):
        if isinstance(value, str):
            from bug_filing.adf import from_markdown
            return from_markdown(value)
        if not isinstance(value, dict):
            raise ValueError(f"Expected ADF doc or markdown string, got {type(value).__name__}")
        return value

    def allowed(self, meta):
        return "ADF"


class StringHandler(FieldTypeHandler):
    tag = "string"

    def detect(self, meta):
        return True  # catch-all; must be last in the handler list

    def envelope(self, value, meta):
        if isinstance(value, dict):
            raise ValueError("Expected plain string, got dict")
        return value

    def allowed(self, meta):
        return "SCALAR"


_BUILTIN_HANDLERS = [ChoiceHandler(), AdfHandler(), StringHandler()]


class IssueFieldIndex:
    """
    Queries the Jira createmeta endpoint for a given project and issue type,
    caches the raw field definitions, and provides a name-to-key index for
    looking up a field's API key by its human-readable name.

    ``type_handlers`` is an optional list of :class:`FieldTypeHandler` instances
    that are consulted before the built-in handlers (ChoiceHandler, AdfHandler,
    StringHandler).  Register external handlers such as UserHandler or
    SprintHandler here.
    """

    def __init__(self, session, project, issuetype, type_handlers=None):
        url = f"{jira_base_url()}/rest/api/3/issue/createmeta"
        params = {
            "projectKeys": project,
            "issuetypeNames": issuetype,
            "expand": "projects.issuetypes.fields",
        }
        response = session.get(url, params=params)
        response.raise_for_status()

        data = response.json()
        projects = data.get("projects", [])
        if not projects:
            raise ValueError(f"Project {project!r} not found")
        issuetypes = projects[0].get("issuetypes", [])
        if not issuetypes:
            raise ValueError(f"Issue type {issuetype!r} not found in project {project!r}")

        self.fields = issuetypes[0]["fields"]
        self.name_to_key = {meta["name"]: key for key, meta in self.fields.items()}
        self.required = [meta["name"] for meta in self.fields.values() if meta["required"]]

        self._handlers = list(type_handlers or []) + _BUILTIN_HANDLERS
        self._resolved = {}   # field_name -> (is_array: bool, handler: FieldTypeHandler)
        self._matchers = {}   # field_name -> FuzzyMatcher | None
        self._unambiguous = None

    # ------------------------------------------------------------------
    # Internal: per-field handler resolution
    # ------------------------------------------------------------------

    def _resolve(self, field_name):
        """Return (is_array, handler) for the given field, cached after first call."""
        if field_name not in self._resolved:
            meta = self.fields[self.name_to_key[field_name]]
            is_array = meta["schema"]["type"] == "array"
            for handler in self._handlers:
                if handler.detect(meta):
                    self._resolved[field_name] = (is_array, handler)
                    break
            else:
                raise ValueError(f"No handler matched field {field_name!r}")
        return self._resolved[field_name]

    def _meta(self, field_name):
        return self.fields[self.name_to_key[field_name]]

    # ------------------------------------------------------------------
    # Public type-query helpers
    # ------------------------------------------------------------------

    def field_tag(self, field_name) -> str:
        """Return the handler tag for this field, e.g. 'choice', 'adf', 'string'."""
        return self._resolve(field_name)[1].tag

    def field_is_array(self, field_name) -> bool:
        """Return True if this field expects a list of values."""
        return self._resolve(field_name)[0]

    # ------------------------------------------------------------------
    # Required-field helpers
    # ------------------------------------------------------------------

    @property
    def user_required(self):
        return [name for name in self.required if name not in self.unambiguous]

    @property
    def unambiguous(self):
        if self._unambiguous is None:
            result = {}
            for meta in self.fields.values():
                if not meta["required"]:
                    continue
                allowed = meta.get("allowedValues", [])
                if len(allowed) != 1:
                    continue
                for k in _IDENTIFIER_KEYS:
                    if k in allowed[0]:
                        result[meta["name"]] = {k: allowed[0][k]}
                        break
            self._unambiguous = result
        return self._unambiguous

    # ------------------------------------------------------------------
    # Value matching and enveloping
    # ------------------------------------------------------------------

    def value_matcher(self, field_name):
        """Return a FuzzyMatcher for this field's valid values, or None."""
        if field_name not in self._matchers:
            is_array, handler = self._resolve(field_name)
            self._matchers[field_name] = handler.matcher(self._meta(field_name))
        return self._matchers[field_name]

    def _enveloped(self, value, field_name):
        is_array, handler = self._resolve(field_name)
        meta = self._meta(field_name)
        if is_array:
            if not isinstance(value, list):
                raise ValueError(f"Expected a list for array field, got {type(value).__name__}")
            return [handler.envelope(v, meta) for v in value]
        if isinstance(value, list):
            raise ValueError(f"Expected {handler.tag!r}, got a list")
        return handler.envelope(value, meta)

    def field(self, name, value):
        return (self.name_to_key[name], self._enveloped(value, name))

    def _canonical_value(self, field_name, value_string):
        """Return the canonical form of value_string for this field (for ambiguity resolution)."""
        is_array, handler = self._resolve(field_name)
        return handler.canonical(value_string, self._meta(field_name))

    # ------------------------------------------------------------------
    # Fuzzy field/payload construction
    # ------------------------------------------------------------------

    def fuzzy_payload(self, fields):
        result = {}
        for name, value in fields.items():
            key, enveloped = self.fuzzy_field(name, value)
            result[key] = enveloped
        return {"fields": result}

    def fuzzy_field(self, name, value):
        field_matches = FuzzyMatcher(self.allowed_fields()).lookup(name)
        if len(field_matches) != 1:
            raise ValueError(f"Field {name!r} matched {len(field_matches)} candidates: {field_matches}")
        resolved_name = field_matches[0]

        matcher = self.value_matcher(resolved_name)
        if matcher is not None:
            def resolve_value(v):
                matches = matcher.lookup(str(v))
                if len(matches) != 1:
                    canonicals = {self._canonical_value(resolved_name, m) for m in matches}
                    if len(canonicals) == 1:
                        return canonicals.pop()
                    raise ValueError(f"Value {v!r} for field {resolved_name!r} matched {len(matches)} candidates: {matches}")
                return matches[0]
            resolved_value = [resolve_value(v) for v in value] if isinstance(value, list) else resolve_value(value)
        else:
            resolved_value = value

        return self.field(resolved_name, resolved_value)

    # ------------------------------------------------------------------
    # Allowed values and fields
    # ------------------------------------------------------------------

    def allowed_values(self, field_name):
        """Return a list of valid value strings, or a sentinel ('SCALAR', 'ADF', etc.)."""
        is_array, handler = self._resolve(field_name)
        return handler.allowed(self._meta(field_name))

    def allowed_fields(self):
        return list(self.name_to_key.keys())
