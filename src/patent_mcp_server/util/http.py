"""
Shared HTTP plumbing for USPTO API clients (api.uspto.gov).

Provides the single retry policy, error-handling block, and 429 rate-limit
loop used by the ODP, PTAB, and DSAPI clients, plus the standard
httpx.AsyncClient factory. USPTO documents 60 requests/minute per API key
and 4 requests/minute for PDF/ZIP downloads.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Set, Tuple, Union
from urllib.parse import urljoin, urlparse

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
MAX_DOWNLOAD_REDIRECTS = 5
MAX_BINARY_DOWNLOAD_BYTES = 100 * 1024 * 1024
SENSITIVE_REDIRECT_HEADERS = (
    "Authorization",
    "Proxy-Authorization",
    "X-API-KEY",
    "X-Access-Token",
)

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
    follow_redirects: Optional[bool] = None,
    accept_redirect_response: bool = False,
    stream_response: bool = False,
) -> Union[httpx.Response, Dict[str, Any]]:
    """Send one request with retry, 429 handling, and standard error mapping.

    This function performs one network attempt; callers own the Tenacity retry
    boundary so JSON requests and complete streamed downloads each have exactly
    one retry layer. 429 responses are retried in-place after honoring
    Retry-After. All other failures are mapped to ApiError dictionaries.

    Returns:
        The successful httpx.Response, or an ApiError dictionary.
    """
    try:
        attempt = 0
        while True:
            redirect_option = (
                {"follow_redirects": follow_redirects}
                if follow_redirects is not None
                else {}
            )
            if stream_response:
                request = client.build_request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    data=form_data,
                    headers=headers,
                    timeout=config.REQUEST_TIMEOUT,
                )
                response = await client.send(
                    request,
                    stream=True,
                    **redirect_option,
                )
            else:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    data=form_data,
                    headers=headers,
                    timeout=config.REQUEST_TIMEOUT,
                    **redirect_option,
                )
            if response.status_code != 429:
                break
            if attempt >= config.MAX_RETRIES:
                await response.aclose()
                logger.error(
                    f"Rate limit (429) persisted after {config.MAX_RETRIES} "
                    f"retries: {url}"
                )
                return rate_limit_error()
            delay = rate_limit_delay(response, attempt)
            await response.aclose()
            logger.warning(f"Rate limited (429) on {url}; retrying in {delay:.0f}s")
            await asyncio.sleep(delay)
            attempt += 1

        if accept_redirect_response and response.is_redirect:
            return response
        if response.is_error and stream_response:
            body_bytes = await read_response_prefix(
                response,
                max_bytes=MAX_ERROR_BODY_CHARS * 4,
            )
            body = body_bytes.decode("utf-8", errors="replace")[:MAX_ERROR_BODY_CHARS]
            logger.error(f"HTTP error: {response.status_code} - {body}")
            try:
                error_json = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                error_json = None
            return ApiError.from_http_error(
                status_code=response.status_code,
                response_text=body,
                response_json=error_json if isinstance(error_json, dict) else None,
            )
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


_send_with_retry = uspto_retry(_send)


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
    result = await _send_with_retry(
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


@uspto_retry
async def request_bytes(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    context: str = "Download failed",
    allowed_redirect_hosts: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Download bounded binary content with the same retry/429 handling.

    Returns:
        Dict with 'content' (bytes), 'content_type', and 'size_bytes' on
        success, or an ApiError dictionary on failure.
    """
    current_url = url
    current_headers = dict(client.headers)
    if headers:
        current_headers.update(headers)
    initial_host = urlparse(url).hostname
    allowed_hosts = set(allowed_redirect_hosts or ())
    if initial_host:
        allowed_hosts.add(initial_host)

    for redirect_count in range(MAX_DOWNLOAD_REDIRECTS + 1):
        result = await _send(
            client,
            HTTPMethods.GET,
            current_url,
            headers=current_headers,
            context=context,
            follow_redirects=False,
            accept_redirect_response=True,
            stream_response=True,
        )
        if isinstance(result, dict):
            return result

        if not result.is_redirect:
            break
        if redirect_count >= MAX_DOWNLOAD_REDIRECTS:
            await result.aclose()
            return ApiError.create(
                "USPTO download exceeded the redirect limit",
                error_code="TOO_MANY_REDIRECTS",
            )
        location = result.headers.get("location")
        if not location:
            await result.aclose()
            return ApiError.create(
                "USPTO download redirect did not include a location",
                error_code="INVALID_REDIRECT",
            )
        try:
            current_url, current_headers = prepare_safe_redirect(
                current_url,
                location,
                current_headers,
                allowed_hosts=allowed_hosts,
            )
        except ValueError as exc:
            await result.aclose()
            return ApiError.create(
                str(exc),
                error_code="UNSAFE_REDIRECT",
            )
        await result.aclose()
    else:  # pragma: no cover - loop always breaks or returns
        return ApiError.create(
            "USPTO download redirect handling failed",
            error_code="INVALID_REDIRECT",
        )

    content_type = result.headers.get("content-type", "application/octet-stream")
    bounded = await read_bounded_response(
        result,
        max_bytes=MAX_BINARY_DOWNLOAD_BYTES,
    )
    if isinstance(bounded, dict):
        return bounded
    content = bounded
    logger.info(f"Download successful: {len(content)} bytes, type={content_type}")
    return {
        "content": content,
        "content_type": content_type,
        "size_bytes": len(content),
    }


async def read_bounded_response(
    response: httpx.Response,
    *,
    max_bytes: int,
) -> Union[bytes, Dict[str, Any]]:
    """Read a streaming response without buffering beyond ``max_bytes``."""
    if max_bytes <= 0:
        await response.aclose()
        return ApiError.create(
            "Download size limit must be positive",
            error_code="INVALID_DOWNLOAD_LIMIT",
        )

    content_length = response.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                await response.aclose()
                return ApiError.create(
                    f"USPTO download exceeds the {max_bytes}-byte file size limit",
                    error_code="DOWNLOAD_TOO_LARGE",
                )
        except ValueError:
            pass

    content = bytearray()
    try:
        async for chunk in response.aiter_bytes():
            if len(content) + len(chunk) > max_bytes:
                return ApiError.create(
                    f"USPTO download exceeds the {max_bytes}-byte file size limit",
                    error_code="DOWNLOAD_TOO_LARGE",
                )
            content.extend(chunk)
    finally:
        await response.aclose()
    return bytes(content)


async def read_response_prefix(
    response: httpx.Response,
    *,
    max_bytes: int,
) -> bytes:
    """Read at most ``max_bytes`` from a streaming response, then close it."""
    if max_bytes <= 0:
        await response.aclose()
        return b""
    prefix = bytearray()
    try:
        async for chunk in response.aiter_bytes():
            remaining = max_bytes - len(prefix)
            if remaining <= 0:
                break
            prefix.extend(chunk[:remaining])
            if len(prefix) >= max_bytes:
                break
    finally:
        await response.aclose()
    return bytes(prefix)


def prepare_safe_redirect(
    current_url: str,
    location: str,
    headers: Dict[str, str],
    *,
    allowed_hosts: Set[str],
) -> Tuple[str, Dict[str, str]]:
    """Validate a download redirect and strip credentials cross-origin."""
    next_url = urljoin(current_url, location)
    current = urlparse(current_url)
    target = urlparse(next_url)
    if target.scheme != "https" or target.username or target.password:
        raise ValueError("USPTO download redirect must use credential-free HTTPS")
    if target.hostname not in allowed_hosts or target.port not in (None, 443):
        raise ValueError("USPTO download redirect used an unapproved host")

    next_headers = dict(headers)
    if (current.scheme, current.netloc) != (target.scheme, target.netloc):
        sensitive_names = {header.lower() for header in SENSITIVE_REDIRECT_HEADERS}
        for header in list(next_headers):
            if header.lower() in sensitive_names:
                next_headers.pop(header)
        for header in SENSITIVE_REDIRECT_HEADERS:
            next_headers[header] = ""
    return next_url, next_headers
