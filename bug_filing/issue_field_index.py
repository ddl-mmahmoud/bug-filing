from bug_filing.fuzzy_matcher import FuzzyMatcher
from bug_filing.jira_session import JIRA_BASE_URL


class IssueFieldIndex:
    """
    Queries the Jira createmeta endpoint for a given project and issue type,
    caches the raw field definitions, and provides a name-to-key index for
    looking up a field's API key by its human-readable name.
    """

    def __init__(self, session, project, issuetype, envelope_fns=None, type_matchers=None):
        url = f"{JIRA_BASE_URL}/rest/api/3/issue/createmeta"
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
        self._types = None
        self._unambiguous = None
        self._matchers = {}
        self._type_matchers = dict(type_matchers or {})
        self._envelope_fns = {
            "adf": self._envelope_adf,
            "string": self._envelope_string,
            "choice": self._envelope_choice,
            **(envelope_fns or {}),
        }

    _IDENTIFIER_KEYS = ["value", "key", "name", "id"]
    _ADF_SYSTEMS = {"description", "environment"}

    def _item_type(self, meta):
        if meta.get("allowedValues"):
            keys = [k for k in self._IDENTIFIER_KEYS if k in meta["allowedValues"][0]]
            return ("choice", keys)
        if meta["schema"].get("type") == "user" or meta["schema"].get("items") == "user":
            return ("user",)
        if meta["schema"].get("custom", "").endswith(":gh-sprint"):
            return ("sprint",)
        if meta["schema"].get("custom", "").endswith(":textarea") or meta["schema"].get("system") in self._ADF_SYSTEMS:
            return ("adf",)
        return ("string",)

    def _field_type(self, meta):
        if meta["schema"]["type"] == "array":
            return ("array", self._item_type(meta))
        return self._item_type(meta)

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
                for k in self._IDENTIFIER_KEYS:
                    if k in allowed[0]:
                        result[meta["name"]] = {k: allowed[0][k]}
                        break
            self._unambiguous = result
        return self._unambiguous

    @property
    def types(self):
        if self._types is None:
            self._types = {meta["name"]: self._field_type(meta) for meta in self.fields.values()}
        return self._types

    def field(self, name, value):
        return (self.name_to_key[name], self._enveloped(value, self.types[name]))

    def value_matcher(self, field_name):
        if field_name not in self._matchers:
            field_type = self.types[field_name]
            type_tag = field_type[1][0] if field_type[0] == "array" else field_type[0]
            if type_tag in self._type_matchers:
                self._matchers[field_name] = self._type_matchers[type_tag]
            else:
                av = self.allowed_values(field_name)
                self._matchers[field_name] = FuzzyMatcher(av) if isinstance(av, list) else None
        return self._matchers[field_name]

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

    def _canonical_value(self, field_name, value_string):
        """Return the preferred identifier value for the allowedValues entry that contains value_string."""
        field_key = self.name_to_key[field_name]
        meta = self.fields[field_key]
        field_type = self.types[field_name]
        if field_type[0] == "choice":
            id_keys = field_type[1]
        elif field_type[0] == "array" and field_type[1][0] == "choice":
            id_keys = field_type[1][1]
        else:
            id_keys = None
        if id_keys is None:
            return value_string
        for entry in meta.get("allowedValues", []):
            if any(entry.get(k) == value_string for k in id_keys):
                for k in id_keys:
                    if k in entry:
                        return entry[k]
        return value_string

    def allowed_values(self, field_name):
        field_key = self.name_to_key[field_name]
        meta = self.fields[field_key]
        return self._allowed_for_type(meta, self.types[field_name])

    def allowed_fields(self):
        return list(self.name_to_key.keys())

    def _allowed_for_type(self, meta, field_type):
        tag = field_type[0]
        if tag == "array":
            return self._allowed_for_type(meta, field_type[1])
        if tag == "choice":
            return [
                av[k]
                for av in meta.get("allowedValues", [])
                for k in field_type[1]
                if k in av
            ]
        if tag == "string":
            return "SCALAR"
        if tag == "adf":
            return "ADF"
        if tag == "user":
            return "USER"
        if tag == "sprint":
            return "SPRINT"
        raise ValueError(f"Unknown field type: {tag!r}")

    @staticmethod
    def _envelope_adf(value, field_type):
        if isinstance(value, str):
            from bug_filing.adf import from_markdown
            return from_markdown(value)
        if not isinstance(value, dict):
            raise ValueError(f"Expected ADF doc or markdown string, got {type(value).__name__}")
        return value

    @staticmethod
    def _envelope_string(value, field_type):
        if isinstance(value, dict):
            raise ValueError(f"Expected plain string, got dict")
        return value

    @staticmethod
    def _envelope_choice(value, field_type):
        if isinstance(value, dict):
            raise ValueError(f"Expected scalar for choice field, got dict")
        return {field_type[1][0]: value}

    def _enveloped(self, value, field_type):
        tag = field_type[0]

        if tag == "array":
            if not isinstance(value, list):
                raise ValueError(f"Expected a list for array field, got {type(value).__name__}")
            return [self._enveloped(v, field_type[1]) for v in value]

        if isinstance(value, list):
            raise ValueError(f"Expected {tag}, got a list")

        if tag not in self._envelope_fns:
            raise ValueError(f"Unknown field type: {tag!r}")
        return self._envelope_fns[tag](value, field_type)
