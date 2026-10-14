"""
Search-text building shared by the ODP and PTAB search tools.

Both sources use the same query syntax, and both ignore "*" and "?"
inside a quoted phrase, so names are built by one rule.
"""

import re
from typing import List, Optional, Sequence, Tuple

_QUERY_OPERATOR_WORDS = {"AND", "OR", "NOT"}
_QUERY_SYNTAX_CHARS = re.compile(r'([+\-!(){}\[\]^"~:\\/&|])')
_APOSTROPHES = re.compile(r"['\u2018\u2019`]")
_LETTER_RUNS = re.compile(r"[^\W_]+")

# The most words for which each word is also left out in turn, so a
# looser-name check makes at most 2 + this many extra searches.
MAX_WORDS_TO_DROP = 4


def _query_word(word: str) -> str:
    """Make one name word safe to send bare in a q string.

    Words ODP would read as operators (AND/OR/NOT) are lowercased and
    query-syntax characters are escaped. "*" and "?" are left as wildcards.
    """
    if word.upper() in _QUERY_OPERATOR_WORDS:
        word = word.lower()
    return _QUERY_SYNTAX_CHARS.sub(r"\\\1", word)


def name_clause(field: str, name: str) -> str:
    """Build a q clause matching a person or organization name."""
    return f"{field}:{name_expr(name)}"


def name_expr(name: str) -> str:
    """Build the part of a name clause that follows "field:".

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
        return _query_word(name)
    if "*" not in name and "?" not in name:
        phrase = name.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{phrase}"'
    return _and_group([
        _query_word(word)
        for word in name.split()
        if any(ch.isalnum() for ch in word)
    ])


def _and_group(terms: Sequence[str]) -> str:
    """One term bare, several as (a AND b)."""
    return terms[0] if len(terms) == 1 else f"({' AND '.join(terms)})"


def has_apostrophe(name: str) -> bool:
    """True if the name has a straight, curly or backtick apostrophe."""
    return bool(_APOSTROPHES.search(name))


def prefix_terms(name: str) -> List[str]:
    """Split a name into word prefixes such as ["Raytheon*", "Co*"].

    Each word is cut at its first apostrophe, so "Children's" becomes
    "Children*" and also matches the stored spellings "Children’s",
    "CHILDRENS", "CHILDREN?S" and "CHILDREN;S" (live-verified 2026-10-14).
    Punctuation splits a word into its runs of letters and digits, as the
    search index does ("Hewlett-Packard" -> "Hewlett*", "Packard*").
    """
    terms = []
    for word in name.split():
        word = _APOSTROPHES.split(word, maxsplit=1)[0]
        terms.extend(f"{run}*" for run in _LETTER_RUNS.findall(word))
    return terms


def prefix_expr(name: str) -> Optional[str]:
    """Every word of the name as a prefix, ANDed; None if no words."""
    terms = prefix_terms(name)
    return _and_group(terms) if terms else None


def looser_name_exprs(name: str) -> List[Tuple[str, str]]:
    """Forms of a name looser than name_expr(name), as (label, expr).

    Used when a name search finds nothing (live-verified 2026-10-14):
    - all words in any order: "Jodele Sonata" -> 23, "Raytheon Co" -> 7
    - every word as a prefix: "Raytheon Co" -> 9448 ("Raytheon Company")
    - each word left out in turn, for 2 to MAX_WORDS_TO_DROP words:
      "Cincinnati Childrens Hospital" -> 0 in every form above, but 44
      without "Childrens"
    Forms that repeat an earlier one, or the original, are left out.
    """
    words = [
        _query_word(word)
        for word in name.split()
        if any(ch.isalnum() for ch in word)
    ]
    prefixes = prefix_terms(name)
    forms = []
    if len(words) > 1:
        forms.append(("all words, in any order", _and_group(words)))
    if prefixes:
        forms.append(("every word as a prefix", _and_group(prefixes)))
    if 2 <= len(prefixes) <= MAX_WORDS_TO_DROP:
        for i, term in enumerate(prefixes):
            rest = prefixes[:i] + prefixes[i + 1:]
            forms.append((f"without {term.rstrip('*')}", _and_group(rest)))
    seen = {name_expr(name)}
    looser = []
    for label, expr in forms:
        if expr not in seen:
            seen.add(expr)
            looser.append((label, expr))
    return looser


def and_join(query: Optional[str], clauses: Sequence[str]) -> Optional[str]:
    """Join the caller's search text and a tool's own clauses with AND.

    The caller's text is put in parentheses when anything is joined to it.
    USPTO reads "A OR B AND C" as "B AND C" (live-verified 2026-10-14:
    PGR or CBM trials filed in 2024 -> 0 bare, 41 in parentheses), so a
    bare OR would silently drop half of the caller's search. The tool's
    own clauses are single field:value terms or already in parentheses.
    Returns None when there is nothing to search on.
    """
    parts = [f"({query})" if clauses else query] if query else []
    parts.extend(clauses)
    return " AND ".join(parts) or None
