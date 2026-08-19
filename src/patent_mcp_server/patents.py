"""
USPTO Patent Search MCP Server

This file provides a Model Context Protocol (MCP) server that exposes tools for interacting
with multiple USPTO patent data APIs:

1. ppubs.uspto.gov - Full text patent documents, PDF downloads, and advanced search
2. api.uspto.gov - Metadata, continuity information, transactions, and assignments
3. PTAB API v3 - Patent Trial and Appeal Board proceedings and decisions
4. DSAPI (api.uspto.gov) - Office actions, enriched citations, litigation, status codes

The server uses stdio transport for Claude Code/Cursor integration.

The package version lives in pyproject.toml and is exposed at runtime as
patent_mcp_server.config.PACKAGE_VERSION.
"""
import atexit
import json
import logging
import re
import sys
from typing import Any, Dict, Optional, Union

from mcp.server.fastmcp import FastMCP

from patent_mcp_server.config import config
from patent_mcp_server.constants import (
    Sources, Fields, PTABTrialTypes
)
from patent_mcp_server.util.errors import ApiError, is_error
from patent_mcp_server.util.validation import validate_patent_number, validate_app_number
from patent_mcp_server.util.response import (
    ResponseEnvelope, check_and_truncate
)
from patent_mcp_server.resources import (
    get_cpc_section_info, get_cpc_subsection_info,
    get_data_source_info, get_all_data_sources,
    get_search_syntax_guide, CPC_SECTIONS
)
from patent_mcp_server.prompts import get_prompt
from patent_mcp_server.uspto.ppubs_uspto_gov import PpubsClient
from patent_mcp_server.uspto.api_uspto_gov import ApiUsptoClient
from patent_mcp_server.uspto.ptab_client import PTABClient
from patent_mcp_server.uspto.dsapi_client import DsapiClient

# Initialize FastMCP server
mcp = FastMCP("uspto_patent_tools")

# Set up logging with configured level
logging.basicConfig(
    level=config.get_log_level(),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger('uspto_patent_mcp')

# Validate configuration
config.validate()

# Create client instances for each USPTO API
ppubs_client = PpubsClient()
api_client = ApiUsptoClient()
ptab_client = PTABClient()

# Create DSAPI client (api.uspto.gov — uses USPTO_API_KEY)
dsapi_client = DsapiClient()


# Register cleanup handler
async def cleanup():
    """Clean up resources on shutdown."""
    logger.info("Shutting down USPTO Patent MCP server, cleaning up resources...")
    try:
        await ppubs_client.close()
        await api_client.close()
        await ptab_client.close()
        await dsapi_client.close()
        logger.info("Cleanup completed successfully")
    except Exception as e:
        logger.error(f"Error during cleanup: {str(e)}")


# Register cleanup with atexit (best effort for stdio shutdown)
def sync_cleanup():
    """Synchronous cleanup wrapper for atexit."""
    import asyncio
    try:
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(cleanup())
                return
        except RuntimeError:
            pass

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(cleanup())
        finally:
            loop.close()
    except Exception as e:
        logger.debug(f"Cleanup during shutdown (non-critical): {str(e)}")


atexit.register(sync_cleanup)


# =====================================================================
# MCP Resources - Static data accessible via @ mentions
# =====================================================================

@mcp.resource("patents://cpc/{code}")
async def resource_cpc_classification(code: str) -> str:
    """Get CPC classification code information.

    Returns details about a CPC (Cooperative Patent Classification) code
    including section, class, and subclass information.
    """
    if len(code) == 1:
        info = get_cpc_section_info(code)
    else:
        info = get_cpc_subsection_info(code)
    return json.dumps(info, indent=2)


@mcp.resource("patents://cpc")
async def resource_cpc_sections() -> str:
    """Get all CPC section overview.

    Returns summary of all 9 CPC sections (A-H, Y) with their titles
    and descriptions for patent classification reference.
    """
    sections = {
        code: {"title": data["title"], "description": data["description"]}
        for code, data in CPC_SECTIONS.items()
    }
    return json.dumps(sections, indent=2)


def _derive_status_stage(code: str) -> str:
    """Derive coarse examination stage from a status code prefix.

    The authoritative DSAPI status-code dataset does not ship a stage
    field, so we bucket by numeric prefix for backward compatibility with
    callers that used the old static `STATUS_CODES` map.
    """
    try:
        n = int(str(code).strip())
    except (TypeError, ValueError):
        return "unknown"
    if 10 <= n <= 19:
        return "pre-exam"
    if 20 <= n <= 29:
        return "pre-exam"
    if 30 <= n <= 39:
        return "examination"
    if 40 <= n <= 49:
        return "appeal"
    if 50 <= n <= 59:
        return "granted"
    if 60 <= n <= 69:
        return "abandoned"
    if 70 <= n <= 79:
        return "published"
    if 80 <= n <= 89:
        return "continuity"
    return "unknown"


async def _lookup_status_code_via_dsapi(code: str) -> Dict[str, Any]:
    """Look up a single status code via the DSAPI source of truth.

    Returns a dict of shape ``{code, description, stage}`` on hit or an
    error dict on miss/upstream failure.
    """
    criteria = f'appl_status_code:"{code}"'
    raw = await dsapi_client.search(
        dataset="oce_patent_examination_status_codes",
        version="v1",
        criteria=criteria,
        rows=5,
    )

    if is_error(raw):
        return raw

    inner = raw.get("response", {}) if isinstance(raw, dict) else {}
    docs = inner.get("docs", []) if isinstance(inner, dict) else []

    if not docs:
        return ApiError.create(
            message=f"Unknown status code: {code}",
            error_code="NOT_FOUND",
            status_code=404,
        )

    doc = docs[0]
    description = doc.get("status_description") or doc.get("statusDescription") or ""
    return {
        "code": str(code),
        "description": description,
        "stage": _derive_status_stage(code),
    }


@mcp.resource("patents://status-codes")
async def resource_status_codes() -> str:
    """Get USPTO application status code definitions.

    Delegates to the authoritative DSAPI status code dataset.
    """
    raw = await dsapi_client.search(
        dataset="oce_patent_examination_status_codes",
        version="v1",
        criteria="*:*",
        rows=500,
    )
    if is_error(raw):
        return json.dumps(raw, indent=2)

    inner = raw.get("response", {}) if isinstance(raw, dict) else {}
    docs = inner.get("docs", []) if isinstance(inner, dict) else []

    codes = {}
    for doc in docs:
        code = str(doc.get("appl_status_code") or doc.get("applStatusCode") or "").strip()
        if not code:
            continue
        codes[code] = {
            "description": doc.get("status_description") or doc.get("statusDescription") or "",
            "stage": _derive_status_stage(code),
        }

    return json.dumps(codes, indent=2)


@mcp.resource("patents://status-codes/{code}")
async def resource_status_code(code: str) -> str:
    """Get a specific USPTO status code definition (via DSAPI)."""
    return json.dumps(await _lookup_status_code_via_dsapi(code), indent=2)


@mcp.resource("patents://sources")
async def resource_data_sources() -> str:
    """Get information about available patent data sources.

    Returns details about all integrated APIs including coverage,
    rate limits, authentication requirements, and best use cases.
    """
    return json.dumps(get_all_data_sources(), indent=2)


@mcp.resource("patents://sources/{source}")
async def resource_data_source(source: str) -> str:
    """Get information about a specific data source."""
    return json.dumps(get_data_source_info(source), indent=2)


@mcp.resource("patents://search-syntax")
async def resource_search_syntax() -> str:
    """Get search query syntax guide for all APIs.

    Returns documentation on query syntax for PPUBS and ODP APIs with examples.
    """
    return get_search_syntax_guide()


# =====================================================================
# MCP Prompts - Workflow templates accessible via / commands
# =====================================================================

@mcp.prompt()
async def prior_art_search() -> str:
    """Guide for conducting a comprehensive prior art search.

    USE THIS PROMPT WHEN: You need to find existing patents and publications
    relevant to an invention for patentability assessment or invalidity analysis.
    """
    return get_prompt("prior_art_search")["content"]


@mcp.prompt()
async def patent_validity_analysis() -> str:
    """Guide for analyzing patent validity and prosecution history.

    USE THIS PROMPT WHEN: You need to assess the strength and validity
    of a patent by reviewing its prosecution history and any challenges.
    """
    return get_prompt("patent_validity")["content"]


@mcp.prompt()
async def competitor_portfolio_analysis() -> str:
    """Guide for analyzing a company's patent portfolio.

    USE THIS PROMPT WHEN: You need to understand a competitor's IP position,
    technology focus areas, and patent strategy.
    """
    return get_prompt("competitor_portfolio")["content"]


@mcp.prompt()
async def ptab_proceeding_research() -> str:
    """Guide for researching PTAB proceedings (IPR/PGR/CBM).

    USE THIS PROMPT WHEN: You need to research Patent Trial and Appeal Board
    proceedings, decisions, and outcomes for validity challenges.
    """
    return get_prompt("ptab_research")["content"]


@mcp.prompt()
async def freedom_to_operate() -> str:
    """Guide for freedom-to-operate (FTO) analysis.

    USE THIS PROMPT WHEN: You need to assess patent infringement risk
    for a product or technology before commercialization.
    """
    return get_prompt("freedom_to_operate")["content"]


@mcp.prompt()
async def patent_landscape() -> str:
    """Guide for patent landscape analysis.

    USE THIS PROMPT WHEN: You need to map the competitive patent environment
    in a technology area to identify trends and opportunities.
    """
    return get_prompt("patent_landscape")["content"]


# =====================================================================
# Helper Functions
# =====================================================================

def _normalize_inventor_name(name: str) -> str:
    """Normalize "Last, First" inventor names to "First Last".

    ODP requires "First Last" ordering and 404s on "Last, First". We only
    rewrite the unambiguous comma-separated form; bareword "Last First"
    vs "First Last" is not auto-detectable (ambiguous for single words
    or compound surnames), so we leave those alone.
    """
    if not name or not isinstance(name, str):
        return name
    if "," not in name:
        return name
    parts = [p.strip() for p in name.split(",", 1)]
    if len(parts) != 2 or not all(parts):
        return name.strip()
    last, first = parts
    return f"{first} {last}"


def _normalize_cpc_classification(cpc: str) -> str:
    """Normalize a CPC symbol for use in ODP filter queries.

    USPTO's filter API expects CPC values with no whitespace between the
    4-char subclass prefix and the group/subgroup: "C12N15/63", "G06N3/08",
    "A61K48/00". This is the OPPOSITE of USPTO's response-payload format,
    which pads with double spaces ("C12N  15/63") for fixed-width storage.
    Passing the padded form into filters[] returns 404 "no matching records".

    Accepts common user input variations and normalizes to the filter form:
      "C12N  15/63"  -> "C12N15/63"
      "C12N 15/63"   -> "C12N15/63"
      "C12N15/63"    -> "C12N15/63" (unchanged apart from upper-casing)
      "g06n3/08"     -> "G06N3/08"

    Inputs are upper-cased and whitespace-stripped; anything else is passed
    through unchanged so callers can supply atypical CPC strings.
    """
    if not cpc or not isinstance(cpc, str):
        return cpc
    return re.sub(r"\s+", "", cpc.strip().upper())


async def _search_patent_by_number(patent_number: str) -> Dict[str, Any]:
    """Search for a patent by number and return the patent document metadata."""
    query = f'patentNumber:"{patent_number}"'
    logger.info(f"Searching for patent with query: {query}")

    result = await ppubs_client.run_query(
        query=query,
        sources=[Sources.GRANTED_PATENTS],
        limit=1
    )

    if is_error(result):
        return result

    patents = result.get(Fields.PATENTS, result.get(Fields.DOCS, []))

    if patents and len(patents) > 0:
        logger.info(f"Found patent: {patents[0].get(Fields.GUID)}")
        return {"success": True, "patent": patents[0]}

    # Try alternative query format
    alternative_query = f'"{patent_number}".pn.'
    logger.info(f"Trying alternative query: {alternative_query}")

    result = await ppubs_client.run_query(
        query=alternative_query,
        sources=[Sources.GRANTED_PATENTS],
        limit=1
    )

    if is_error(result):
        return result

    patents = result.get(Fields.PATENTS, result.get(Fields.DOCS, []))

    if not patents or len(patents) == 0:
        return ApiError.not_found("Patent", patent_number)

    logger.info(f"Found patent: {patents[0].get(Fields.GUID)}")
    return {"success": True, "patent": patents[0]}


# =====================================================================
# Diagnostic Tools
# =====================================================================

@mcp.tool()
async def check_api_status() -> Dict[str, Any]:
    """Check liveness of all patent data sources via real HTTP probes.

    USE THIS TOOL WHEN: You encounter errors or want to verify which APIs
    are actually reachable before starting research.

    Issues one cheap GET per client concurrently (2-second timeout each).
    Returns per-source: alive, latency_ms, http_status, base_url.
    """
    import asyncio
    import time
    import httpx

    probe_headers = {
        "User-Agent": config.USER_AGENT,
        "X-API-KEY": config.USPTO_API_KEY if config.USPTO_API_KEY else "",
        "Accept": "application/json",
    }

    probes = {
        "ppubs": f"{config.PPUBS_BASE_URL}/pubwebapp/",
        # Probe a stable, real ODP endpoint used by the tool surface. The old
        # singular /application/search path can return false-negative 403s even
        # when the actual /applications/* endpoints are healthy.
        "odp": f"{config.API_BASE_URL}/api/v1/patent/applications/14643719/meta-data",
        "ptab": f"{config.API_BASE_URL}/api/v1/patent/trials/proceedings/search?limit=1",
        # Probe the records endpoint the dsapi_* tools actually use (the
        # /fields route is not part of the supported surface). The
        # status-codes dataset is tiny, and rows=0 keeps the response small.
        "dsapi": (
            f"{config.API_BASE_URL}/api/v1/patent/oa/"
            "oce_patent_examination_status_codes/v1/records"
        ),
    }

    async def probe(name: str, url: str) -> tuple[str, dict]:
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                if name == "dsapi":
                    # DSAPI search is a form-encoded POST, not a GET
                    resp = await client.post(
                        url,
                        headers=probe_headers,
                        data={"criteria": "*:*", "start": "0", "rows": "0"},
                    )
                else:
                    resp = await client.get(url, headers=probe_headers)
            latency_ms = int((time.monotonic() - t0) * 1000)
            return name, {
                "alive": resp.status_code < 500,
                "latency_ms": latency_ms,
                "http_status": resp.status_code,
                "base_url": url,
            }
        except httpx.TimeoutException:
            return name, {
                "alive": False,
                "latency_ms": None,
                "http_status": None,
                "error": "timeout (>2s)",
                "base_url": url,
            }
        except Exception as exc:
            return name, {
                "alive": False,
                "latency_ms": None,
                "http_status": None,
                "error": str(exc)[:120],
                "base_url": url,
            }

    results_list = await asyncio.gather(*(probe(n, u) for n, u in probes.items()))
    results = dict(results_list)

    return {
        "success": True,
        "results": results,
    }


@mcp.tool()
async def get_cpc_info(cpc_code: str) -> Dict[str, Any]:
    """Look up CPC (Cooperative Patent Classification) code information.

    USE THIS TOOL WHEN: You need to understand what technology area a CPC
    code represents, or find related classification codes.

    Args:
        cpc_code: CPC code to look up (e.g., "G06" for computing, "G06N3/08" for neural networks)

    Returns:
        Classification details including section, title, and description.
        For section codes (A-H, Y), returns subsection list.
    """
    if len(cpc_code) == 1:
        return get_cpc_section_info(cpc_code)
    else:
        return get_cpc_subsection_info(cpc_code)


@mcp.tool()
async def get_status_code(code: str) -> Dict[str, Any]:
    """Look up USPTO application status code meaning.

    USE THIS TOOL WHEN: You encounter a status code in application data
    and need to understand what examination stage it represents.

    Args:
        code: Status code number (e.g., "30" for "Docketed New Case")

    Returns:
        Status code description and examination stage. Delegates to the
        authoritative DSAPI oce_patent_examination_status_codes dataset
        (233 records).
    """
    return await _lookup_status_code_via_dsapi(code)


# =====================================================================
# PPUBS Tools - Full text patents and PDF downloads
# =====================================================================

@mcp.tool()
async def ppubs_search_patents(
    query: str,
    offset: int = 0,
    limit: int = 100,
    sort: str = "date_publ desc",
) -> Dict[str, Any]:
    """Search granted US patents in Patent Public Search (ppubs.uspto.gov).

    USE THIS TOOL WHEN: You need full-text search of US patents with daily
    updates, or need access to the most recent patent filings.

    PREFER OVER ODP search WHEN: You need the most current data
    (PPUBS updates daily vs ODP periodic updates).

    Args:
        query: Search query using BRS (Boolean Retrieval System) syntax.
               Format: term.field_code. (value first, then field code with dots)
               Examples:
               - "machine learning" - searches all fields
               - "neural network".ttl. - title contains phrase
               - Smith.in. AND IBM.an. - inventor Smith, assignee IBM
               - G06N3/08.cpc. - CPC classification
               - (Sonata AND Jodele).in. - boolean combination in inventor field
        offset: Starting position for pagination (default: 0)
        limit: Maximum results to return (default: 100, max: 500)
        sort: Sort order (default: "date_publ desc")

    Returns:
        Normalized response with patent results including GUID, title,
        abstract, dates, inventors, and classification codes.
    """
    result = await ppubs_client.run_query(
        query=query,
        start=offset,
        limit=min(limit, 500),
        sort=sort,
        sources=[Sources.GRANTED_PATENTS],
    )

    if is_error(result):
        return result

    response = ResponseEnvelope.from_ppubs(result, offset, limit)
    return check_and_truncate(response)


@mcp.tool()
async def ppubs_search_applications(
    query: str,
    offset: int = 0,
    limit: int = 100,
    sort: str = "date_publ desc",
) -> Dict[str, Any]:
    """Search published US patent applications in Patent Public Search.

    USE THIS TOOL WHEN: You need to search pre-grant published applications
    (applications publish 18 months after filing, before grant).

    Args:
        query: Search query using BRS syntax (same as ppubs_search_patents).
               Format: term.field_code. (e.g., "neural network".ttl.)
        offset: Starting position for pagination (default: 0)
        limit: Maximum results to return (default: 100, max: 500)
        sort: Sort order (default: "date_publ desc")

    Returns:
        Normalized response with application results.
    """
    result = await ppubs_client.run_query(
        query=query,
        start=offset,
        limit=min(limit, 500),
        sort=sort,
        sources=[Sources.PUBLISHED_APPLICATIONS],
    )

    if is_error(result):
        return result

    response = ResponseEnvelope.from_ppubs(result, offset, limit)
    return check_and_truncate(response)


@mcp.tool()
async def ppubs_get_full_document(guid: str, source_type: str) -> Dict[str, Any]:
    """Get complete patent document by GUID from PPUBS.

    USE THIS TOOL WHEN: You have a document GUID from search results
    and need the full patent text including all claims and description.

    Args:
        guid: Document GUID (e.g., "US-9876543-B2")
        source_type: Document type - "USPAT" for patents, "US-PGPUB" for applications

    Returns:
        Complete document with claims, description, drawings info, and metadata.
    """
    result = await ppubs_client.get_document(guid, source_type)

    if is_error(result):
        return result

    return check_and_truncate(result)


@mcp.tool()
async def ppubs_get_patent_by_number(patent_number: str) -> Dict[str, Any]:
    """Get a granted patent's full text by patent number.

    USE THIS TOOL WHEN: You know the patent number and need the complete
    document including claims, description, and all sections.

    Args:
        patent_number: Patent number without commas (e.g., "7123456" or "10000000")

    Returns:
        Complete patent document with full text of all sections.
    """
    try:
        patent_number = validate_patent_number(str(patent_number))
    except ValueError as e:
        return ApiError.validation_error(str(e), "patent_number")

    search_result = await _search_patent_by_number(patent_number)

    if is_error(search_result):
        return search_result

    patent = search_result["patent"]
    result = await ppubs_client.get_document(patent[Fields.GUID], patent[Fields.TYPE])

    if is_error(result):
        return result

    return check_and_truncate(result)


@mcp.tool()
async def ppubs_download_patent_pdf(patent_number: str) -> Dict[str, Any]:
    """Download a patent as PDF. Saves to disk and returns file path.

    USE THIS TOOL WHEN: You need the official PDF document of a patent.
    The PDF is written to the configured download directory; the caller
    reads it from disk rather than receiving bytes inline.

    Args:
        patent_number: Patent number without commas (e.g., "7123456")

    Returns:
        Dictionary with file_path to the saved PDF on success.
    """
    import os

    try:
        patent_number = validate_patent_number(str(patent_number))
    except ValueError as e:
        return ApiError.validation_error(str(e), "patent_number")

    search_result = await _search_patent_by_number(patent_number)

    if is_error(search_result):
        return search_result

    patent = search_result["patent"]
    result = await ppubs_client.download_image(
        guid=patent[Fields.GUID],
        image_location=patent[Fields.IMAGE_LOCATION],
        page_count=patent[Fields.PAGE_COUNT],
        document_type=patent[Fields.TYPE],
    )

    if is_error(result):
        return result

    download_dir = config.DOWNLOAD_DIR
    os.makedirs(download_dir, exist_ok=True)

    filename = f"ppubs_{patent_number}.pdf"
    file_path = os.path.join(download_dir, filename)

    with open(file_path, "wb") as f:
        f.write(result["content"])

    return {
        "success": True,
        "file_path": file_path,
        "size_bytes": result.get("size_bytes", len(result["content"])),
        "content_type": result.get("content_type", "application/pdf"),
        "filename": filename,
        "patent_number": patent_number,
    }


# =====================================================================
# ODP Tools - USPTO Open Data Portal (api.uspto.gov)
# =====================================================================

@mcp.tool()
async def odp_get_application(app_num: str) -> Dict[str, Any]:
    """Get patent application data from USPTO Open Data Portal.

    USE THIS TOOL WHEN: You need prosecution/file wrapper data for an
    application including status, dates, and basic metadata.

    Args:
        app_num: Application number without slashes or commas (e.g., "14412875")

    Returns:
        Application data including filing date, status, and basic info.
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}"
    result = await api_client.make_request(url)

    if is_error(result):
        return result

    return check_and_truncate(ResponseEnvelope.from_odp(result))


@mcp.tool()
async def odp_get_application_metadata(app_num: str) -> Dict[str, Any]:
    """Get detailed metadata for a patent application.

    USE THIS TOOL WHEN: You need comprehensive application metadata
    including examiner info, art unit, and detailed status.

    Args:
        app_num: Application number without slashes (e.g., "14412875")
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/meta-data"
    result = await api_client.make_request(url)

    if is_error(result):
        return result

    return ResponseEnvelope.from_odp(result)


@mcp.tool()
async def odp_get_continuity(app_num: str) -> Dict[str, Any]:
    """Get patent family/continuity data (parent and child applications).

    USE THIS TOOL WHEN: You need to understand the patent family tree -
    parent applications, continuations, divisionals, and CIPs.

    Args:
        app_num: Application number without slashes (e.g., "14412875")

    Returns:
        Continuity data showing parent/child relationships and priority claims.
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/continuity"
    result = await api_client.make_request(url)

    if is_error(result):
        return result

    return ResponseEnvelope.from_odp(result)


@mcp.tool()
async def odp_get_assignment(app_num: str) -> Dict[str, Any]:
    """Get patent assignment/ownership records.

    USE THIS TOOL WHEN: You need to know current and historical owners
    of a patent or application.

    Args:
        app_num: Application number without slashes (e.g., "14412875")
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/assignment"
    return await api_client.make_request(url)


@mcp.tool()
async def odp_get_adjustment(app_num: str) -> Dict[str, Any]:
    """Get patent term adjustment (PTA) data.

    USE THIS TOOL WHEN: You need to calculate the actual expiration date
    of a patent accounting for USPTO delays.

    Args:
        app_num: Application number without slashes (e.g., "14412875")
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/adjustment"
    return await api_client.make_request(url)


@mcp.tool()
async def odp_get_attorney(app_num: str) -> Dict[str, Any]:
    """Get attorney/agent of record for an application.

    Args:
        app_num: Application number without slashes (e.g., "14412875")
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/attorney"
    return await api_client.make_request(url)


@mcp.tool()
async def odp_get_foreign_priority(app_num: str) -> Dict[str, Any]:
    """Get foreign priority claims for an application.

    USE THIS TOOL WHEN: You need to find priority claims to foreign
    applications that may affect the effective filing date.

    Args:
        app_num: Application number without slashes (e.g., "14412875")
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/foreign-priority"
    return await api_client.make_request(url)


@mcp.tool()
async def odp_get_transactions(app_num: str) -> Dict[str, Any]:
    """Get prosecution transaction history for an application.

    USE THIS TOOL WHEN: You need the complete timeline of prosecution
    events including office actions, responses, and fee payments.

    Args:
        app_num: Application number without slashes (e.g., "14412875")
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/transactions"
    result = await api_client.make_request(url)

    if is_error(result):
        return result

    return check_and_truncate(ResponseEnvelope.from_odp(result))


@mcp.tool()
async def odp_get_documents(app_num: str) -> Dict[str, Any]:
    """Get list of documents in the application file wrapper.

    Args:
        app_num: Application number without slashes (e.g., "14412875")
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/documents"
    result = await api_client.make_request(url)

    if is_error(result):
        return result

    return check_and_truncate(ResponseEnvelope.from_odp(result))


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
    import os

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

    # Save to configured download directory
    download_dir = config.DOWNLOAD_DIR
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


@mcp.tool()
async def odp_search_applications(
    query: Optional[str] = None,
    application_number: Optional[str] = None,
    patent_number: Optional[str] = None,
    inventor_name: Optional[str] = None,
    assignee_name: Optional[str] = None,
    cpc_classification: Optional[str] = None,
    publication_category: Optional[str] = None,
    application_status: Optional[str] = None,
    application_type: Optional[str] = None,
    group_art_unit: Optional[Union[str, int]] = None,
    filing_date_from: Optional[str] = None,
    filing_date_to: Optional[str] = None,
    offset: int = 0,
    limit: int = 25,
) -> Dict[str, Any]:
    """Search patent applications in USPTO Open Data Portal.

    USE THIS TOOL WHEN: You need to search applications with filtering
    by applicant metadata, dates, or other criteria not available in PPUBS.

    Supports both simple queries and complex multi-field filtering.
    Uses the ODP POST search API with structured filters and range filters.

    Args:
        query: General search query (free-form text or ODP DSL field:value syntax)
        application_number: Filter by application number (exact match)
        patent_number: Filter by patent number (exact match)
        inventor_name: Filter by inventor name. Preferred format is
            "First Last" (e.g., "Sonata Jodele"). A comma-separated form
            "Last, First" (e.g., "Jodele, Sonata") is auto-normalized to
            "First Last" before querying. Plain "Last First" (no comma)
            cannot be auto-detected and will 404. Wildcards (e.g., "Jodele*")
            are not recommended as they match too broadly.
        assignee_name: Filter by assignee/applicant name (supports wildcards with *)
        cpc_classification: Filter by Cooperative Patent Classification symbol
            (e.g., "C12N 15/63" for recombinant DNA vectors, "G06N3/08" for
            neural networks with backpropagation). Input is auto-normalized
            to USPTO's filter-query format, which is whitespace-stripped
            ("C12N15/63"). Note that this differs from the padded format
            USPTO uses in response payloads ("C12N  15/63") — the filter
            API requires the no-space form. Builds a structured filter
            entry against applicationMetaData.cpcClassificationBag.
        publication_category: Filter by publication category. Values are
            exact phrases as stored by USPTO; the most common is
            "Pre-Grant Publications - PGPub". Builds a filter entry against
            applicationMetaData.publicationCategoryBag.
        application_status: Filter by application status description.
            IMPORTANT: Values are case-sensitive exact phrases (e.g.,
            "Non Final Action Mailed", "Patented Case", "Docketed New Case").
            Lowercase or partial phrases return 404. To discover valid
            values, use the get_status_code tool — the authoritative DSAPI
            status-code dataset has 233 records. Builds a filter entry
            against applicationMetaData.applicationStatusDescriptionText.
        application_type: Filter by application type. Case-insensitive.
            Known values: "Utility", "Design", "Plant", "Reissue",
            "Provisional", "Statutory Invention Registration". Builds a
            filter entry against applicationMetaData.applicationTypeLabelName.
        group_art_unit: Filter by group art unit number (e.g., "1631" for
            molecular biology / microbiology, "2100" for computer
            architecture). Accepts str or int; both are cast to str. Builds
            a filter entry against applicationMetaData.groupArtUnitNumber.
        filing_date_from: Filing date range start (YYYY-MM-DD)
        filing_date_to: Filing date range end (YYYY-MM-DD)
        offset: Starting position (default: 0)
        limit: Max results (default: 25)

    Returns:
        Normalized response with matching applications.
    """
    body: Dict[str, Any] = {
        "pagination": {"offset": offset, "limit": limit},
    }

    # Build q string: combine free-form query with field-specific DSL using AND
    q_parts = []
    if query:
        q_parts.append(query)
    if inventor_name:
        normalized = _normalize_inventor_name(inventor_name)
        val = f'"{normalized}"' if " " in normalized else normalized
        q_parts.append(f"applicationMetaData.inventorBag.inventorNameText:{val}")
    if assignee_name:
        val = f'"{assignee_name}"' if " " in assignee_name else assignee_name
        q_parts.append(f"applicationMetaData.firstApplicantName:{val}")
    if q_parts:
        body["q"] = " AND ".join(q_parts)

    # Build filters array for exact-match fields
    filters = []
    if application_number:
        filters.append({
            "name": "applicationNumberText",
            "value": [application_number],
        })
    if patent_number:
        filters.append({
            "name": "applicationMetaData.patentNumber",
            "value": [patent_number],
        })
    if cpc_classification:
        filters.append({
            "name": "applicationMetaData.cpcClassificationBag",
            "value": [_normalize_cpc_classification(cpc_classification)],
        })
    if publication_category:
        filters.append({
            "name": "applicationMetaData.publicationCategoryBag",
            "value": [publication_category],
        })
    if application_status:
        filters.append({
            "name": "applicationMetaData.applicationStatusDescriptionText",
            "value": [application_status],
        })
    if application_type:
        filters.append({
            "name": "applicationMetaData.applicationTypeLabelName",
            "value": [application_type],
        })
    if group_art_unit is not None:
        filters.append({
            "name": "applicationMetaData.groupArtUnitNumber",
            "value": [str(group_art_unit)],
        })
    if filters:
        body["filters"] = filters

    # Build rangeFilters for date ranges
    if filing_date_from and filing_date_to:
        body["rangeFilters"] = [{
            "field": "applicationMetaData.filingDate",
            "valueFrom": filing_date_from,
            "valueTo": filing_date_to,
        }]
    elif filing_date_from:
        q_part = f"applicationMetaData.filingDate:>={filing_date_from}"
        body["q"] = f"{body['q']} AND {q_part}" if "q" in body else q_part
    elif filing_date_to:
        q_part = f"applicationMetaData.filingDate:<={filing_date_to}"
        body["q"] = f"{body['q']} AND {q_part}" if "q" in body else q_part

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/search"
    result = await api_client.make_request(url, method="POST", data=body)

    if is_error(result):
        return result

    return check_and_truncate(ResponseEnvelope.from_odp(result, offset, limit))


@mcp.tool()
async def odp_search_datasets(
    query: Optional[str] = None,
    offset: int = 0,
    limit: int = 25,
) -> Dict[str, Any]:
    """Search USPTO bulk data products/datasets.

    USE THIS TOOL WHEN: You need to find bulk download datasets
    available from USPTO for large-scale analysis.

    Args:
        query: Search query for dataset names/descriptions
        offset: Starting position (default: 0)
        limit: Max results (default: 25)
    """
    params: Dict[str, Any] = {"offset": offset, "limit": limit}
    if query:
        params["searchText"] = query

    query_string = api_client.build_query_string(params)
    url = f"{config.API_BASE_URL}/api/v1/datasets/products/search?{query_string}"

    return await api_client.make_request(url)


@mcp.tool()
async def odp_get_dataset(product_id: str) -> Dict[str, Any]:
    """Get details of a specific bulk dataset product.

    Args:
        product_id: Dataset product identifier
    """
    url = f"{config.API_BASE_URL}/api/v1/datasets/products/{product_id}"
    result = await api_client.make_request(url)

    if is_error(result):
        return result

    return check_and_truncate(
        ResponseEnvelope.success(results=result, source="odp")
    )


# =====================================================================
# PTAB Tools - Patent Trial and Appeal Board
# =====================================================================

@mcp.tool()
async def ptab_search_proceedings(
    query: Optional[str] = None,
    trial_type: Optional[str] = None,
    patent_number: Optional[str] = None,
    party_name: Optional[str] = None,
    filing_date_from: Optional[str] = None,
    filing_date_to: Optional[str] = None,
    status: Optional[str] = None,
    offset: int = 0,
    limit: int = 25,
) -> Dict[str, Any]:
    """Search PTAB trial proceedings (IPR, PGR, CBM, derivation).

    USE THIS TOOL WHEN: You need to find patent validity challenges at
    the Patent Trial and Appeal Board.

    Trial types:
    - IPR: Inter Partes Review (most common, based on patents/publications)
    - PGR: Post-Grant Review (broader grounds, within 9 months of grant)
    - CBM: Covered Business Method (for financial method patents, sunsetted)
    - DER: Derivation proceedings

    Args:
        query: Full-text search query
        trial_type: Type of trial (IPR, PGR, CBM, DER)
        patent_number: Patent number being challenged
        party_name: Petitioner or patent owner name
        filing_date_from: Filing date range start (YYYY-MM-DD)
        filing_date_to: Filing date range end (YYYY-MM-DD)
        status: Proceeding status (Pending, Instituted, Terminated, FWD Entered)
        offset: Starting position (default: 0)
        limit: Max results (default: 25)

    Returns:
        Normalized response with matching proceedings.
    """
    if trial_type and trial_type not in PTABTrialTypes.ALL:
        return ApiError.validation_error(
            f"Invalid trial type. Must be one of: {', '.join(PTABTrialTypes.ALL)}",
            "trial_type"
        )

    result = await ptab_client.search_proceedings(
        query=query,
        trial_type=trial_type,
        patent_number=patent_number,
        party_name=party_name,
        filing_date_from=filing_date_from,
        filing_date_to=filing_date_to,
        status=status,
        offset=offset,
        limit=limit,
    )

    if is_error(result):
        return result

    return check_and_truncate(ResponseEnvelope.from_ptab(result, offset, limit))


@mcp.tool()
async def ptab_get_proceeding(proceeding_number: str) -> Dict[str, Any]:
    """Get details of a specific PTAB proceeding.

    Args:
        proceeding_number: Proceeding number (e.g., "IPR2023-00001")

    Returns:
        Proceeding details including parties, patent, status, and dates.
    """
    return await ptab_client.get_proceeding(proceeding_number)


@mcp.tool()
async def ptab_search_decisions(
    query: Optional[str] = None,
    decision_type: Optional[str] = None,
    proceeding_number: Optional[str] = None,
    patent_number: Optional[str] = None,
    decision_date_from: Optional[str] = None,
    decision_date_to: Optional[str] = None,
    offset: int = 0,
    limit: int = 25,
) -> Dict[str, Any]:
    """Search PTAB trial decisions.

    USE THIS TOOL WHEN: You need to find institution decisions, final
    written decisions, or termination orders.

    Args:
        query: Full-text search in decision text
        decision_type: Type (institution, final, termination)
        proceeding_number: Filter by proceeding number
        patent_number: Filter by patent number
        decision_date_from: Date range start (YYYY-MM-DD)
        decision_date_to: Date range end (YYYY-MM-DD)
        offset: Starting position (default: 0)
        limit: Max results (default: 25)
    """
    result = await ptab_client.search_decisions(
        query=query,
        decision_type=decision_type,
        proceeding_number=proceeding_number,
        patent_number=patent_number,
        decision_date_from=decision_date_from,
        decision_date_to=decision_date_to,
        offset=offset,
        limit=limit,
    )

    if is_error(result):
        return result

    return check_and_truncate(ResponseEnvelope.from_ptab(result, offset, limit))


@mcp.tool()
async def ptab_get_decision(decision_id: str) -> Dict[str, Any]:
    """Get details of a specific PTAB decision.

    Args:
        decision_id: Decision identifier
    """
    return await ptab_client.get_decision(decision_id)


# =====================================================================
# DSAPI Tools — USPTO Data Set API (api.uspto.gov)
# =====================================================================

@mcp.tool()
async def dsapi_search_enriched_citations(
    patent_app_number: str,
    rows: int = 25,
) -> Dict[str, Any]:
    """Search enriched cited reference metadata for a patent application.

    USE THIS TOOL WHEN: You need citation details from office actions for a
    specific application, including who cited it (examiner vs applicant),
    citation category codes, and passage locations.

    Data source: USPTO DSAPI enriched_cited_reference_metadata/v3
    (43.6M records, last updated 2024-07).

    Fields include: officeActionDate, citedDocumentIdentifier, kindCode,
    countryCode, inventorNameText, citationCategoryCode, passageLocationText,
    examinerCitedReferenceIndicator, applicantCitedExaminerReferenceIndicator,
    nplIndicator, qualitySummaryText, groupArtUnitNumber, techCenter.

    Args:
        patent_app_number: Patent application number (digits only, e.g., "16123456")
        rows: Maximum results to return (default: 25)

    Returns:
        DSAPI response with matching enriched citation records.
    """
    criteria = f'patentApplicationNumber:"{patent_app_number}"'
    return await dsapi_client.search(
        dataset="enriched_cited_reference_metadata",
        version="v3",
        criteria=criteria,
        rows=rows,
    )


@mcp.tool()
async def dsapi_get_citation_details(
    criteria: str,
    start: int = 0,
    rows: int = 25,
) -> Dict[str, Any]:
    """Advanced search of enriched cited reference metadata using Lucene syntax.

    USE THIS TOOL WHEN: You need flexible queries across enriched citation data,
    e.g., searching by cited document identifier, tech center, work group,
    or combining multiple field conditions.

    Data source: USPTO DSAPI enriched_cited_reference_metadata/v3
    (43.6M records, last updated 2024-07).

    IMPORTANT: The citedDocumentIdentifier field uses formatted patent numbers
    with spaces, commas, and kind codes (e.g., "US 7,725,375 B2"), NOT raw
    numbers. Use wildcards for partial matching: citedDocumentIdentifier:"US 7,725*"

    Example queries:
    - citedDocumentIdentifier:"US 7,725,375*" — find all citations of a specific patent
    - techCenter:"2100" — all citations from tech center 2100
    - examinerCitedReferenceIndicator:"true" AND countryCode:"US"

    Args:
        criteria: Lucene query string (e.g., 'citedDocumentIdentifier:"US 7,725,375*"')
        start: Pagination offset (default: 0)
        rows: Maximum results to return (default: 25)

    Returns:
        DSAPI response with matching enriched citation records.
    """
    return await dsapi_client.search(
        dataset="enriched_cited_reference_metadata",
        version="v3",
        criteria=criteria,
        start=start,
        rows=rows,
    )


@mcp.tool()
async def dsapi_search_litigation(
    criteria: str,
    start: int = 0,
    rows: int = 25,
) -> Dict[str, Any]:
    """Search USPTO patent litigation cases using Lucene query syntax.

    USE THIS TOOL WHEN: You need to find patent litigation cases by case name,
    court, parties, or any other field. Covers federal district court cases.

    Data source: USPTO DSAPI oce_patent_litigation_cases/v1
    (74.6K records, last updated 2021-10).

    Fields include: case_name, case_number, court_name, assigned_to,
    date_filed, date_closd, settlement, jury_demand, case_cause,
    jurisdictional_basis, pacer_id, related_case, lead_case.

    Example queries:
    - case_name:"Apple" — cases with Apple in the name
    - court_name:"Delaware" — cases in Delaware courts
    - settlement:"true" — cases that settled

    Args:
        criteria: Lucene query string
        start: Pagination offset (default: 0)
        rows: Maximum results to return (default: 25)

    Returns:
        DSAPI response with matching litigation case records.
    """
    return await dsapi_client.search(
        dataset="oce_patent_litigation_cases",
        version="v1",
        criteria=criteria,
        start=start,
        rows=rows,
    )


@mcp.tool()
async def dsapi_get_patent_litigation(
    patent_number: str,
    rows: int = 25,
) -> Dict[str, Any]:
    """Search litigation cases involving a specific patent number.

    USE THIS TOOL WHEN: You need to find all litigation cases where a
    specific patent was asserted or challenged in federal court.

    KNOWN LIMITATION: The litigation dataset has no patent_number field.
    This tool searches case_name and case_cause for the patent number string,
    which is a heuristic — it may return no results even if litigation exists.
    For more reliable results, look up the patent's assignee first and use
    dsapi_search_litigation with case_name:"<assignee name>".

    Data source: USPTO DSAPI oce_patent_litigation_cases/v1
    (74.6K records, last updated 2021-10).

    Args:
        patent_number: Patent number to search for (e.g., "7654321")
        rows: Maximum results to return (default: 25)

    Returns:
        DSAPI response with matching litigation case records.
    """
    criteria = f'case_name:"{patent_number}" OR case_cause:"{patent_number}"'
    return await dsapi_client.search(
        dataset="oce_patent_litigation_cases",
        version="v1",
        criteria=criteria,
        rows=rows,
    )


@mcp.tool()
async def dsapi_search_rejections(
    patent_app_number: str,
    rows: int = 25,
) -> Dict[str, Any]:
    """Search office action rejections for a patent application.

    USE THIS TOOL WHEN: You need to understand what rejections an application
    received during prosecution, including 101/102/103/112 rejections and
    subject matter eligibility indicators (Alice, Bilski, Mayo, Myriad).

    Data source: USPTO DSAPI oa_rejections/v2
    (87.0M records, last updated 2021-05).

    Key fields:
    - hasRej101, hasRej102, hasRej103, hasRej112, hasRejDP — rejection type flags
    - aliceIndicator, bilskiIndicator, mayoIndicator, myriadIndicator — SME indicators
    - legalSectionCode — statutory section of the rejection
    - claimNumberArrayDocument — affected claims
    - allowedClaimIndicator — whether the claim was ultimately allowed
    - actionTypeCategory — type of office action (e.g., Non-Final, Final)

    Args:
        patent_app_number: Patent application number (digits only, e.g., "16123456")
        rows: Maximum results to return (default: 25)

    Returns:
        DSAPI response with rejection records for the application.
    """
    criteria = f'patentApplicationNumber:"{patent_app_number}"'
    return await dsapi_client.search(
        dataset="oa_rejections",
        version="v2",
        criteria=criteria,
        rows=rows,
    )


@mcp.tool()
async def dsapi_search_oa_citations(
    patent_app_number: str,
    rows: int = 25,
) -> Dict[str, Any]:
    """Search office action citations for a patent application.

    USE THIS TOOL WHEN: You need to find what references were cited in
    office actions during prosecution of a specific application, including
    whether the citation was by the examiner or applicant and under which
    legal section.

    Data source: USPTO DSAPI oa_citations/v2
    (132.8M records, last updated 2019-06).

    Fields include: referenceIdentifier, parsedReferenceIdentifier,
    legalSectionCode, actionTypeCategory, examinerCitedReferenceIndicator,
    applicantCitedExaminerReferenceIndicator, officeActionCitationReferenceIndicator,
    groupArtUnitNumber, techCenter, paragraphNumber.

    Args:
        patent_app_number: Patent application number (digits only, e.g., "16123456")
        rows: Maximum results to return (default: 25)

    Returns:
        DSAPI response with office action citation records.
    """
    criteria = f'patentApplicationNumber:"{patent_app_number}"'
    return await dsapi_client.search(
        dataset="oa_citations",
        version="v2",
        criteria=criteria,
        rows=rows,
    )


@mcp.tool()
async def dsapi_search_office_actions(
    patent_app_number: str,
    rows: int = 5,
) -> Dict[str, Any]:
    """Search full office action text for a patent application.

    USE THIS TOOL WHEN: You need the actual text content of office actions,
    including section-level rejection text (101, 102, 103, 112), claim
    analysis, citation details, and examiner reasoning.

    CAUTION: Records are large (full text). Default limit is 5 to avoid
    oversized responses. Increase rows only if needed.

    Data source: USPTO DSAPI oa_actions/v1
    (19.1M records, last updated 2020-03).

    Key fields include: bodyText, inventionTitle, patentNumber,
    submissionDate, groupArtUnitNumber, techCenter, examinerEmployeeNumber,
    inventionSubjectMatterCategory, sections.section101RejectionText,
    sections.section102RejectionText, sections.section103RejectionText,
    sections.section112RejectionText, sections.summaryText,
    sections.detailCitationText, applicationStatusNumber.

    Args:
        patent_app_number: Patent application number (digits only, e.g., "16123456")
        rows: Maximum results to return (default: 5, keep small — records are large)

    Returns:
        DSAPI response with full office action text records.
    """
    criteria = f'patentApplicationNumber:"{patent_app_number}"'
    result = await dsapi_client.search(
        dataset="oa_actions",
        version="v1",
        criteria=criteria,
        rows=rows,
    )

    if is_error(result):
        return result

    # DSAPI shape: {"response": {"start", "numFound", "docs": [...]}}
    inner = result.get("response", {}) if isinstance(result, dict) else {}
    docs = inner.get("docs", []) if isinstance(inner, dict) else []
    total = inner.get("numFound", len(docs)) if isinstance(inner, dict) else len(docs)

    return check_and_truncate(
        ResponseEnvelope.success(
            results=docs,
            source="dsapi",
            count=len(docs),
            total=total,
            limit=rows,
        )
    )


@mcp.tool()
async def dsapi_lookup_status_code(
    status_code: str,
) -> Dict[str, Any]:
    """Look up a USPTO patent examination status code description.

    USE THIS TOOL WHEN: You encounter a numeric status code in application
    data and need to understand what examination stage or disposition it
    represents.

    Data source: USPTO DSAPI oce_patent_examination_status_codes/v1
    (233 records, last updated 2018-02).

    Fields: appl_status_code, status_description.

    Args:
        status_code: Status code number to look up (e.g., "30", "150")

    Returns:
        DSAPI response with the status code description.
    """
    criteria = f'appl_status_code:"{status_code}"'
    return await dsapi_client.search(
        dataset="oce_patent_examination_status_codes",
        version="v1",
        criteria=criteria,
        rows=5,
    )


@mcp.tool()
async def dsapi_list_status_codes(
    criteria: str = "*:*",
    rows: int = 50,
) -> Dict[str, Any]:
    """List or search USPTO patent examination status codes.

    USE THIS TOOL WHEN: You need to browse available status codes or search
    for status codes by description text.

    Data source: USPTO DSAPI oce_patent_examination_status_codes/v1
    (233 records, last updated 2018-02).

    Example queries:
    - "*:*" — list all status codes (default)
    - status_description:"abandoned" — codes related to abandonment
    - status_description:"patent" — codes related to patent grants

    Args:
        criteria: Lucene query string (default: "*:*" for all)
        rows: Maximum results to return (default: 50)

    Returns:
        DSAPI response with matching status code records.
    """
    return await dsapi_client.search(
        dataset="oce_patent_examination_status_codes",
        version="v1",
        criteria=criteria,
        rows=rows,
    )


# =====================================================================
# Main entry point
# =====================================================================

def main():
    """Initialize and run the server with stdio transport."""
    logger.info("Starting USPTO Patent MCP server with stdio transport")
    mcp.run(transport='stdio')


if __name__ == "__main__":
    main()
