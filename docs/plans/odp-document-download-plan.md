# ODP File Wrapper Document Download — Implementation Plan

**Date:** 2026-04-06
**Goal:** Add an MCP tool to download individual file wrapper documents (office actions, claims, specifications, etc.) from the USPTO ODP API using the existing authenticated client.

---

## Problem

`odp_get_documents` returns document metadata including download URLs, but no tool exists to actually download the files. The download URLs require the `X-API-KEY` header, which the `ApiUsptoClient` already provides — but its only request method (`make_request`) calls `response.json()`, which fails on binary content like PDFs.

**Evidence of authentication requirement:** Attempting to access download URLs via WebFetch and curl without the API key returns HTTP 403 Forbidden.

---

## Architecture

### Download URL Patterns (from `odp_get_documents` response)

```
PDF:  {API_BASE_URL}/api/v1/download/applications/{app_num}/{document_id}.pdf
DOCX: {API_BASE_URL}/api/v1/download/applications/{app_num}/{document_id}/files/{filename}.docx
XML:  {API_BASE_URL}/api/v1/download/applications/{app_num}/{document_id}/xmlarchive
```

Where:
- `API_BASE_URL` = `https://api.uspto.gov` (from `config.py` line 25)
- `app_num` = application number without slashes (e.g., `18533474`)
- `document_id` = document identifier from `odp_get_documents` response (e.g., `MM26RN3L138X163`)

### Existing Patterns to Follow

1. **Authentication:** `ApiUsptoClient` (api_uspto_gov.py:43-47) sets `X-API-KEY` header from `config.USPTO_API_KEY`
2. **Binary download:** `PpubsClient.download_image` (ppubs_uspto_gov.py:430-455) uses `response.aread()` for binary content
3. **Tool registration:** All tools use `@mcp.tool()` decorator in `patents.py`
4. **Validation:** `validate_app_number()` for app_num input
5. **Error handling:** `ApiError.create()` and `is_error()` pattern

---

## Changes Required

### File 1: `src/patent_mcp_server/uspto/api_uspto_gov.py`

Add a `download_file` method to `ApiUsptoClient`:

```python
@retry(
    stop=stop_after_attempt(config.MAX_RETRIES),
    wait=wait_exponential(
        multiplier=config.RETRY_DELAY,
        min=config.RETRY_MIN_WAIT,
        max=config.RETRY_MAX_WAIT
    ),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    reraise=True
)
async def download_file(self, url: str) -> Dict[str, Any]:
    """Download a file from the USPTO API and return raw bytes.

    Uses the same X-API-KEY authentication as make_request but handles
    binary content (PDFs, DOCX files) instead of JSON.

    Args:
        url: Full download URL (from odp_get_documents downloadUrl field)

    Returns:
        Dict with 'content' (bytes) and 'content_type' on success,
        or error dict on failure.
    """
    headers = {
        "User-Agent": config.USER_AGENT,
        "X-API-KEY": config.USPTO_API_KEY if config.USPTO_API_KEY else ""
    }

    logger.info(f"Downloading file from {url}")

    try:
        response = await self.client.get(
            url,
            headers=headers,
            timeout=config.REQUEST_TIMEOUT
        )

        response.raise_for_status()
        content = await response.aread()
        content_type = response.headers.get("content-type", "application/octet-stream")
        logger.info(f"Download successful: {len(content)} bytes, type={content_type}")

        return {
            "content": content,
            "content_type": content_type,
            "size_bytes": len(content)
        }

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.error(f"Download HTTP error: {status_code}")
        return ApiError.from_http_error(
            status_code=status_code,
            response_text=e.response.text
        )

    except (httpx.TimeoutException, httpx.NetworkError) as e:
        logger.warning(f"Network error during download (will retry): {str(e)}")
        raise  # Let tenacity handle the retry

    except Exception as e:
        logger.error(f"Unexpected download error: {str(e)}")
        return ApiError.from_exception(e, f"Download from {url} failed")
```

### File 2: `src/patent_mcp_server/patents.py`

Add `odp_download_document` tool after the existing `odp_get_documents` tool (after line 764):

```python
@mcp.tool()
async def odp_download_document(
    app_num: str,
    document_id: str,
) -> Dict[str, Any]:
    """Download a document from the application file wrapper as PDF.

    USE THIS TOOL WHEN: You need to read the actual content of a file
    wrapper document such as an office action, examiner interview summary,
    list of references cited (892 form), claims, specification, or any
    other document listed by odp_get_documents.

    WORKFLOW: First call odp_get_documents to list available documents and
    get document identifiers, then call this tool with the desired
    document_id to download and save the PDF.

    Args:
        app_num: Application number without slashes (e.g., "18533474")
        document_id: Document identifier from odp_get_documents response
            (e.g., "MM26RN3L138X163")

    Returns:
        Dictionary with file_path to the saved PDF on success.
        The caller can then read the PDF using standard file reading.
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    if not document_id or not document_id.strip():
        return ApiError.validation_error(
            "document_id is required", "document_id"
        )

    document_id = document_id.strip()

    url = f"{config.API_BASE_URL}/api/v1/download/applications/{app_num}/{document_id}.pdf"
    result = await api_client.download_file(url)

    if is_error(result):
        return result

    # Save to temp directory
    import os
    import tempfile

    download_dir = os.path.join(tempfile.gettempdir(), "patent_docs")
    os.makedirs(download_dir, exist_ok=True)

    filename = f"{app_num}_{document_id}.pdf"
    file_path = os.path.join(download_dir, filename)

    with open(file_path, "wb") as f:
        f.write(result["content"])

    return {
        "success": True,
        "file_path": file_path,
        "size_bytes": result["size_bytes"],
        "content_type": result["content_type"],
        "document_id": document_id,
        "application_number": app_num,
    }
```

### Import addition in `patents.py`

No new imports needed — `os` and `tempfile` are imported inline (following the pattern used by `ppubs_download_patent_pdf` which imports `base64` inline at line 448 of ppubs_uspto_gov.py).

---

## Testing Approach

After implementation, verify by:

1. Calling `odp_get_documents("18533474")` to get document list
2. Calling `odp_download_document("18533474", "MM26RN3L138X163")` to download the Non-Final Rejection
3. Verifying the saved PDF is readable via Claude's Read tool
4. Checking error handling by passing an invalid document_id

---

## Risk Assessment

- **Low risk:** The download URL pattern is deterministic and observed from actual API responses
- **Low risk:** Authentication uses the same X-API-KEY mechanism already working for all ODP tools
- **Low risk:** Binary download pattern is proven in `PpubsClient.download_image`
- **Possible issue:** Large documents may need an increased timeout (current default: 30s). The `REQUEST_TIMEOUT` config is already user-configurable via environment variable.
