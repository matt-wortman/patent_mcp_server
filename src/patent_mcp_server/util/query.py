"""
Search-text building shared by the ODP and PTAB search tools.

Both sources use the same query syntax, and both ignore "*" and "?"
inside a quoted phrase, so names are built by one rule.
"""

import re

_QUERY_OPERATOR_WORDS = {"AND", "OR", "NOT"}
_QUERY_SYNTAX_CHARS = re.compile(r'([+\-!(){}\[\]^"~:\\/&|])')


def name_clause(field: str, name: str) -> str:
    """Build a q clause matching a person or organization name.

    A multi-word name is normally sent as a quoted exact phrase. But ODP
    ignores "*" and "?" inside a quoted phrase (live-verified 2026-10-14:
    "Cincinnati Children*" -> 0 matches), so a multi-word name with a
    wildcard is instead sent as each word ANDed together:
    field:(Cincinnati AND Children*) -> 44 matches. Words then need not
    be adjacent or in order.

    PTAB party names behave the same way ("Samsung Elec*" -> 0,
    (Samsung AND Elec*) -> 1119), and a quoted single word loses its
    wildcard too ("Samsun*" -> 0, bare Samsun* -> 1201), so a single word
    is always sent bare.

    In that form, words ODP would read as operators (AND/OR/NOT) are
    lowercased, query-syntax characters are escaped, and words with no
    letters or digits (e.g. "&") are dropped.
    """
    if " " not in name:
        return f"{field}:{name}"
    if "*" not in name and "?" not in name:
        return f'{field}:"{name}"'
    terms = []
    for word in name.split():
        if not any(ch.isalnum() for ch in word):
            continue
        if word.upper() in _QUERY_OPERATOR_WORDS:
            word = word.lower()
        terms.append(_QUERY_SYNTAX_CHARS.sub(r"\\\1", word))
    return f"{field}:({' AND '.join(terms)})"
