"""
Search-text building shared by the ODP and PTAB search tools.

Both sources use the same query syntax, and both ignore "*" and "?"
inside a quoted phrase, so names are built by one rule.
"""

import re

_QUERY_OPERATOR_WORDS = {"AND", "OR", "NOT"}
_QUERY_SYNTAX_CHARS = re.compile(r'([+\-!(){}\[\]^"~:\\/&|])')


def _query_word(word: str) -> str:
    """Make one name word safe to send bare in a q string.

    Words ODP would read as operators (AND/OR/NOT) are lowercased and
    query-syntax characters are escaped. "*" and "?" are left as wildcards.
    """
    if word.upper() in _QUERY_OPERATOR_WORDS:
        word = word.lower()
    return _QUERY_SYNTAX_CHARS.sub(r"\\\1", word)


def name_clause(field: str, name: str) -> str:
    """Build a q clause matching a person or organization name.

    A multi-word name is normally sent as a quoted exact phrase. But ODP
    ignores "*" and "?" inside a quoted phrase (live-verified 2026-10-14:
    "Cincinnati Children*" -> 0 matches), so a multi-word name with a
    wildcard is instead sent as each word ANDed together:
    field:(Cincinnati AND Children*) -> 44 matches. Words then need not
    be adjacent or in order. Words with no letters or digits (e.g. "&")
    are dropped from that form.

    PTAB party names behave the same way ("Samsung Elec*" -> 0,
    (Samsung AND Elec*) -> 1119), and a quoted single word loses its
    wildcard too ("Samsun*" -> 0, bare Samsun* -> 1201), so a single word
    is always sent bare.

    Every form is escaped (live-verified 2026-10-14). Unescaped, a '"'
    split a phrase into loose pieces ('Sonata "Sunny" Jodele' -> 4019
    instead of 0), and a ')' or ':' made a search USPTO could not read.
    Escaping leaves ordinary names alone (Hewlett-Packard -> 34528 bare or
    escaped). Inside a phrase only '"' and '\\' need a backslash.
    """
    name = name.strip()
    if " " not in name:
        return f"{field}:{_query_word(name)}"
    if "*" not in name and "?" not in name:
        phrase = name.replace("\\", "\\\\").replace('"', '\\"')
        return f'{field}:"{phrase}"'
    terms = [
        _query_word(word)
        for word in name.split()
        if any(ch.isalnum() for ch in word)
    ]
    return f"{field}:({' AND '.join(terms)})"
