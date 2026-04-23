"""
Response normalization utilities for consistent API responses.

This module provides utilities for:
- Standardizing response format across different backends
- Token budget awareness and response truncation
- Response envelope creation
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Union

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
            "patentTrialProceedingDataBag",     # /proceedings/search
            "patentTrialDecisionDataBag",       # /decisions/search
            "patentTrialDocumentDataBag",       # /proceedings/{id}/documents
            "patentTrialAppealDecisionDataBag", # /appeals/decisions/search
            "patentTrialInterferenceDataBag",   # /interferences/search
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


def _save_oversized_to_disk(
    response: Dict[str, Any],
    source: str,
) -> Dict[str, Any]:
    """Serialize an oversized response to disk and return a summary envelope.

    Writes the full response as JSON to
    ``{config.DOWNLOAD_DIR}/oversized_{source}_{timestamp}.json`` and returns
    a small summary that replaces the `results` payload so the caller stays
    under the token budget.
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

    size_bytes = len(serialized.encode("utf-8"))
    est = estimate_tokens(response)

    results = response.get("results")
    if isinstance(results, dict):
        preview_keys = sorted(results.keys())[:20]
        shape = "dict"
    elif isinstance(results, list):
        preview_keys = []
        shape = "list"
    else:
        preview_keys = []
        shape = type(results).__name__

    summary = {
        "_oversized": True,
        "_shape": shape,
        "file_path": file_path,
        "size_bytes": size_bytes,
        "estimated_tokens": est,
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
    # Overwrite count to reflect the summary (single envelope)
    truncated["count"] = 1
    return truncated


def truncate_response(
    response: Dict[str, Any],
    max_tokens: Optional[int] = None,
    max_results: int = 20,
) -> Dict[str, Any]:
    """Truncate a response if it exceeds token budget.

    Handles three cases:
    1. List-shaped `results` over max_results → slice to max_results.
    2. Dict-shaped `results` (single record) still over budget → save full
       payload to disk, replace `results` with a summary envelope.
    3. List still oversized after slicing (each item is huge) → save full
       payload to disk, replace `results` with a summary envelope.

    Args:
        response: Response dictionary to potentially truncate
        max_tokens: Maximum tokens allowed (default from config)
        max_results: Maximum results to keep when truncating

    Returns:
        Original or truncated response
    """
    max_tokens = max_tokens or config.MAX_RESPONSE_TOKENS

    estimated_tokens = estimate_tokens(response)

    if estimated_tokens <= max_tokens:
        return response

    source = response.get("source", "unknown")

    # Case 1: list-shaped results — try slicing first
    if "results" in response and isinstance(response["results"], list):
        truncated = response.copy()
        original_count = len(truncated["results"])
        if original_count > max_results:
            truncated["results"] = truncated["results"][:max_results]
            truncated["_truncated"] = True
            truncated["_original_count"] = original_count
            truncated["_truncated_to"] = max_results
            truncated["_truncation_message"] = (
                f"Response truncated from {original_count} to {max_results} results "
                f"to fit within token budget. Use 'offset' parameter to paginate "
                f"through remaining results."
            )
            truncated["count"] = max_results
            logger.info(
                f"Truncated response from {original_count} to {max_results} results "
                f"(estimated {estimated_tokens} tokens exceeded {max_tokens} limit)"
            )

            # Re-check: if still oversized, fall through to disk-save
            if estimate_tokens(truncated) > max_tokens:
                return _save_oversized_to_disk(truncated, source)

            return truncated

        # List is short but still oversized — items themselves are huge.
        return _save_oversized_to_disk(response, source)

    # Case 2: dict-shaped results (single record) — save to disk
    if "results" in response and isinstance(response["results"], dict):
        return _save_oversized_to_disk(response, source)

    # Case 3: no recognizable results envelope but oversized — also disk-save
    # so nothing blows the context window unnoticed.
    return _save_oversized_to_disk(response, source)


def check_and_truncate(
    response: Dict[str, Any],
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Check response size and truncate if necessary.

    This is the main function to call before returning responses.

    Args:
        response: Response to check
        max_tokens: Optional token limit override

    Returns:
        Original or truncated response
    """
    if not config.TRUNCATE_LARGE_RESPONSES:
        return response

    return truncate_response(response, max_tokens)
