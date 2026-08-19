# Refresh Progress — Handoff File

Read this file plus `uspto-refresh-and-simplification-plan.md` at the start of every session.
Sessions must not rely on any other session's conversation memory.

## Done (commit SHAs)

### Session 1 — Purge (completed 2026-08-19, branch `uspto-refresh`)

- `6d21a04` — dead-code purge + test-suite fixes:
  - Deleted: `test/fixtures/api_responses.py`, `test/fixtures/ppubs_responses.py`,
    `test/utils/` (whole dir), root `test/conftest.py` (every fixture had zero users —
    verified; the only fixture tests use, `tmp_download_dir`, lives inside
    `test/unit/test_response.py`), empty dirs `test/{errors,integration,mock,performance}/`.
  - Deleted: `PTABClient.search_interferences`/`get_interference`, `DsapiClient.get_fields`
    (+ its now-orphaned `Optional`/`HTTPMethods` imports).
  - `constants.py`: removed `SortOrders`, `Operators`, `PTABProceedingStatus`, and the four
    unused `Defaults` attributes (`DATASET_LIMIT`, `REQUEST_TIMEOUT`, `MAX_RETRIES`,
    `SESSION_EXPIRY_MINUTES`). **`Defaults` itself is still used** by ppubs + ptab clients.
  - `config.py`: removed `DEFAULT_TRUNCATE_RESULTS`.
  - `util/validation.py`: rewritten as two plain functions (Pydantic models gone); behavior
    and error-message substrings preserved — all 34 validation unit tests pass unchanged.
    The `isinstance(x, str)` guards are required (tests assert ints are rejected; Pydantic
    used to do this via type coercion failure).
  - `resources.py`: removed dead "interferences" entry from `DATA_SOURCES["ptab"]["coverage"]`.
  - `test/test_patents.py`: both network tests now `@pytest.mark.integration` (skipped by default).
  - `test/config/test_config.py`: `__test__ = False` stops collection.
  - `pytest.ini`: markers reduced to unit/integration/smoke/asyncio.
- `e1d7cf3` — packaging: single dev-dep declaration (kept `[dependency-groups]`, dropped
  `[project.optional-dependencies]`), dropped `python-multipart` (imported nowhere; after
  removal it is not even in the transitive tree — verified via `uv sync` lock diff),
  `h2` → `httpx[http2]`. `uv.lock` regenerated. Package-data `json/*.json` untouched.
- `c6cfb2c` — untracked `test/test_results/`, root `json/`, `pdfs/`, `screencap.gif`, stale
  dated plan doc; extended `.gitignore`; removed README GIF embed.

Exit gate: `uv run pytest` → **86 passed, 57 deselected**. Server module imports clean.

### Session 2 — Dedup + 429 handling (completed 2026-08-19, branch `uspto-refresh`)

- `f6f5c66` — shared HTTP helpers + 429 handling:
  - New `src/patent_mcp_server/util/http.py`:
    - `make_logged_client(headers)` — the httpx AsyncClient factory (LoggingTransport,
      http2, follow_redirects, config timeout) replacing the copy-pasted `__init__` blocks.
    - `_send(...)` — tenacity-decorated core (same network-retry policy as before, now in
      ONE place) with the 429 loop inside: reads `Retry-After` then
      `x-rate-limit-retry-after-seconds`, sleeps `min(delay, 60)` (default backoff
      `5s × (attempt+1)` when headers absent/unparseable), retries up to
      `config.MAX_RETRIES`, then returns a plain-English `RATE_LIMITED` ApiError (429).
      Other HTTP errors → the formerly-triplicated `ApiError.from_http_error` block.
    - `request_json(...)` and `request_bytes(...)` — public wrappers (JSON parse vs.
      binary content dict). `request_bytes` serves ODP `download_file` (4 req/min limit).
  - `api_uspto_gov.py`, `ptab_client.py`, `dsapi_client.py` converted onto the helpers.
    Client names, method signatures, and output shapes unchanged (verified: patents.py
    call sites are `make_request`/`download_file`/`build_query_string`, `dsapi.search`,
    and the 4 public PTAB methods — none touched).
  - `dsapi_client.py`: hardcoded `DSAPI_BASE_URL` removed → `config.API_BASE_URL`.
  - New `test/unit/test_http.py` — 6 unit tests for the pure Retry-After parsing logic
    (no network; constructs `httpx.Response` objects directly, no mocked transport).

Exit gate: `uv run pytest` → **92 passed, 57 deselected**; live smoke
`uv run pytest test/smoke/test_{odp,ptab,dsapi}_smoke.py -m smoke` → **32 passed** (13s).

### Session 3 — Upstream port + currency polish (completed 2026-08-19, branch `uspto-refresh`)

- `3c77b3f` — PPUBS session-race fix (faithful port of upstream `2ea55322`) + currency:
  - `ppubs_uspto_gov.py`: `asyncio.Lock` serializes session establishment with a
    double-checked cache re-check (`get_session` → `_establish_session`); 403 refresh
    goes through `_refresh_session(stale_token)` which skips re-establishing when
    another call already replaced the token; the `X-Access-Token` now travels
    per-request (`_with_auth` in `make_request`, `_auth_headers()` on the three raw
    `client.post`/`build_request` calls in the PDF pipeline) instead of being written
    into shared client headers; `_ensure_case_id()` returns a stable local case id to
    `run_query`/`_request_save` so a concurrent refresh can't swap it mid-assembly.
  - Version drift fixed at the root: `config.PACKAGE_VERSION = importlib.metadata.version("patent_mcp_server")`;
    `USER_AGENT` default now interpolates it; `patents.py` docstring no longer carries
    a version number at all.
  - `dsapi_client.py` module docstring: stale "may 404" legacy-dataset warnings
    removed (all six datasets confirmed live 2026-08-19); dead fields-endpoint line
    dropped (its method was deleted in Session 1).
  - README: dead `developer.uspto.gov/api-catalog` PTAB link → `https://data.uspto.gov/apis/ptab-api`
    (old host no longer resolves at all — curl gets connection failure; new URL
    returns 200, verified before writing).

Exit gate: `uv run pytest` → **92 passed, 57 deselected**;
`uv run pytest test/smoke/test_ppubs_smoke.py -m smoke` → **4 passed** (7.6s) —
search, application search, full document, and PDF download all live through the
new lock/per-request-token code path. `config.USER_AGENT` verified to render
`patent-mcp-server/0.10.0` from package metadata.

### Session 4 — Fable 5 code review + release (completed 2026-08-19, branch `uspto-refresh`)

- `4d0e4e2` — v0.11.0: review fixes + release (tag `v0.11.0`).
  Review ran as 8 parallel finder angles (line-by-line, removed-behavior audit,
  cross-file tracer, reuse, simplification, efficiency, altitude, conventions)
  plus a manual pass over patents.py; 23 verified findings filed. Fixes:
  - **PPUBS concurrency bug**: `run_query` deep-copies the search template
    (shallow `.copy()` shared the nested `query` dict between concurrent
    searches — one search could silently run another's query).
  - **PPUBS PDF pipeline**: all three raw HTTP calls now go through
    `make_request` (retry + 403 refresh + 429); FAILED print jobs detected;
    poll loop bounded by `Defaults.PRINT_POLL_MAX_ATTEMPTS` (60 × 1s);
    `_auth_headers()` removed (make_request injects the token).
  - **PPUBS 429**: shares `util/http.rate_limit_delay` + `rate_limit_error`
    (honors Retry-After, float parse, MAX_RETRIES attempts, 60s cap) and the
    exported `uspto_retry` tenacity policy; client built via
    `make_logged_client` — PPUBS now fully on the shared helpers.
  - `_ensure_case_id` returns a SESSION_ERROR ApiError when session bootstrap
    fails (callers no longer send `caseId: null` upstream).
  - `config.PACKAGE_VERSION` wrapped in try/except PackageNotFoundError →
    "0.0.0+uninstalled" fallback (was an import-time crash for uninstalled
    checkouts).
  - `check_api_status` DSAPI probe now form-POSTs the status-codes *records*
    endpoint the tools actually use (the old /fields probe route is not part
    of the supported surface).
  - `request_json` wraps non-dict JSON as `{"results": ...}` (top-level array
    would have crashed `is_error`).
  - Error bodies capped at 1,000 chars in logs and error dicts
    (`MAX_ERROR_BODY_CHARS`); debug body dumps guarded by `isEnabledFor`
    (f-strings were decoding every response body at any log level).
  - Dead code: pydantic/os/RetryError and several patents.py imports removed;
    `test/config/` deleted (zero users); tautological cap test replaced with
    real clamp tests (cap now lives inside `rate_limit_delay`).
  - Packaging: **python-multipart>=0.0.22 floor restored** — Session 1's
    "not in the transitive tree" note was WRONG (mcp 1.23.1 requires it);
    unused direct pydantic dep dropped; version 0.11.0.
  - Docs: README (four sources, no interferences/appeals, v0.11.0 history),
    CLAUDE.md (util/http.py in map, plain-function validation, smoke-test
    guidance replacing the mocked-HTTP instruction, intentional tool
    overlaps documented, v0.11.0 history).

Exit gate: `uv run pytest` → **93 passed, 57 deselected**; full smoke
`uv run pytest test/smoke/ -m smoke` → **37 passed** (21s, includes live PDF
download through the new make_request path); MCP stdio E2E (fresh server,
34 tools listed, one tool per backend + liveness probe, all four backends
HTTP 200) → **PASS**. Tagged `v0.11.0`.

## Reference — upstream PPUBS field codes (recorded per plan; do NOT adopt)

Ours (verified working live 2026-08-19): `.ttl.` `.abst.` `.aclm.` `.isd.` `.an.`
Upstream v1.1.1 uses instead: `.ti.` `.ab.` `.clm.` `.as.` `@pd` `@ad`
Fallback only — if PPUBS field syntax ever breaks, try upstream's codes before
deeper debugging. No code change now (plan guardrail: no speculative rewrite).

## Decisions made

- **Working branch is `uspto-refresh`** (created off `main` at `82c748b`). All sessions
  continue on this branch; merge to `main` after Session 4's release gate.
- `.gitignore` artifact patterns are **root-anchored** (`/json/`, `/pdfs/`) because an
  unanchored `json/` would match load-bearing `src/patent_mcp_server/json/` package data.
- `SEARCH_SYNTAX_GUIDE` constant kept — it is the value returned by the used accessor
  `get_search_syntax_guide()`; nothing to remove there.
- `test/config/` kept (plan said stop collection, not delete), though `TestConfig` and
  `test_data.json` have no callers outside their own file — a Session 4 review candidate.
- `python-multipart` removal recorded: it was a promoted transitive pin for CVE-2026-24486;
  nothing in the resolved dependency tree requires it anymore.
- (S2) **DSAPI client now uses HTTP/2** — the shared `make_logged_client` factory always
  passes `http2=True`; the old DSAPI `__init__` didn't. Deliberate: same host as ODP/PTAB,
  and all 12 DSAPI smoke tests pass live over h2. If DSAPI ever misbehaves, this is the
  one transport-level behavior change to suspect.
- (S2) **PPUBS client untouched** — plan says adopt helpers only if it drops in cleanly;
  its session/token/cookie handling and existing 429 logic make that a non-trivial fit,
  and Session 3 rewrites its session handling anyway. Revisit after the upstream port
  (Session 3 or 4), not before.
- (S2) `download_file` (ODP) HTTPStatusError path now also tries to parse the error body
  as JSON (via the shared block) — previously it passed only `response_text`. Same output
  shape (ApiError dict), strictly better messages.
- (S2) Pre-existing Pyright errors in `ptab_client.py` fixed by annotating the two params
  dicts as `Dict[str, Any]` (was inferred `Dict[str, int]`).
- (S3) **Kept our PDF download URL `/api/print/save/{pdf_name}`** — upstream uses
  `/api/internal/print/save/`, but that difference predates the ported commit (it was a
  context line, not part of the fix), and ours passes the live PDF smoke test. Only the
  concurrency fix was ported, exactly as the plan requires.
- (S3) **PPUBS did NOT adopt `util/http.py`** — the ported fix is deliberately grafted
  onto the client's existing structure so it stays a faithful port. Whether PPUBS should
  share the helpers is a Session 4 review question, not settled here.
- (S3) `PACKAGE_VERSION` lives in `config.py` (module-level, imported alongside `config`).
  If the package is ever run without being installed, the import raises
  `PackageNotFoundError` — acceptable because `uv run` always installs the project.

## Surprises

- Pre-existing Pyright warnings in `test/test_patents.py` (possibly-unbound `client`, etc.) —
  manual-script heritage; harmless, tests are integration-skipped.
- `test/manual/` is gitignored but present locally (`test_odp_download.py`) — left alone.
- README line 3 still links retired `developer.uspto.gov/api-catalog` — deliberate; that fix
  is Session 3 scope.
- (S2) None. Conversion was clean; smoke suite green on first run after conversion.
  User confirmed the MyODP profile action item is done (account verified 2026-08-19),
  so the API key is in good standing for future sessions' smoke tests.
- (S3) Upstream commit `2ea55322` bundles the session fix with a streamable-http
  transport feature, protocol-layer tests, and a shutdown fix — none of that was
  ported (out of scope; our server is stdio-only). Upstream also grew branches
  (`fix/ppubs-live-api-drift`, `feature/federal-litigation-documents`) that may be
  worth a look in a future cycle, after this refresh lands.
- (S3) The IDE's Pyright surfaces pre-existing warnings in `ppubs_uspto_gov.py` /
  `patents.py` (Union narrowing on `make_request` return, `sources=None` default) and
  falsely reports `util.http` unresolved (stale IDE env — tests import it fine).
  Session 4's review should consider the real ones; ignore the import ghost.

## Known limitations (reviewed Session 4; deliberately NOT fixed)

- **403-refresh retry re-sends the original body** — after a session refresh,
  the retried request's JSON body still carries the dead session's `caseId`.
  Fixing generically requires re-invoking the caller; disproportionate for a
  30-minute session-expiry window. If PPUBS searches ever fail specifically
  right after "Session expired, refreshing" log lines, this is why.
- **Cookie-jar reset race** — `_establish_session` resets the shared cookie
  jar while other requests may be in flight; a late response can write stale
  cookies into the fresh jar. Narrow window; full fix needs per-request
  cookie isolation. Accepted residual risk (upstream's design too).
- **429 wait budget vs client timeouts** — a tool call can sleep up to
  60s × MAX_RETRIES on persistent 429s (plan's chosen design). MCP clients
  with short per-call timeouts may give up first; the server still returns a
  clean RATE_LIMITED error when it exhausts retries.
- `test/test_patents.py` Pyright possibly-unbound warnings — manual-script
  heritage, integration-skipped, harmless.

## Next session's tasks verbatim

Sessions 1–4 are COMPLETE; v0.11.0 is tagged on `uspto-refresh`. Remaining:

1. **Merge to `main`** (plan: "merge to main after Session 4's release gate").
   The release gate passed; merge is ready when the user wants it.
2. **Session 5 (optional, per plan)**:
   - Live-probe first-party `GET /api/v1/patent/status-codes` (unverified, from
     a third-party client); if real, consider replacing the 2018-vintage
     `oce_patent_examination_status_codes` dataset behind the existing tools.
   - Add a PTAB document-download tool using `fileDownloadURI`
     (`api.uspto.gov/api/v1/patent/ptab-files/...`), routed through the shared
     429 handler (downloads limited to 4 req/min).
   - Each item independently skippable. Exit: pytest + new smoke tests green → commit.
3. Possible future cycle (recorded S3): upstream branches
   `fix/ppubs-live-api-drift` and `feature/federal-litigation-documents`.

Note for future sessions: there is no `.env` in the repo — `USPTO_API_KEY`
lives in the user's shell environment. MCP clients spawning the server get a
sanitized env, so their config must set the key explicitly (the user's
existing client config evidently does; smoke tests inherit the shell env).
