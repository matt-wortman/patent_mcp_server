# CLAUDE.md - Development Guidelines for USPTO Patent MCP Server

This file provides guidance for Claude Code and other AI assistants working on this project.

## Project Overview

This is a Model Context Protocol (MCP) server that provides access to USPTO patent data through multiple APIs. The server is built with FastMCP and uses async/await patterns throughout.

## Critical Rules

### Before Committing Changes

**IMPORTANT: Never commit and push changes without ensuring all tests pass.**

Before any commit:
```bash
# Run the full test suite
uv run pytest

# Expected output: All tests should pass (live smoke tests are skipped by default)
# Example: "99 passed, 43 deselected"
```

If tests fail:
1. Fix the failing tests before committing
2. Do not skip or delete failing tests unless the functionality has been intentionally removed
3. Update tests when function signatures change

### Test Organization

- **Unit tests** (`test/unit/`): Run by default, pure internal logic, no network
- **Live smoke tests** (`test/smoke/`): Real USPTO endpoints, skipped by default. PPUBS tests run without credentials; ODP, PTAB, and DSAPI tests require `USPTO_API_KEY`.

To run live smoke tests:
```bash
uv run pytest -m smoke
```

## Project Structure

```
src/patent_mcp_server/
├── patents.py              # Main server file with MCP tools, resources, and prompts
├── config.py               # Configuration management (environment variables)
├── constants.py            # Constants and enumerations
├── prompts.py              # Workflow prompt templates
├── resources.py            # Static resource data (CPC codes, status codes)
├── util/
│   ├── http.py             # Shared HTTP plumbing: client factory, retry policy, 429 handling, error mapping
│   ├── response.py         # Response normalization utilities
│   ├── errors.py           # Error handling utilities
│   ├── validation.py       # Input validation (plain functions raising ValueError)
│   └── logging.py          # Logging configuration
└── uspto/
    ├── ppubs_uspto_gov.py  # Patent Public Search client
    ├── api_uspto_gov.py    # Open Data Portal client
    ├── ptab_client.py      # PTAB proceedings client
    └── dsapi_client.py     # Data Set API client (office actions, citations, litigation)
```

## Code Conventions

### Function Naming

- **PPUBS tools**: `ppubs_*` (e.g., `ppubs_search_patents`)
- **ODP tools**: `odp_*` (e.g., `odp_get_application`)
- **PTAB tools**: `ptab_*` (e.g., `ptab_search_proceedings`)
- **DSAPI tools**: `dsapi_*` (e.g., `dsapi_search_office_actions`)

### Parameter Naming

- Use `query` not `q` for search queries
- Use `app_num` for application numbers
- Use `patent_number` for patent numbers
- Use `offset` and `limit` for pagination

### Error Handling

All tools should return a dictionary with consistent structure:
```python
# Success
{"success": True, "results": [...], "total": N, ...}

# Error
{"error": True, "message": "Error description", "error_code": "CODE"}
```

Use `ApiError.create()` for error responses.

### Async Patterns

All API clients use async/await:
```python
async def tool_name(...) -> Dict[str, Any]:
    async with SomeClient() as client:
        return await client.method(...)
```

## Testing Guidelines

### Writing Unit Tests

- Unit tests cover pure internal logic only (validation, error mapping,
  response normalization, header parsing) — no network, and **no mocked
  upstream HTTP** (see the Server Integrity Rule below)
- Use `@pytest.mark.unit` marker
- Test files go in `test/unit/`

### Writing Live Smoke Tests

- Anything that crosses the network gets a real smoke test against a stable
  fixture (a known patent/application number)
- Use `@pytest.mark.smoke` marker; place in `test/smoke/`
- Skipped by default; ODP/PTAB/DSAPI modules skip themselves when
  `USPTO_API_KEY` is not set, PPUBS runs without credentials

## Dependencies

Managed via `pyproject.toml`. Key dependencies:
- `mcp[cli]` - FastMCP server framework (pulls in pydantic)
- `httpx[http2]` - Async HTTP client
- `tenacity` - Retry logic
- `python-multipart` - not imported here; a pinned security floor for a
  transitive dependency of mcp (CVE-2026-24486)

To add a dependency:
```bash
uv add package-name
```

## Configuration

Environment variables are loaded from `.env` file:
- `USPTO_API_KEY` - Required for most tools
- `LOG_LEVEL` - Logging verbosity

See `config.py` for all options.

## Common Tasks

### Adding a New Tool

1. Add the function to `patents.py` with `@mcp.tool()` decorator
2. Follow naming conventions (`prefix_action`)
3. Add comprehensive docstring with "USE THIS TOOL WHEN" guidance
4. Add unit tests in `test/unit/`
5. Run tests before committing

### Updating an Existing Tool

1. Update the function signature
2. Update docstring if behavior changed
3. Update tests to match new signature
4. Run all tests before committing

### Running the Server Locally

```bash
# Start the server
uv run patent-mcp-server

# Run in development mode with debug logging
LOG_LEVEL=DEBUG uv run patent-mcp-server
```

## Intentional Tool Overlaps

Two pairs of tools overlap on purpose — all four work, and each side serves a
different access pattern. Do not "deduplicate" them:

- `get_status_code` vs `dsapi_lookup_status_code` — both hit the DSAPI
  status-code dataset. `get_status_code` returns a compact
  `{code, description, stage}` answer; `dsapi_lookup_status_code` returns the
  raw DSAPI record shape.
- `get_cpc_info` (tool) vs `patents://cpc/{code}` (resource) — same CPC data;
  the tool is for model calls, the resource for @-mention lookups.

## Server Integrity Rule

A tool in this server exists if and only if it works against the live USPTO API. When upstream dies, delete the tool and bump the version — do not deprecate, do not leave warnings, do not keep a dead wrapper "for future migration." If USPTO publishes a successor API, that's a separate migration PR.

Tests follow the same rule: pure internal logic (validation, error mapping, response normalization) gets unit tests with no network. Anything that crosses the network gets a real smoke test against a stable fixture. No mocked upstream HTTP — it tests a fiction.

## Version History

- **v0.11.1** - PPUBS search default sort changed from `date_publ desc` to `score desc` (relevance). Live-verified: date ordering buried relevant hits under recent grants for common terms.
- **v0.11.0** - Full review + hardening pass. Fixed PPUBS concurrency bug (shared search template now deep-copied per query); PDF pipeline gained retry/session-refresh/429 protection, failed-print-job detection, and a poll timeout; PPUBS adopted the shared HTTP helpers (`util/http.py`); session-bootstrap failures now return a clear SESSION_ERROR instead of sending `caseId: null` upstream; version lookup no longer crashes uninstalled checkouts; error bodies capped at 1,000 chars; restored `python-multipart` CVE floor; dropped unused direct pydantic dep; deleted dead `test/config/` helper and stale doc claims.
- **v0.10.0** - Restored live-tools-only philosophy. Deleted dead stub wrappers for PatentsView, PTAB, Litigation, and Office Action tools that returned `API_UNAVAILABLE`. Reactivated 4 PTAB tools (`ptab_search_proceedings`, `ptab_get_proceeding`, `ptab_search_decisions`, `ptab_get_decision`) against live `api.uspto.gov` endpoints. Migrated DSAPI client from `developer.uspto.gov` to `api.uspto.gov`. Added `odp_download_document` for file wrapper PDF downloads. Added 37 live smoke tests across DSAPI, ODP, PPUBS, PTAB, and liveness probe. `check_api_status` rewritten as a real liveness probe.
- **v0.7.0** - PatentsView removed (upstream decommissioned). Mocked-upstream tests replaced with real smoke tests against live USPTO endpoints. check_api_status rewritten as real liveness probe.
- **v0.6.2** - DSAPI consolidation: merged enriched_citation_client, litigation_client, and office_action_client into single dsapi_client.py; documentation overhaul
- **v0.6.1** - Added 18 new PatentsView tools: patent text (granted + pre-grant), citations, related applications, CPC subclass/USPC/WIPO classifications, location search
- **v0.6.0** - PyPI release preparation
- **v0.5.0** - USPTO-only focus, renamed ODP tools with `odp_` prefix
- **v0.3.0** - Added PTAB, PatentsView, Office Actions, Citations, Litigation APIs
- **v0.2.2** - Centralized config, error handling, validation

## Reminders

1. **Always run tests before committing**
2. Keep docstrings up to date
3. Use consistent error handling
4. Follow async patterns
5. Don't introduce new dependencies without good reason
