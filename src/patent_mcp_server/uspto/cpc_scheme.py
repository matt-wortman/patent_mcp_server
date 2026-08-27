"""
CPC scheme lookup client (static scheme pages on www.uspto.gov).

Resolves full CPC symbols (e.g. "G01S17/32") to their real titles plus the
parent chain of titles, by fetching and parsing the static per-subclass
scheme pages that USPTO's own Patent Public Search UI links to:

    https://www.uspto.gov/web/patents/classification/cpc/html/cpc-G01S.html

No API key or session is required. Each subclass page is fetched once per
process and the parsed entries are cached in memory.
"""

import html as html_module
import logging
import re
from typing import Any, Dict, List, Optional, Union

from patent_mcp_server.config import config
from patent_mcp_server.util.errors import ApiError, is_error
from patent_mcp_server.util.http import make_logged_client, request_bytes

logger = logging.getLogger('cpc_scheme')

CPC_SCHEME_URL_TEMPLATE = (
    "https://www.uspto.gov/web/patents/classification/cpc/html/cpc-{subclass}.html"
)

# A resolvable CPC symbol starts with a subclass: section letter, two-digit
# class, subclass letter (e.g. "G01S" in "G01S17/32").
_SUBCLASS_RE = re.compile(r"^([A-HY]\d{2}[A-Z])")

# Scheme page markup: each classification entry is a
# <table class="classItem ..." id="SYMBOL"> block containing an optional
# indent-level cell and a <div class="class-title"> with the title text.
_ITEM_RE = re.compile(r'<table class="classItem[^"]*" id="([^"]+)">')
_INDENT_RE = re.compile(r'title="Indent level is (\d+)"')
_TITLE_RE = re.compile(r'<div class="class-title">(.*?)</div>', re.S)
_DATE_REVISED_RE = re.compile(r'<span class="date-revised">.*?</span>', re.S)
_TAG_RE = re.compile(r'<[^>]+>')


def normalize_cpc_symbol(code: str) -> str:
    """Normalize a CPC symbol: strip all whitespace, uppercase.

    "g01s 17/32" -> "G01S17/32"
    """
    return re.sub(r"\s+", "", code or "").upper()


def subclass_of(symbol: str) -> Optional[str]:
    """Return the 4-character subclass of a normalized CPC symbol, or None.

    "G01S17/32" -> "G01S"; "G01" (class only) -> None.
    """
    match = _SUBCLASS_RE.match(symbol)
    return match.group(1) if match else None


def parse_scheme_html(html_text: str) -> List[Dict[str, Any]]:
    """Parse a CPC scheme page into an ordered list of entries.

    Each entry is {"symbol": str, "indent": int, "title": str}, in document
    order (which is scheme order: parents precede their children).
    """
    entries: List[Dict[str, Any]] = []
    matches = list(_ITEM_RE.finditer(html_text))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html_text)
        block = html_text[match.start():end]
        indent_match = _INDENT_RE.search(block)
        indent = int(indent_match.group(1)) if indent_match else 0
        title_match = _TITLE_RE.search(block)
        title = ""
        if title_match:
            raw = _DATE_REVISED_RE.sub("", title_match.group(1))
            raw = _TAG_RE.sub("", raw)
            title = re.sub(r"\s+", " ", html_module.unescape(raw)).strip()
        entries.append({
            "symbol": match.group(1),
            "indent": indent,
            "title": title,
        })
    return entries


def build_hierarchy(
    entries: List[Dict[str, Any]], symbol: str
) -> Optional[List[Dict[str, Any]]]:
    """Build the parent chain of scheme entries ending at `symbol`.

    Walks backwards from the symbol's entry, collecting each entry with a
    smaller indent level (its parent), and prepends the subclass entry
    (e.g. "G01S") which sits at the top of every scheme page. Returns the
    chain ordered subclass-first, or None when the symbol is not present.
    """
    index = {entry["symbol"]: i for i, entry in enumerate(entries)}
    if symbol not in index:
        return None

    i = index[symbol]
    chain = [entries[i]]
    level = entries[i]["indent"]
    for j in range(i - 1, -1, -1):
        if level == 0:
            break
        if entries[j]["indent"] < level:
            chain.append(entries[j])
            level = entries[j]["indent"]
    chain.reverse()

    # The subclass entry ("G01S") also has indent 0, so the walk above stops
    # at the top-level main group ("G01S17/00"); prepend the subclass.
    subclass = subclass_of(symbol)
    if subclass and chain[0]["symbol"] != subclass and subclass in index:
        chain.insert(0, entries[index[subclass]])

    return chain


class CpcSchemeClient:
    """Client for USPTO's static CPC scheme pages on www.uspto.gov.

    Fetches one HTML page per subclass, parses every classification entry
    on it, and caches the parsed entries in memory for the process lifetime
    (the scheme changes only a few times a year).

    Supports context manager protocol for proper resource cleanup.
    """

    def __init__(self):
        self.headers = {"User-Agent": config.USER_AGENT}
        self.client = make_logged_client(self.headers)
        self._scheme_cache: Dict[str, List[Dict[str, Any]]] = {}

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit with cleanup."""
        await self.close()

    async def get_scheme_entries(
        self, subclass: str
    ) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
        """Fetch and parse the scheme page for a subclass (cached).

        Returns the parsed entry list, or an ApiError dictionary on failure.
        """
        cached = self._scheme_cache.get(subclass)
        if cached is not None:
            return cached

        url = CPC_SCHEME_URL_TEMPLATE.format(subclass=subclass)
        result = await request_bytes(
            self.client, url,
            context=f"CPC scheme fetch failed for subclass {subclass}",
        )
        if is_error(result):
            return result

        html_text = result["content"].decode("utf-8", errors="replace")
        entries = parse_scheme_html(html_text)
        if not entries:
            # Page came back but not in the expected shape (e.g. an HTML
            # error page). Don't cache; the next call may succeed.
            return ApiError.create(
                message=f"No CPC scheme entries parsed from {url}",
                error_code="CPC_SCHEME_PARSE_FAILED",
            )

        self._scheme_cache[subclass] = entries
        logger.info(f"Cached CPC scheme for {subclass}: {len(entries)} entries")
        return entries

    async def lookup(self, cpc_code: str) -> Dict[str, Any]:
        """Resolve a CPC symbol to its title and parent chain of titles.

        Args:
            cpc_code: Symbol at subclass level or deeper, e.g. "G01S",
                "G01S17/00", "G01S17/32". Whitespace and case are ignored.

        Returns:
            On success: {"symbol", "subclass", "subclass_title",
            "definition", "hierarchy": [{"symbol", "indent", "title"}, ...]}
            with the hierarchy ordered subclass-first.
            On failure: an ApiError dictionary.
        """
        symbol = normalize_cpc_symbol(cpc_code)
        subclass = subclass_of(symbol)
        if not subclass:
            return ApiError.create(
                message=(
                    f"'{cpc_code}' is not a full CPC symbol (need at least "
                    "a subclass like G01S)"
                ),
                error_code="CPC_SYMBOL_INVALID",
            )

        entries = await self.get_scheme_entries(subclass)
        if isinstance(entries, dict):
            return entries

        chain = build_hierarchy(entries, symbol)
        if chain is None:
            return ApiError.create(
                message=(
                    f"CPC symbol {symbol} not found in the current "
                    f"{subclass} scheme"
                ),
                error_code="CPC_SYMBOL_NOT_FOUND",
            )

        subclass_title = chain[0]["title"] if chain[0]["symbol"] == subclass else None
        return {
            "symbol": symbol,
            "subclass": subclass,
            "subclass_title": subclass_title,
            "definition": chain[-1]["title"],
            "hierarchy": chain,
        }

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
