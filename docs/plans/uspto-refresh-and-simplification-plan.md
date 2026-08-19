# Patent MCP Server — Currency Check, Code Review, and Simplification

## Context

~4 months since active work (v0.10.0, April 2026, migrated onto api.uspto.gov). Goals:

1. **Currency** — bring the server in line with USPTO API changes through August 2026.
2. **Code review** — thorough Fable 5 review of what remains after cleanup.
3. **Simplification** — remove bloat and duplication; keep ALL working functionality.
4. **Session discipline** — every work session stays well under 200K tokens, with a committed
   handoff file between sessions so no session depends on another's conversation memory.

**Good news from live probes (2026-08-19): all four backends (PPUBS, ODP, PTAB, DSAPI) returned
200 with real data.** Nothing is currently broken. The work is hardening, cleanup, and one bug fix.

## ⚠️ User action item (not code — do this yourself)

USPTO made four extra profile fields mandatory **effective 2026-08-18 (yesterday)**; non-compliance
revokes ODP access **and your API key**. Every `odp_*`, `ptab_*`, `dsapi_*` tool depends on that key.
Verify your account at https://data.uspto.gov/myodp. (Source: ODP release-4.0 notes; field names
reported second-hand as Job Title, Organization Name, Organization Type, Intended Use.)

## Established findings (trust these; don't re-explore)

### Currency gaps
- **No 429/rate-limit handling** in `api_uspto_gov.py`, `ptab_client.py`, `dsapi_client.py`
  (they retry only Timeout/NetworkError). USPTO documents 60 req/min per key and **4 req/min for
  PDF/ZIP downloads**. Only the PPUBS client handles 429 (via `x-rate-limit-retry-after-seconds`).
- **Fork is behind upstream** (`riemannzeta/patent_mcp_server` v1.1.1, 2026-08-05; `upstream`
  remote exists but is stale-fetched). Upstream commit `2ea55322` fixes a **real concurrency bug
  we still have**: `PpubsClient.get_session()` resets shared `self.client.cookies`
  (ppubs_uspto_gov.py:100) and writes the token into shared headers (:127) with no lock — races
  under concurrent tool calls. Wholesale merge is NOT viable (our b54c374 rewrote the client
  layer; ~3,000-line divergence) — port the fix manually.
- PPUBS field codes: ours (`.ttl. .abst. .aclm. .isd. .an.`) verified working live 2026-08-19;
  upstream uses `.ti. .ab. .clm. .as. @pd @ad`. Keep ours; record upstream's as fallback.
- Stale strings: `patents.py` docstring says 0.7.0, `config.USER_AGENT` says 0.7.0, pyproject says
  0.10.0. `dsapi_client.py:20-24` "may 404" warnings now false (both datasets return 200 with data).
  `README.md:3` links retired developer.uspto.gov (Developer Hub decommissioned ~2026-06-05).

### Confirmed dead code (zero callers, verified)
- `test/fixtures/api_responses.py` (368), `test/fixtures/ppubs_responses.py` (247),
  `test/utils/assertions.py` (220), `test/utils/helpers.py` (194) — 1,029 lines imported by nothing.
- `util/validation.py`: 5 unused Pydantic models (~85 of 133 lines); only
  `validate_patent_number`/`validate_app_number` are used.
- `PTABClient.search_interferences`/`get_interference` (~45 lines; also point at an undocumented
  path); `DsapiClient.get_fields` (~42 lines).
- `constants.py`: `SortOrders`, `Operators`, `PTABProceedingStatus` unused; `Defaults.*` duplicates
  config.py values. `config.DEFAULT_TRUNCATE_RESULTS` never read. `SEARCH_SYNTAX_GUIDE` constant
  export unused (accessor is used). `DATA_SOURCES` still advertises dead "interferences".
- Empty test dirs (`test/integration/`, `mock/`, `performance/`, `errors/`); 6 pytest.ini markers
  never applied; mostly-unused conftest fixtures.

### Test-suite defects
- `test/test_patents.py`: 2 unmarked async tests make REAL network calls in the default run.
- `test/config/test_config.py`: `TestConfig` helper class wrongly collected by pytest.

### Packaging defects (pyproject.toml)
- Dev deps declared twice (`[project.optional-dependencies]` AND `[dependency-groups]`) with
  **conflicting pytest-asyncio pins**.
- `python-multipart` imported nowhere (CVE-2026-24486 transitive pin promoted to direct dep).
- `h2` should be expressed as `httpx[http2]` (three clients pass `http2=True`).
- NOTE: package-data `json/*.json` is CORRECT — `src/patent_mcp_server/json/search_query.json`
  is load-bearing (read at ppubs_uspto_gov.py:76). Do not touch it.

### Duplication (~1,230 client lines)
- Identical tenacity `@retry(...)` block copy-pasted 6×; ~25-line error-handling block triplicated
  byte-for-byte (api_uspto_gov.py:151-174, ptab_client.py:107-130, dsapi_client.py:205-227);
  identical `__aenter__/__aexit__/close()` ×4 (leave these — trivial).
- `dsapi_client.py:45` hardcodes `https://api.uspto.gov` instead of `config.API_BASE_URL`.
- Tool overlaps (`get_status_code` vs `dsapi_lookup_status_code`; `get_cpc_info` vs the
  `patents://cpc/{code}` resource): **document, do not remove** — all work.

### Committed artifacts to untrack (user approved removing ALL, including the GIF)
`test/test_results/` (~28K lines), root `json/` dumps, `pdfs/` (1.8 MB), `screencap.gif` (1.7 MB),
stale tracked plan `docs/plans/2026-02-10-fix-dependabot-security-vulnerabilities-plan.md`.
If README embeds screencap.gif, remove/replace that reference too. Extend `.gitignore`.
docs/ personal work product is untracked — leave alone.

## Design decisions (already made — do not relitigate)

- **Client dedup = shared helpers, NOT a base class.** New small module
  `src/patent_mcp_server/util/http.py` with:
  - `async request_json(client, method, url, **kwargs)` — wraps the request with the single retry
    policy, the (currently triplicated) error-handling block, and the 429 loop.
  - `make_logged_client(headers)` — the httpx AsyncClient + LoggingTransport + http2 + timeout
    factory replacing 4 copy-pasted `__init__` blocks.
  - Clients keep their names, shapes, public methods, and output formats. PPUBS adopts it only if
    it drops in cleanly (it already handles 429). Net: ~150–200 lines removed, zero interface change.
- **429 handling = plain loop inside `request_json`** (NOT tenacity gymnastics): on 429, read
  `Retry-After` (fallback `x-rate-limit-retry-after-seconds`), `asyncio.sleep(min(delay, 60))`
  with default backoff if header absent, retry up to `config.MAX_RETRIES`, then return an
  `ApiError` 429 with a plain-English message. ~12 lines, one place, covers ODP+PTAB+DSAPI.
- **Upstream = manual port of one fix** (`2ea55322` session lock + per-request token), not a merge.
  Add `asyncio.Lock` in `PpubsClient.__init__`; wrap `get_session()` body with double-checked
  expiry re-check; pass `X-Access-Token` per-request instead of mutating shared headers.
  `git fetch upstream` first and read the real commit — port faithfully, not from memory.
- **Do NOT normalize DSAPI output into ResponseEnvelope** — output-shape changes of working tools
  are regressions. Keep `from_ppubs/from_odp/from_ptab` externally identical.
- **Version strings**: read once via `importlib.metadata.version("patent_mcp_server")` so they
  can never drift again.

### What NOT to do (overcomplication guardrails)
No base-class hierarchy, client registry, or plugin system. No wholesale upstream merge. No
splitting patents.py into modules. No renaming/removing user-facing tools. No mocked-HTTP tests
(project rule: live smoke + pure-function unit tests only). No generic rate limiter (token bucket,
aiolimiter) — the Retry-After loop is the whole requirement. No speculative PPUBS field-code
rewrite. No new dependencies.

## Session plan (each sized well under 200K tokens)

**Handoff artifact:** `docs/plans/refresh-progress.md`, committed at the end of every session.
Fixed sections: *Done (commit SHAs) / Decisions made / Surprises / Next session's tasks verbatim.*
Each session starts by reading ONLY this plan + the progress file + the files it touches.
No session re-explores the codebase.

### How the 200K token ceiling is enforced

1. **One session = one fresh conversation.** Run each session after `/clear` (or in a new
   window). Context starts at ~0; only what the session deliberately loads counts.
2. **Files replace conversation memory.** The next session's entire prompt is:
   *"Read docs/plans/uspto-refresh-and-simplification-plan.md and
   docs/plans/refresh-progress.md, then execute Session N."* Nothing outside those two files
   plus the files being edited is ever required — no cross-session context accumulation.
3. **No re-exploration.** All discovery is baked into the "Established findings" section above.
   Sessions read only the files they change.
4. **Sized by required reading.** Estimates below reflect what each session must actually read;
   all leave ≥80K headroom. The review session runs LAST, after the purge shrinks src/, so the
   full read fits easily.
5. **Heavy work goes to subagents.** Bulky reads (upstream commits, full smoke-suite output,
   the `/code-review` pass) run in subagents; only their short summaries enter the main context.
6. **Tripwire at ~150K.** If a session's context approaches ~150K (check with `/context`), stop
   at a clean point: run tests, commit, write the handoff via the `session-handoff` skill, and
   continue in a fresh session. An unplanned split costs nothing — it uses the same handoff
   protocol as a planned session boundary.

### Session 1 — Purge (deletions + hygiene; est. 40–60K tokens)
1. Delete all confirmed-dead code listed above (test fixtures/utils, Pydantic models, interference
   methods, `DsapiClient.get_fields`, unused constants/config, empty test dirs, unused markers/fixtures).
2. Remove "interferences" from `DATA_SOURCES`.
3. Fix test defects: mark `test_patents.py` network tests so default run skips them; stop
   collection of `TestConfig`.
4. Fix pyproject: single dev-dep declaration, drop `python-multipart` (record decision),
   `h2` → `httpx[http2]`. Leave package-data alone.
5. Untrack artifacts + update `.gitignore` + fix README GIF reference if present.
6. Exit: `uv run pytest` green → commit(s) → create + commit progress file.

### Session 2 — Dedup + 429 handling (est. 60–80K tokens)
1. Create `util/http.py` (`request_json` with retry/error/429; `make_logged_client`).
2. Convert `api_uspto_gov.py`, `ptab_client.py`, `dsapi_client.py` onto it.
3. Fix `dsapi_client.py:45` hardcoded base URL → `config.API_BASE_URL`.
4. Exit: pytest green + live smoke (`uv run pytest -m smoke` for ODP/PTAB/DSAPI files) →
   commit → update progress file.

### Session 3 — Upstream port + currency polish (est. 40–60K tokens)
1. `git fetch upstream`; read commit `2ea55322`; port the PPUBS session-race fix.
2. Live-verify a PPUBS search + PDF download.
3. Fix version drift via `importlib.metadata`; delete stale "may 404" comments; fix README dead
   developer.uspto.gov link; note upstream's alternate field codes in the progress file.
4. Exit: pytest + PPUBS live smoke green → commit → update progress file.

### Session 4 — Fable 5 code review + release (est. 80–120K tokens)
1. Full review of the now-smaller src/ (~2,300 lines) and test/: error paths, tool-boundary
   validation, docstring accuracy (docstrings ARE the MCP tool descriptions — fix falsehoods,
   don't gut them), log hygiene, async correctness. Use the `/code-review` skill at high effort
   plus a manual pass over patents.py.
2. Document (not remove) the tool-naming overlaps.
3. Fix findings; update CLAUDE.md/README version history; bump to v0.11.0; tag.
4. Exit: pytest green + FULL live smoke pass → commit + tag.

### Session 5 (optional — only after 1–4 land clean; est. 40–80K tokens)
1. Live-probe first-party `GET /api/v1/patent/status-codes` (unverified, from a third-party
   client); if real, consider replacing the 2018-vintage `oce_patent_examination_status_codes`
   dataset behind the existing tools.
2. Add a PTAB document-download tool using `fileDownloadURI`
   (`api.uspto.gov/api/v1/patent/ptab-files/...`), routed through the shared 429 handler
   (downloads are limited to 4 req/min).
3. Each item independently skippable. Exit: pytest + new smoke tests green → commit.

Ordering rationale: deletions first (Sessions 2–4 read ~1,400 fewer lines), dedup before the
upstream port (fix lands on final client shape), review last (covers what actually ships).

## Verification

- After every session: `uv run pytest` (86 unit tests, no network) must be green before commit.
- Sessions 2–4: relevant live smoke files (`uv run pytest test/smoke/... -m smoke`) — requires
  `USPTO_API_KEY` in `.env`, which itself requires the MyODP profile fix above.
- Session 4 end: full smoke suite (all 37) + liveness probe tool as the final release gate.
- End-to-end: restart the MCP server (`uv run patent-mcp-server`) and exercise one tool per
  backend through MCP (`ppubs_search_patents`, `odp_get_application_metadata`,
  `ptab_search_proceedings`, `dsapi_search_office_actions`).

## Critical files
- `src/patent_mcp_server/uspto/{ppubs_uspto_gov,api_uspto_gov,ptab_client,dsapi_client}.py`
- `src/patent_mcp_server/patents.py`, `config.py`, `constants.py`, `resources.py`
- `src/patent_mcp_server/util/{validation,response,errors}.py` + new `util/http.py`
- `pyproject.toml`, `pytest.ini`, `test/test_patents.py`, `test/config/test_config.py`
- Handoff: `docs/plans/refresh-progress.md`
