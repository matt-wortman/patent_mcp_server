# MCP Server Integrity Review

**Project:** USPTO Patent MCP Server

**Reviewed version:** 0.11.1 (`4402a13`)

**Review date:** 2026-08-19

**Scope:** Source code, packaging, dependency security, MCP protocol behavior,
tests, configuration, and documentation

**Overall status:** Needs hardening before the next published release

## Executive Summary

The server's core MCP behavior is healthy when run with a compatible 1.x MCP
SDK. A real stdio handshake completed successfully and exposed the expected 34
tools, 4 concrete resources, 3 resource templates, and 6 prompts. All 93 unit
tests and all 4 live PPUBS smoke tests passed.

The current package should not be published unchanged, however. Fresh installs
resolve MCP SDK 2.x and fail at startup, DEBUG logging exposes credentials, and
the oversized-response fallback can silently discard records while reporting
that the full payload was saved. These are the highest-priority findings.

No application source files were changed during this review.

## Findings

### IR-01 — Fresh installations resolve an incompatible MCP SDK

**Severity:** High — release blocker

**Location:** `pyproject.toml:40`, `src/patent_mcp_server/patents.py:24`

The package declares `mcp[cli]>=1.3.0` without an upper bound. A fresh install
now resolves `mcp 2.0.0`, where `mcp.server.fastmcp` was removed and `FastMCP`
was renamed to `MCPServer`. Starting the installed console script fails with:

```text
ModuleNotFoundError: No module named 'mcp.server.fastmcp'
```

The repository lockfile currently pins `mcp 1.23.1`. A dependency audit found
three advisories affecting that version. Their vulnerable WebSocket, HTTP
session, and experimental task paths are not reachable through this server's
stdio-only configuration, but the locked dependency is still unsupported and
should be updated.

**Recommendation:** Keep the server on the maintained 1.x SDK until a deliberate
v2 migration:

```toml
"mcp[cli]>=1.28.1,<2"
```

Regenerate `uv.lock` afterward. The complete unit suite and stdio handshake
both passed with `mcp 1.28.1`.

References:

- [MCP SDK v2 changes](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/whats-new.md)
- [MCP SDK installation and version-bound guidance](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/get-started/installation.md)
- [MCP SDK security policy](https://github.com/modelcontextprotocol/python-sdk/security)

### IR-02 — DEBUG logging exposes API and session credentials

**Severity:** High

**Location:** `src/patent_mcp_server/util/logging.py:13-41`

`LoggingTransport` logs complete request and response header dictionaries.
Request headers contain the ODP `X-API-KEY`, while PPUBS response headers can
contain `X-Access-Token`. A controlled sentinel API key appeared verbatim in
the generated DEBUG log message during review.

The default log level is INFO, but DEBUG is a documented configuration option,
so users can trigger this leak during routine troubleshooting.

**Recommendation:** Redact at least `X-API-KEY`, `X-Access-Token`,
`Authorization`, `Cookie`, and `Set-Cookie`. Prefer an allowlist of harmless
headers over a denylist. Consider requiring a separate explicit option before
logging request bodies.

### IR-03 — Oversized-response fallback silently loses records

**Severity:** High — data integrity

**Location:** `src/patent_mcp_server/util/response.py:319-340`

When list results exceed the token budget, `truncate_response()` slices the
list first. If the slice is still oversized, it passes the already-truncated
copy to `_save_oversized_to_disk()`. The summary then says:

```text
Response exceeded token budget; full payload saved to disk.
```

A reproduction with 10 input records and `max_results=5` produced a saved file
containing only 5 records.

**Recommendation:** Save the original response before slicing, then return a
small summary. Extend `test_list_still_oversized_saves_to_disk` to assert that
the on-disk record count and contents match the complete input.

### IR-04 — Closing the logging transport does not close its connection pool

**Severity:** Medium

**Location:** `src/patent_mcp_server/util/logging.py:8-43`

`LoggingTransport` wraps `httpx.AsyncHTTPTransport` but does not forward
`aclose()`. A probe transport remained open after the wrapper was closed. This
makes the clients' cleanup methods and shutdown log messages inaccurate and
can leak sockets in long-running or repeatedly instantiated processes.

**Recommendation:** Add:

```python
async def aclose(self):
    await self.transport.aclose()
```

Add a unit test that verifies close propagation.

### IR-05 — Pagination inputs are unbounded and truncation is inconsistent

**Severity:** Medium — availability and context safety

**Locations:** `src/patent_mcp_server/patents.py:1013-1161`,
`src/patent_mcp_server/patents.py:1343-1686`,
`src/patent_mcp_server/uspto/dsapi_client.py:61-103`

Several ODP, PTAB, and DSAPI tools accept negative or arbitrarily large
`offset`, `limit`, `start`, and `rows` values. Most DSAPI tools return raw
upstream dictionaries without `check_and_truncate`, while the office-action
tool does normalize and truncate.

Responses are fully buffered and parsed before truncation, so post-processing
does not protect host memory from an excessively large upstream response.
Unvalidated application numbers are also interpolated directly into Lucene
criteria, allowing a nominally narrow lookup to be broadened.

**Recommendation:** Enforce non-negative offsets and source-appropriate maximum
page sizes at every tool boundary, validate digit-only application numbers for
narrow lookup tools, consistently normalize responses, and consider streamed
or size-limited downloads for large payloads.

### IR-06 — Legacy integration tests can report success without validating behavior

**Severity:** Medium — test integrity

**Locations:** `test/test_tools.py`, `test/test_patents.py:27-229`,
`pytest.ini:22-27`

`test/test_tools.py` contains no assertions; its test functions return boolean
values that pytest ignores. `test/test_patents.py` catches broad exceptions,
logs them, and returns normally. Pytest warnings are disabled, hiding warnings
about non-`None` test returns.

Consequently, these tests can be green when API operations fail. The newer
`test/smoke/` suite uses real assertions and is materially more trustworthy.

**Recommendation:** Convert every expected condition to an assertion, allow
unexpected exceptions to fail the test, and remove obsolete manual-result file
generation. Consider deleting the legacy modules after their useful scenarios
are represented in `test/smoke/`.

### IR-07 — PPUBS smoke tests are unnecessarily gated by an ODP API key

**Severity:** Low

**Location:** `test/smoke/test_ppubs_smoke.py:1-13`

PPUBS does not require `USPTO_API_KEY`, but the entire smoke module skips when
the key is absent. This prevents the only unauthenticated live integration from
running in many developer and CI environments.

**Recommendation:** Remove the API-key skip from the PPUBS module. Keep key
gating only on ODP, PTAB, and DSAPI tests.

### IR-08 — Configuration and README documentation have drifted

**Severity:** Low

**Locations:** `.env.example:4-5`, `.env.example:15`, `README.md:166-217`

- `.env.example` still includes the removed `PATENTSVIEW_API_KEY`.
- Its example user agent identifies version `0.2.3` rather than `0.11.1`.
- The README's ODP tool table omits `odp_download_document`.
- Three README prompt names do not match the names advertised over MCP:
  `patent_validity_analysis`, `competitor_portfolio_analysis`, and
  `ptab_proceeding_research`.
- The server advertises the MCP SDK version in `serverInfo.version`, not the
  application package version, which can confuse client telemetry.

**Recommendation:** Refresh `.env.example` and README from the actual capability
registry, and explicitly expose `PACKAGE_VERSION` as the MCP server version if
supported by the selected SDK.

## Verification Evidence

| Check | Result |
|---|---|
| Python syntax compilation | Passed |
| Unit suite | 93 passed |
| Live PPUBS smoke suite | 4 passed |
| MCP stdio initialization | Passed |
| Advertised tools | 34 |
| Advertised concrete resources | 4 |
| Advertised resource templates | 3 |
| Advertised prompts | 6 |
| Unit-test coverage measurement | 42% overall |
| Fresh install with declared dependency range | Failed on MCP 2.0 import |
| Unit suite with MCP 1.28.1 | 93 passed |
| Dependency audit with MCP 1.28.1 | No MCP advisories found |

The PPUBS tests exercised live search, application search, full-patent
retrieval, and PDF download. ODP, PTAB, and DSAPI live smoke tests were not run
because no genuine USPTO API key was available in the review environment.

## Recommended Remediation Order

1. Bound MCP to `>=1.28.1,<2` and regenerate the lockfile.
2. Redact credentials in HTTP logs.
3. Fix oversized-response data loss and add a regression assertion.
4. Forward `aclose()` through `LoggingTransport`.
5. Add pagination bounds, input validation, and consistent response handling.
6. Repair or remove the assertion-free legacy integration tests.
7. Remove the unnecessary PPUBS API-key test gate.
8. Refresh `.env.example`, README capability names, and server version metadata.

## Suggested Release Gate

Before the next release:

```bash
uv lock --upgrade-package mcp
uv run pytest
uv run pytest -m smoke
uv run pip-audit
uv build
uv run twine check dist/*
```

At minimum, IR-01 through IR-04 should be resolved before publishing another
package version.
