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

### Staying at Parity with USPTO's API Catalog

Use the `uspto-api-parity` skill (`.claude/skills/uspto-api-parity/SKILL.md`) to check for drift and refresh the baseline.

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

See the Version History section of README.md and `git log`.
