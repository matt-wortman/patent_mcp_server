"""
Response normalization utilities for consistent API responses.

This module provides utilities for:
- Standardizing response format across different backends
- Reply size limits and response truncation
- Response envelope creation

Reply size limits: Claude Code (identified by the client name it sends in
MCP initialize) may receive replies up to ``CLAUDE_CODE_MAX_RESPONSE_CHARS``
characters of the indented JSON text actually sent, because every tool
declares a 500,000-character result size to it. Every other client, and any
call made outside an MCP request, keeps the ``MAX_RESPONSE_TOKENS`` estimate.
A list reply over the limit is cut to its first rows with every row saved to
disk (for Claude Code, as many rows as fit, otherwise 20), and a
single-record `results` reply over the limit is saved to disk.
A flat record with no `results` envelope is always returned whole.
"""

import html
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import pydantic_core
from mcp.server.lowlevel.server import request_ctx

from patent_mcp_server.config import config

logger = logging.getLogger('response_util')


class ResponseEnvelope:
    """Standard response envelope for all API responses."""

    @staticmethod
    def success(
        results: Union[List[Any], Dict[str, Any], Any],
        source: str,
        count: Optional[int] = None,
        total: Optional[int] = None,
        offset: int = 0,
        limit: int = 100,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a standardized success response.

        Args:
            results: The actual results data
            source: Data source identifier (e.g., 'ppubs', 'odp', 'ptab', 'dsapi')
            count: Number of results in this response
            total: Total available results (for pagination)
            offset: Current offset position
            limit: Requested limit
            metadata: Optional source-specific metadata

        Returns:
            Standardized response dictionary
        """
        # Calculate count if not provided
        if count is None:
            if isinstance(results, list):
                count = len(results)
            elif isinstance(results, dict):
                count = 1
            else:
                count = 1 if results is not None else 0

        # Calculate has_more
        if total is not None:
            has_more = (offset + count) < total
        else:
            has_more = False
            total = count

        response = {
            "success": True,
            "source": source,
            "count": count,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
            "results": results,
        }

        if metadata:
            response["metadata"] = metadata

        return response

    @staticmethod
    def no_matches(
        source: str,
        offset: int = 0,
        limit: int = 25,
    ) -> Dict[str, Any]:
        """Create the response for a search that matched no records.

        ODP and PTAB report zero matches as HTTP 404 "Not Found"; search
        tools convert that into this empty result so callers do not mistake
        it for a failure.

        Args:
            source: Data source identifier (e.g., 'odp', 'ptab')
            offset: Requested offset
            limit: Requested limit

        Returns:
            Standardized empty response with an explanatory message
        """
        response = ResponseEnvelope.success(
            results=[],
            source=source,
            count=0,
            total=0,
            offset=offset,
            limit=limit,
        )
        response["message"] = (
            "No records matched this search. USPTO reports zero matches as "
            "'Not Found', so check spelling and the exact form of names and "
            "phrases (field values are matched as stored). For search tools, "
            "query_sent shows the search exactly as USPTO received it."
        )
        return response

    @staticmethod
    def from_ppubs(
        raw_response: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Normalize a PPUBS API response.

        Args:
            raw_response: Raw response from PPUBS API
            offset: Requested offset
            limit: Requested limit

        Returns:
            Standardized response
        """
        # PPUBS format: {numFound, perPage, page, patents: [...]}
        results = raw_response.get("patents", [])
        total = raw_response.get("numFound", len(results))

        return ResponseEnvelope.success(
            results=results,
            source="ppubs",
            count=len(results),
            total=total,
            offset=offset,
            limit=limit,
            metadata={
                "per_page": raw_response.get("perPage"),
                "page": raw_response.get("page"),
                "total_pages": raw_response.get("totalPages"),
            }
        )

    @staticmethod
    def from_odp(
        raw_response: Dict[str, Any],
        offset: int = 0,
        limit: int = 25,
    ) -> Dict[str, Any]:
        """Normalize an ODP (api.uspto.gov) API response.

        Args:
            raw_response: Raw response from ODP API
            offset: Requested offset
            limit: Requested limit

        Returns:
            Standardized response
        """
        # ODP format varies: {count, patentFileWrapperDataBag: [...]} or direct data
        if "patentFileWrapperDataBag" in raw_response:
            results = raw_response.get("patentFileWrapperDataBag", [])
            total = raw_response.get("count", len(results))
        elif "petitionDecisionDataBag" in raw_response:
            results = raw_response.get("petitionDecisionDataBag", [])
            total = raw_response.get("count", len(results))
        elif "officeActionDataBag" in raw_response:
            results = raw_response.get("officeActionDataBag", [])
            total = raw_response.get("count", len(results))
        elif "results" in raw_response:
            results = raw_response.get("results", [])
            total = raw_response.get("count", len(results))
        else:
            # Single result or direct data
            results = raw_response
            total = 1

        return ResponseEnvelope.success(
            results=results,
            source="odp",
            count=len(results) if isinstance(results, list) else 1,
            total=total,
            offset=offset,
            limit=limit,
        )

    @staticmethod
    def from_ptab(
        raw_response: Dict[str, Any],
        offset: int = 0,
        limit: int = 25,
    ) -> Dict[str, Any]:
        """Normalize a PTAB API response.

        Args:
            raw_response: Raw response from PTAB API
            offset: Requested offset
            limit: Requested limit

        Returns:
            Standardized response
        """
        # PTAB API v3 (migrated to ODP) returns data under endpoint-specific
        # DataBag keys, similar to ODP's patentFileWrapperDataBag pattern.
        # Search the known PTAB response keys first, then fall back to generic keys.
        _PTAB_RESPONSE_KEYS = [
            "patentTrialProceedingDataBag",  # /trials/proceedings/search
            "patentTrialDecisionDataBag",    # /trials/decisions/search
            "patentTrialDocumentDataBag",    # /trials/documents endpoints
            "patentAppealDataBag",           # /appeals decisions endpoints
            "patentInterferenceDataBag",     # /interferences decisions endpoints
            # Generic fallbacks (legacy or future format changes)
            "results",
            "data",
        ]
        results = []
        for key in _PTAB_RESPONSE_KEYS:
            val = raw_response.get(key)
            if val is not None:
                results = val
                break
        total = raw_response.get("total", raw_response.get("count", len(results) if isinstance(results, list) else 1))

        return ResponseEnvelope.success(
            results=results,
            source="ptab",
            count=len(results) if isinstance(results, list) else 1,
            total=total,
            offset=offset,
            limit=limit,
        )


def estimate_tokens(data: Any) -> int:
    """Estimate token count for a data structure.

    Uses rough approximation of 4 characters per token.

    Args:
        data: Data to estimate tokens for

    Returns:
        Estimated token count
    """
    if data is None:
        return 0

    try:
        json_str = json.dumps(data, default=str)
        return len(json_str) // 4
    except (TypeError, ValueError):
        return len(str(data)) // 4


def _current_client_name() -> Optional[str]:
    """Return the MCP client's self-reported name for the current request.

    Reads ``clientInfo.name`` from the session's initialize parameters.
    Returns None outside an MCP request (for example in unit tests) or when
    the client sent no initialize parameters.
    """
    try:
        ctx = request_ctx.get()
    except LookupError:
        return None
    try:
        client_params = ctx.session.client_params
        if client_params is None:
            return None
        name = client_params.clientInfo.name
    except AttributeError:
        return None
    return name if isinstance(name, str) else None


def _sent_text_length(response: Dict[str, Any]) -> int:
    """Length of the text FastMCP sends for this reply.

    FastMCP turns a dict reply into text with this exact call
    (``mcp/server/fastmcp/utilities/func_metadata.py``). It also sends the
    reply as structured content, and Claude Code 2.1.316 shows the agent that
    structured form as compact JSON, which was about 30% shorter in live
    checks. The indented text is never the shorter of the two, so measuring
    it keeps every reply inside what Claude Code shows inline.
    """
    return len(pydantic_core.to_json(response, fallback=str, indent=2).decode())


def _over_inline_limit(response: Dict[str, Any], max_tokens: Optional[int]) -> bool:
    """Decide whether a reply is too large to send inline.

    - An explicit ``max_tokens`` from the caller: today's token estimate
      against that value.
    - Otherwise, a client named in ``config.LARGE_REPLY_CLIENTS`` (Claude
      Code): the length of the indented text actually sent against
      ``config.CLAUDE_CODE_MAX_RESPONSE_CHARS``.
    - Otherwise: the token estimate against ``config.MAX_RESPONSE_TOKENS``.
    """
    if max_tokens:
        return estimate_tokens(response) > max_tokens
    if _uses_char_limit(max_tokens):
        return _sent_text_length(response) > config.CLAUDE_CODE_MAX_RESPONSE_CHARS
    return estimate_tokens(response) > config.MAX_RESPONSE_TOKENS


def _uses_char_limit(max_tokens: Optional[int]) -> bool:
    """True when this reply is checked against the Claude Code character limit.

    That is when the caller gave no explicit ``max_tokens`` and the current
    client's name is in ``config.LARGE_REPLY_CLIENTS``.
    """
    if max_tokens:
        return False
    name = _current_client_name()
    large_clients = {client.lower() for client in config.LARGE_REPLY_CLIENTS}
    return name is not None and name.lower() in large_clients


def _first_rows_reply(
    response: Dict[str, Any], rows_kept: int, file_path: str, next_offset: int
) -> Dict[str, Any]:
    """Build a reply holding the first ``rows_kept`` rows, naming the full file."""
    original_count = len(response["results"])
    reply = response.copy()
    reply["results"] = response["results"][:rows_kept]
    reply["_truncated"] = True
    reply["_original_count"] = original_count
    reply["_truncated_to"] = rows_kept
    reply["count"] = rows_kept
    reply["_oversized_file"] = file_path
    reply["_truncation_message"] = (
        f"This reply holds the first {rows_kept} of {original_count} results on "
        f"this page, as many as fit Claude Code's reply size limit. All "
        f"{original_count} results are saved in full at {file_path}. Read that "
        f"file for the rest. The next page starts at offset {next_offset}."
    )
    return reply


def _truncate_list_to_fit(response: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Claude Code only: keep as many leading rows as fit the character limit.

    Every row is saved to disk and the reply names the file and says how many
    of how many rows it holds. If not even one row fits, the full payload is
    saved and `results` is replaced by the summary envelope, as for other
    clients. The reply is measured with every added field in place.
    """
    rows = response["results"]
    limit = config.CLAUDE_CODE_MAX_RESPONSE_CHARS
    next_offset = (response.get("offset") or 0) + len(rows)
    # Same length as the real file name: _write_oversized_file uses a
    # 13-digit millisecond timestamp.
    placeholder = os.path.join(
        config.DOWNLOAD_DIR, f"oversized_{source or 'unknown'}_{'9' * 13}.json"
    )

    def fits(rows_kept: int, file_path: str) -> bool:
        reply = _first_rows_reply(response, rows_kept, file_path, next_offset)
        return _sent_text_length(reply) <= limit

    # The whole list did not fit, so the answer is below len(rows).
    low, high = 0, len(rows) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle, placeholder):
            low = middle
        else:
            high = middle - 1

    if low == 0:
        return _save_oversized_to_disk(response, source)

    file_path, _ = _write_oversized_file(response, source)
    while low > 0 and not fits(low, file_path):  # guard against a longer path
        low -= 1
    if low == 0:
        os.remove(file_path)
        return _save_oversized_to_disk(response, source)

    logger.info(
        f"Kept the first {low} of {len(rows)} results for Claude Code "
        f"(reply size limit {limit} characters)"
    )
    return _first_rows_reply(response, low, file_path, next_offset)


def _write_oversized_file(response: Dict[str, Any], source: str) -> Tuple[str, int]:
    """Write the full response as JSON to the download folder.

    The file is ``{config.DOWNLOAD_DIR}/oversized_{source}_{timestamp}.json``.

    Returns:
        The file path and its size in bytes
    """
    download_dir = config.DOWNLOAD_DIR
    os.makedirs(download_dir, exist_ok=True)

    timestamp = int(time.time() * 1000)
    safe_source = source or "unknown"
    filename = f"oversized_{safe_source}_{timestamp}.json"
    file_path = os.path.join(download_dir, filename)

    try:
        serialized = json.dumps(response, default=str, indent=2)
    except (TypeError, ValueError):
        serialized = str(response)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(serialized)

    return file_path, len(serialized.encode("utf-8"))


def _save_oversized_to_disk(
    response: Dict[str, Any],
    source: str,
) -> Dict[str, Any]:
    """Serialize an oversized response to disk and return a summary envelope.

    Writes the full response with ``_write_oversized_file`` and returns a
    small summary that replaces the `results` payload so the caller stays
    under the token budget.
    """
    file_path, size_bytes = _write_oversized_file(response, source)
    est = estimate_tokens(response)

    results = response.get("results")
    if isinstance(results, dict):
        preview_keys = sorted(results.keys())[:20]
        shape = "dict"
        record_count = 1
    elif isinstance(results, list):
        preview_keys = []
        shape = "list"
        record_count = len(results)
    else:
        preview_keys = []
        shape = type(results).__name__
        record_count = 0 if results is None else 1

    summary = {
        "_oversized": True,
        "_shape": shape,
        "file_path": file_path,
        "size_bytes": size_bytes,
        "estimated_tokens": est,
        "record_count": record_count,
        "preview_keys": preview_keys,
        "_message": (
            "Response exceeded token budget; full payload saved to disk. "
            "Read the file for complete data."
        ),
    }

    logger.info(
        f"Saved oversized response ({est} est. tokens, {size_bytes} bytes) "
        f"to {file_path}"
    )

    truncated = response.copy()
    truncated["results"] = summary
    truncated["_oversized"] = True
    truncated["_oversized_file"] = file_path
    # Keep the envelope's count: it describes the records saved on disk,
    # which is what a caller paginating through results needs to know.
    return truncated


def truncate_response(
    response: Dict[str, Any],
    max_tokens: Optional[int] = None,
    max_results: int = 20,
) -> Dict[str, Any]:
    """Truncate a response if it is over the inline size limit.

    The limit depends on the client (see ``_over_inline_limit``): Claude Code
    gets a character limit on the text actually sent, every other client the
    token estimate. An explicit ``max_tokens`` always uses the token estimate.

    Over the limit:
    1. List-shaped `results`:
       - Claude Code → keep as many leading rows as fit its character limit,
         save every row to disk, and say in the reply how many of how many
         rows it holds and where the file is.
       - Other clients → if over max_results, keep the first max_results
         inline and save the full payload to disk, naming the file.
       In either case, if no slice fits (each item is huge, or for other
       clients the list is already short), save the full payload to disk and
       replace `results` with a summary envelope.
    2. Dict-shaped `results` (single record) → save the full payload to disk
       and replace `results` with a summary envelope.
    3. No `results` key (a flat record, such as a PPUBS patent document) →
       return it unchanged. Every field would stay inline anyway, so a disk
       copy would only add a false "saved to disk" notice. A `results` value
       of any other type is saved to disk and replaced by the summary.

    Args:
        response: Response dictionary to potentially truncate
        max_tokens: Explicit token limit (default: the client-aware limit)
        max_results: Maximum results to keep when truncating (clients other
            than Claude Code)

    Returns:
        Original or truncated response
    """
    if not _over_inline_limit(response, max_tokens):
        return response

    source = response.get("source", "unknown")

    # Case 1: list-shaped results — try slicing first
    if "results" in response and isinstance(response["results"], list):
        # Claude Code: as many rows as fit, not a fixed max_results.
        if _uses_char_limit(max_tokens):
            return _truncate_list_to_fit(response, source)

        truncated = response.copy()
        original_count = len(truncated["results"])
        if original_count > max_results:
            truncated["results"] = truncated["results"][:max_results]
            truncated["_truncated"] = True
            truncated["_original_count"] = original_count
            truncated["_truncated_to"] = max_results
            truncated["count"] = max_results
            logger.info(
                f"Truncated response from {original_count} to {max_results} results "
                f"(over the inline size limit)"
            )

            # Re-check: if still oversized, fall through to disk-save.
            # Save the ORIGINAL response — the summary promises the full
            # payload is on disk, so saving the sliced copy loses records.
            if _over_inline_limit(truncated, max_tokens):
                return _save_oversized_to_disk(response, source)

            # The preview stays inline, but every row also goes to disk: the
            # envelope's offset, limit and has_more still describe the whole
            # page, so rows cut here are otherwise skipped by the next page.
            file_path, _ = _write_oversized_file(response, source)
            next_offset = (response.get("offset") or 0) + original_count
            truncated["_oversized_file"] = file_path
            truncated["_truncation_message"] = (
                f"Showing the first {max_results} of {original_count} results to fit "
                f"the token budget. All {original_count} results are saved in full at "
                f"{file_path}; read that file for the rest. The next page starts at "
                f"offset {next_offset}."
            )
            return truncated

        # List is short but still oversized — items themselves are huge.
        return _save_oversized_to_disk(response, source)

    # Case 2: dict-shaped results (single record) — save to disk
    if "results" in response and isinstance(response["results"], dict):
        return _save_oversized_to_disk(response, source)

    # Case 3: no `results` key at all (a flat record). A disk save here would
    # keep every field inline and only add a `results` summary falsely
    # claiming the payload was moved to disk, so return it whole.
    if "results" not in response:
        return response

    # A `results` value of some other type (a long string, say) is replaced
    # by the summary, so the payload really does move to disk.
    return _save_oversized_to_disk(response, source)


def check_and_truncate(
    response: Dict[str, Any],
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Check response size and truncate if necessary.

    This is the main function to call before returning responses. The size
    limit is client-aware; see ``truncate_response``.

    Args:
        response: Response to check
        max_tokens: Optional explicit token limit (overrides the client-aware limit)

    Returns:
        Original or truncated response
    """
    if not config.TRUNCATE_LARGE_RESPONSES:
        return response

    return truncate_response(response, max_tokens)


# Identifying fields kept alongside the claims in a claims-only document.
CLAIMS_ONLY_FIELDS = (
    "guid",
    "publicationReferenceDocumentNumber",
    "inventionTitle",
    "datePublished",
    "applicationNumber",
    "assigneeName",
    "inventorsName",
    "numberOfClaims",
    "exemplaryClaimNumber",
)

_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def claims_html_to_text(claims_html: Optional[str]) -> str:
    """Convert PPUBS claimsHtml to plain text, one line per <br /> break.

    PPUBS marks each claim number in bold and separates claims with <br />,
    e.g. '<b>1</b>. A method ...  <br /> <b>2</b>. The method of claim 1 ...'.
    """
    if not claims_html:
        return ""
    lines = (
        " ".join(html.unescape(_TAG_RE.sub("", part)).split())
        for part in _BREAK_RE.split(claims_html)
    )
    return "\n".join(line for line in lines if line)


def claims_only_document(document: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a full PPUBS document to its identifying fields and claim text.

    A full document often runs past 100,000 characters (description and
    cited references); the claims are usually a few thousand.
    """
    reduced = {field: document.get(field) for field in CLAIMS_ONLY_FIELDS}
    reduced["claims"] = claims_html_to_text(document.get("claimsHtml"))
    return reduced
