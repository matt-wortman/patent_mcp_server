# USPTO Patent MCP Server

A [FastMCP server](https://github.com/modelcontextprotocol/python-sdk/tree/main/src/mcp/server/fastmcp) for accessing United States Patent and Trademark Office (USPTO) patent and patent application data through multiple APIs including the [Patent Public Search](https://www.uspto.gov/patents/search/patent-public-search) API, the [Open Data Portal (ODP) API](https://data.uspto.gov/home), [PTAB API v3](https://developer.uspto.gov/api-catalog), and the [Data Set API (DSAPI)](https://api.uspto.gov) for office actions, enriched citations, and patent litigation. Using this server, Claude Desktop can pull data from USPTO APIs, search through PTAB proceedings and decisions, analyze patent litigation, research prosecution history, and more.

For an introduction to MCP servers see [Introducing the Model Context Protocol](https://www.anthropic.com/news/model-context-protocol).

Special thanks to [Parker Hancock](https://github.com/parkerhancock), author of the amazing [Patent Client project](https://github.com/parkerhancock/patent_client), for [blazing the trail](https://github.com/parkerhancock/patent_client/issues/63) to understanding of the string of requests and responses needed to pull data through the Public Search API.

## Features

This server provides **~34 tools** across 4 USPTO data sources for:

1. **Patent Search** - Full-text search of granted patents and published applications via PPUBS
2. **Full Text Documents** - Get complete text of patents including claims, description, and specification
3. **PDF Downloads** - Download patents as PDF files (Claude Desktop doesn't support this as a client currently)
4. **Prosecution History** - Access office actions, transactions, and file wrapper data
5. **PTAB Proceedings** - Search and retrieve Patent Trial and Appeal Board proceedings (IPR, PGR, CBM), decisions, and appeals
6. **Office Actions & Rejections** - Full-text office actions with §101/102/103/112 rejection flags via DSAPI
7. **Patent Litigation** - Search 74,000+ district court patent cases via DSAPI
8. **Citation Analysis** - Enriched citation data, examiner/applicant provenance, and citation metrics
9. **Patent Family Data** - Continuity information, foreign priority, and related applications

## API Sources

This server interacts with five USPTO patent data sources:

| Source | Description | Auth Required |
|--------|-------------|---------------|
| **ppubs.uspto.gov** | Full text documents, PDF downloads, advanced search (daily updates) | No |
| **api.uspto.gov (ODP)** | Metadata, continuity, transactions, assignments, prosecution history | Yes (ODP API Key) |
| **PTAB API v3 (ODP)** | IPR/PGR/CBM proceedings, decisions, appeals, interferences | Yes (ODP API Key) |
| **DSAPI (api.uspto.gov)** | Office actions, enriched citations, patent litigation, status codes | Yes (ODP API Key) |

## Prerequisites

- **Python 3.10-3.13** (3.12 recommended)
- **Claude Desktop** (for integration). Other models and MCP clients have not been tested.
- **[UV](https://docs.astral.sh/uv/)** for Python version and dependency management

If you're a Python developer but still unfamiliar with uv, you're in for a treat. It's faster and easier than having a separate Python version manager (like pyenv) and setting up, activating, and maintaining virtual environments with venv and pip.

If you don't already have uv installed:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/riemannzeta/patent_mcp_server
   cd patent_mcp_server
   ```

2. Install dependencies with uv:
   ```bash
   uv sync
   ```

3. Verify installation:
   ```bash
   uv run patent-mcp-server
   ```
   Should output:
   ```
   INFO     Starting USPTO Patent MCP server with stdio transport
   ```

## API Key Setup

### USPTO ODP API Key (Required for most tools)

To use the api.uspto.gov tools (ODP, PTAB), you need an Open Data Portal API key. Without it, these endpoints return `403 Forbidden`.

1. Create a USPTO.gov account at [data.uspto.gov](https://data.uspto.gov) (requires ID.me verification)
2. Once signed in, visit **"My ODP"** in the site navigation to get your API key
3. See the [Getting Started guide](https://data.uspto.gov/apis/getting-started) for detailed instructions

4. Create a `.env` file in the patent_mcp_server directory:
   ```bash
   USPTO_API_KEY=your_actual_key_here
   ```
   Note: The PPUBS tools will work without this API key.

## Configuration

The server can be configured using environment variables in your `.env` file. All settings are optional with sensible defaults:

```bash
# API Keys
USPTO_API_KEY=your_key_here

# Logging
LOG_LEVEL=INFO  # Options: DEBUG, INFO, WARNING, ERROR, CRITICAL

# HTTP Settings
REQUEST_TIMEOUT=30.0  # Request timeout in seconds
MAX_RETRIES=3         # Maximum number of retry attempts
RETRY_MIN_WAIT=2      # Minimum wait time between retries (seconds)
RETRY_MAX_WAIT=10     # Maximum wait time between retries (seconds)

# Session Management
SESSION_EXPIRY_MINUTES=30  # How long to cache ppubs sessions
ENABLE_CACHING=true        # Enable/disable session caching

# API Endpoints (usually don't need to change)
PPUBS_BASE_URL=https://ppubs.uspto.gov
API_BASE_URL=https://api.uspto.gov          # ODP API endpoint (NOT data.uspto.gov)
```

## Claude Desktop Configuration

To integrate this MCP server with Claude Desktop:

1. Update your Claude Desktop configuration file (`claude_desktop_config.json`):
   ```json
   {
     "mcpServers": {
       "patents": {
         "command": "uv",
         "args": [
           "--directory",
           "/Users/username/patent_mcp_server",
           "run",
           "patent-mcp-server"
         ]
       }
     }
   }
   ```

   You can find `claude_desktop_config.json` on a Mac by opening the Claude Desktop app, opening Settings (from the Claude menu or by Command + ' on the keyboard), clicking "Developer" in the sidebar, and "Edit Config."

2. Replace `/Users/username/patent_mcp_server` with the actual path to your patent_mcp_server directory.

When integrated with Claude Desktop, the server will be automatically started when needed and doesn't need to be run separately.

## Claude Code Configuration

To integrate this MCP server with Claude Code for a particular project, from the project root:

```shell
claude mcp add-json patents '{"command": "uv", "args": ["--directory", "/path/to/patent_mcp_server", "run", "patent-mcp-server"]}'
```

If you're already running Claude Code, you'll have to /exit and restart. Then /mcp to verify that it's configured.

## Available Tools

### Utility Tools
| Tool | Description |
|------|-------------|
| `check_api_status` | Check status of all USPTO APIs |
| `get_cpc_info` | Get CPC classification information |
| `get_status_code` | Look up USPTO status code meaning |

### Patent Public Search (ppubs.uspto.gov)
| Tool | Description |
|------|-------------|
| `ppubs_search_patents` | Search granted patents (full-text, daily updates) |
| `ppubs_search_applications` | Search published patent applications |
| `ppubs_get_full_document` | Get full patent document by GUID |
| `ppubs_get_patent_by_number` | Get patent's full text by number |
| `ppubs_download_patent_pdf` | Download patent as PDF |

### Open Data Portal (api.uspto.gov)
| Tool | Description |
|------|-------------|
| `odp_get_application` | Get basic application data |
| `odp_search_applications` | Search applications with filters |
| `odp_get_application_metadata` | Get comprehensive metadata |
| `odp_get_continuity` | Get patent family/continuity data |
| `odp_get_assignment` | Get ownership/assignment records |
| `odp_get_adjustment` | Get patent term adjustment data |
| `odp_get_attorney` | Get attorney/agent of record |
| `odp_get_foreign_priority` | Get foreign priority claims |
| `odp_get_transactions` | Get prosecution transaction history |
| `odp_get_documents` | Get file wrapper documents |
| `odp_search_datasets` | Search bulk data products |
| `odp_get_dataset` | Get dataset product details |

### PTAB API v3 (Patent Trial and Appeal Board)
| Tool | Description |
|------|-------------|
| `ptab_search_proceedings` | Search IPR/PGR/CBM proceedings |
| `ptab_get_proceeding` | Get proceeding details |
| `ptab_search_decisions` | Search trial decisions |
| `ptab_get_decision` | Get decision details |

### DSAPI (Data Set API) — Office Actions, Citations & Litigation
| Tool | Description |
|------|-------------|
| `dsapi_search_office_actions` | Full-text office actions for an application |
| `dsapi_search_rejections` | OA rejections with §101/102/103/112 flags and SME indicators |
| `dsapi_search_oa_citations` | References cited in office action paragraphs |
| `dsapi_search_enriched_citations` | Enriched citation metadata for an application |
| `dsapi_get_citation_details` | Advanced Lucene search across enriched citations |
| `dsapi_search_litigation` | Search 74K+ patent litigation cases by Lucene query |
| `dsapi_get_patent_litigation` | Search litigation by patent number (heuristic — see docstring) |
| `dsapi_lookup_status_code` | Decode a USPTO examination status code |
| `dsapi_list_status_codes` | List/search all 233 status codes |

### Resources and Prompts

The server also provides **MCP Resources** (accessible via @ mentions):
- `patents://cpc/{code}` - CPC classification information
- `patents://status-codes` - USPTO status code definitions
- `patents://sources` - Data source information
- `patents://search-syntax` - Query syntax guide

And **MCP Prompts** (workflow templates):
- `prior_art_search` - Comprehensive prior art search guide
- `patent_validity` - Patent validity analysis workflow
- `competitor_portfolio` - Competitor portfolio analysis
- `ptab_research` - PTAB proceeding research guide
- `freedom_to_operate` - FTO analysis workflow
- `patent_landscape` - Technology landscape mapping

## Testing

The project includes comprehensive test suites:

```bash
# Run unit tests (default - skips integration tests)
uv run pytest

# Run with verbose output
uv run pytest -v

# Run integration tests (requires network access)
uv run pytest -m integration

# Run all tests including integration
uv run pytest -m ""

# Run with coverage report
uv run pytest --cov=patent_mcp_server
```

Test results are stored in `/test/test_results/`.

### Development

To install development dependencies:
```bash
uv sync --dev
```

## Version History

### v0.10.0 (Current)
- Restored live-tools-only philosophy per the Server Integrity Rule
- Deleted dead stub wrappers for PatentsView, PTAB, Litigation, and Office Action tools that previously returned `API_UNAVAILABLE`
- Reactivated 4 PTAB tools (`ptab_search_proceedings`, `ptab_get_proceeding`, `ptab_search_decisions`, `ptab_get_decision`) against live `api.uspto.gov` endpoints
- Migrated DSAPI client from `developer.uspto.gov` to `api.uspto.gov`
- Added `odp_download_document` for file wrapper PDF downloads
- Added 37 live smoke tests across DSAPI, ODP, PPUBS, PTAB, and liveness probe
- `check_api_status` rewritten as a real liveness probe

### v0.7.0
- PatentsView removed (upstream decommissioned; HTTP 410 since 2026-04-20)
- Mocked-upstream unit tests replaced with real smoke tests against live USPTO endpoints
- `check_api_status` rewritten as real liveness probe

### v0.6.2
- Documentation quality overhaul: accurate tool counts, consolidated API source tables
- Consolidated office action, enriched citation, and litigation clients into single DSAPI client
- Added DSAPI to MCP resources (`patents://sources`, `patents://search-syntax`)
- Removed stale `office_actions` and `litigation` entries from `check_api_status`
- Fixed 4 stale tool name references in MCP prompt templates
- Updated API key registration instructions: keys are now obtained from [data.uspto.gov](https://data.uspto.gov) ("My ODP")
- Clarified that `api.uspto.gov` is the correct API endpoint (not `data.uspto.gov` which is the web portal)

### v0.6.1
- Extended tool surface across multiple USPTO data sources
- Fixed bug in `search_publications` method (pagination options not being passed)

### v0.6.0
- PyPI release preparation

### v0.5.0
- Focused on USPTO-only data sources
- Renamed ODP tools with `odp_` prefix for clarity
- Improved function signatures (using `query` instead of `q`)
- Enhanced test organization with proper integration test markers
- Updated validation with Pydantic models

### v0.3.0
- Added PTAB, Office Actions, Citations, and Litigation tools
- Comprehensive async client architecture

### v0.2.2
- Centralized configuration with environment variables
- Standardized error handling
- Input validation with Pydantic
- Retry logic with exponential backoff
- Session caching for PPUBS

## License

MIT
