import re


class FuzzyMatcher:
    @staticmethod
    def sanitize(s):
        s = re.sub(r'[^a-zA-Z0-9\s]', '', s).lower()
        return re.sub(r'\s+', ' ', s).strip()

    @staticmethod
    def _tokenize(s):
        return FuzzyMatcher.sanitize(s).split()

    def __init__(self, strings):
        self._originals = list(strings)
        self._token_to_originals = {}
        self._original_to_tokens = {}
        self._original_to_sanitized = {}
        self._original_to_collapsed = {}
        for orig in self._originals:
            tokens = self._tokenize(orig)
            sanitized = self.sanitize(orig)
            self._original_to_tokens[orig] = tokens
            self._original_to_sanitized[orig] = sanitized
            self._original_to_collapsed[orig] = sanitized.replace(' ', '')
            for token in tokens:
                self._token_to_originals.setdefault(token, set()).add(orig)

    def lookup(self, s):
        query_tokens = self._tokenize(s)
        collapsed_query = self.sanitize(s).replace(' ', '')

        matched = set()
        for qt in query_tokens:
            for token, originals in self._token_to_originals.items():
                if qt in token:
                    matched.update(originals)
        for orig, collapsed in self._original_to_collapsed.items():
            if collapsed_query in collapsed:
                matched.add(orig)

        if len(matched) > 1:
            # Prefer candidates where any query token is a direct substring of any of their tokens,
            # or the collapsed query is a substring of the collapsed candidate
            substring = {
                orig for orig in matched
                if any(qt in token for qt in query_tokens for token in self._original_to_tokens[orig])
                or collapsed_query in self._original_to_collapsed[orig]
            }
            if substring and substring != matched:
                matched = substring

        if len(matched) > 1:
            # Further prefer candidates where any query token matches from the start of a token
            prefix = {
                orig for orig in matched
                if any(token.startswith(qt) for qt in query_tokens for token in self._original_to_tokens[orig])
            }
            if prefix and prefix != matched:
                matched = prefix

        if len(matched) > 1:
            # Further prefer candidates whose full sanitized form starts with the sanitized query,
            # or whose collapsed form starts with the collapsed query
            sanitized_query = self.sanitize(s)
            full_prefix = {
                orig for orig in matched
                if self._original_to_sanitized[orig].startswith(sanitized_query)
                or self._original_to_collapsed[orig].startswith(collapsed_query)
            }
            if full_prefix and full_prefix != matched:
                matched = full_prefix

        if len(matched) > 1:
            # Prefer candidates whose collapsed form exactly equals the collapsed query
            exact = {
                orig for orig in matched
                if self._original_to_collapsed[orig] == collapsed_query
            }
            if exact and exact != matched:
                matched = exact

        return [orig for orig in self._originals if orig in matched]
