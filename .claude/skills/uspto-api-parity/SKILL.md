---
name: uspto-api-parity
description: Check whether this server still covers every API in USPTO's ODP catalog, and refresh the baseline snapshot after fixing drift. Use when asked about API drift, the drift GitHub Action, missing endpoints, or the coverage script.
---

# Staying at Parity with USPTO's API Catalog

USPTO's published catalog (six OpenAPI definitions at data.uspto.gov/swagger)
is snapshotted in `scripts/uspto_api_baseline.json`. To check for drift:

```bash
uv run python scripts/check_uspto_api_coverage.py   # exit 2 = drift found
```

A weekly GitHub Action (`.github/workflows/uspto-api-drift.yml`) runs this
and opens a "USPTO API catalog drift detected" issue when USPTO adds or
removes APIs/endpoints. After bringing the server back to parity, refresh
the snapshot with `--update-baseline` and commit it.

Coverage method: diff the six OpenAPI definitions against the paths our
clients call (see the v0.12.0 entry in README.md's Version History).
