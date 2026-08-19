"""
Shared HTTP plumbing for USPTO API clients (api.uspto.gov).

Provides the single retry policy, error-handling block, and 429 rate-limit
loop used by the ODP, PTAB, and DSAPI clients, plus the standard
httpx.AsyncClient factory. USPTO documents 60 requests/minute per API key
and 4 requests/minute for PDF/ZIP downloads.
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Union

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from patent_mcp_server.util.logging import LoggingTransport
from patent_mcp_server.util.errors import ApiError
from patent_mcp_server.config import config
from patent_mcp_server.constants import Defaults, HTTPMethods

logger = logging.getLogger('uspto_http')

# Never sleep longer than this on a single 429, regardless of Retry-After
MAX_RATE_LIMIT_SLEEP = 60.0

# Cap error bodies copied into logs and error dicts. Upstream HTML error
# pages can be hundreds of KB; the first KB carries the useful part.
MAX_ERROR_BODY_CHARS = 1000

# The single network-retry policy shared by every USPTO client, including
# the PPUBS client's own make_request.
uspto_retry = retry(
    stop=stop_after_attempt(config.MAX_RETRIES),
    wait=wait_exponential(
        multiplier=config.RETRY_DELAY,
        min=config.RETRY_MIN_WAIT,
        max=config.RETRY_MAX_WAIT,
    ),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    reraise=True,
)


def make_logged_client(headers: Dict[str, str]) -> httpx.AsyncClient:
    """Create the standard async HTTP client used by all USPTO API clients.

    Includes the request/response logging transport, HTTP/2, redirect
    following, and the configured timeout.

    Args:
        headers: Default headers (User-Agent, X-API-KEY, etc.)

    Returns:
        Configured httpx.AsyncClient
    """
    return httpx.AsyncClient(
        headers=headers,
        http2=True,
        follow_redirects=True,
        transport=LoggingTransport(httpx.AsyncHTTPTransport()),
        timeout=config.REQUEST_TIMEOUT,
    )


def rate_limit_delay(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying a 429 response, capped at MAX_RATE_LIMIT_SLEEP.

    Reads Retry-After, then x-rate-limit-retry-after-seconds; falls back to
    a growing default when neither header is present or parseable.
    """
    for header in ("Retry-After", "x-rate-limit-retry-after-seconds"):
        value = response.headers.get(header)
        if value:
            try:
                return min(float(value), MAX_RATE_LIMIT_SLEEP)
            except ValueError:
                continue
    return min(float(Defaults.RATE_LIMIT_RETRY_DELAY * (attempt + 1)), MAX_RATE_LIMIT_SLEEP)


def rate_limit_error() -> Dict[str, Any]:
    """Fresh RATE_LIMITED error dict for a 429 that survived all retries."""
    return ApiError.create(
        message=(
            "USPTO rate limit reached (HTTP 429) and still active after "
            f"{config.MAX_RETRIES} retries. Wait a minute and try again — "
            "the API allows 60 requests/minute (4/minute for downloads)."
        ),
        status_code=429,
        error_code="RATE_LIMITED",
    )


@uspto_retry
async def _send(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    form_data: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    context: str = "Request failed",
) -> Union[httpx.Response, Dict[str, Any]]:
    """Send one request with retry, 429 handling, and standard error mapping.

    Network errors (timeouts, connection failures) are retried by tenacity.
    429 responses are retried in-place after honoring Retry-After. All other
    failures are mapped to ApiError dictionaries.

    Returns:
        The successful httpx.Response, or an ApiError dictionary.
    """
    try:
        attempt = 0
        while True:
            response = await client.request(
                method,
                url,
                params=params,
                json=json_body,
                data=form_data,
                headers=headers,
                timeout=config.REQUEST_TIMEOUT,
            )
            if response.status_code != 429:
                break
            if attempt >= config.MAX_RETRIES:
                logger.error(f"Rate limit (429) persisted after {config.MAX_RETRIES} retries: {url}")
                return rate_limit_error()
            delay = rate_limit_delay(response, attempt)
            logger.warning(f"Rate limited (429) on {url}; retrying in {delay:.0f}s")
            await asyncio.sleep(delay)
            attempt += 1

        response.raise_for_status()
        return response

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        body = e.response.text[:MAX_ERROR_BODY_CHARS]
        logger.error(f"HTTP error: {status_code} - {body}")
        try:
            error_json = e.response.json()
            return ApiError.from_http_error(
                status_code=status_code,
                response_text=body,
                response_json=error_json,
            )
        except Exception:
            return ApiError.from_http_error(
                status_code=status_code,
                response_text=body,
            )

    except (httpx.TimeoutException, httpx.NetworkError) as e:
        logger.warning(f"Network error (will retry): {str(e)}")
        raise  # Let tenacity handle the retry

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return ApiError.from_exception(e, context)


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    form_data: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    context: str = "Request failed",
) -> Dict[str, Any]:
    """Make a request and parse the JSON response.

    Returns:
        Parsed response JSON, or an ApiError dictionary on any failure.
        A non-dict JSON body (e.g. a top-level array) is wrapped as
        {"results": ...} so callers can always treat the result as a dict.
    """
    result = await _send(
        client,
        method,
        url,
        params=params,
        json_body=json_body,
        form_data=form_data,
        headers=headers,
        context=context,
    )
    if isinstance(result, dict):
        return result

    try:
        parsed = result.json()
    except Exception as e:
        logger.error(f"Failed to parse JSON response: {str(e)}")
        return ApiError.from_exception(e, context)

    if isinstance(parsed, dict):
        return parsed
    return {"results": parsed}


async def request_bytes(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    context: str = "Download failed",
) -> Dict[str, Any]:
    """Download binary content (PDFs, DOCX) with the same retry/429 handling.

    Returns:
        Dict with 'content' (bytes), 'content_type', and 'size_bytes' on
        success, or an ApiError dictionary on failure.
    """
    result = await _send(client, HTTPMethods.GET, url, headers=headers, context=context)
    if isinstance(result, dict):
        return result

    content = result.content
    content_type = result.headers.get("content-type", "application/octet-stream")
    logger.info(f"Download successful: {len(content)} bytes, type={content_type}")
    return {
        "content": content,
        "content_type": content_type,
        "size_bytes": len(content),
    }
