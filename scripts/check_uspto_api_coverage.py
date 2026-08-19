#!/usr/bin/env python3
"""Detect drift between USPTO's published API catalog and our baseline.

USPTO publishes its API catalog as OpenAPI definitions listed in the
Swagger UI at https://data.uspto.gov/swagger/index.html. This script:

1. Fetches the Swagger initializer to discover the current list of API
   definitions (so brand-new APIs are caught, not just new endpoints).
2. Fetches each definition and extracts its endpoint paths.
3. Diffs the result against the checked-in baseline
   (scripts/uspto_api_baseline.json), which records the catalog as of the
   last time the server was brought to parity.

Exit codes:
    0 - catalog matches the baseline (no action needed)
    1 - script error (network failure, spec unparsable)
    2 - drift detected: new/removed APIs or endpoints (report on stdout)

When drift is reported, the update procedure is:
  - New endpoints: wrap them as tools (see CLAUDE.md "Adding a New Tool"),
    live-verify each, add smoke tests, bump the minor version.
  - Removed endpoints: per the Server Integrity Rule, delete the matching
    tools and bump the version.
  - Then regenerate the baseline:  python scripts/check_uspto_api_coverage.py --update-baseline

The data.uspto.gov WAF serves an HTML shell to non-browser user agents,
so requests must send a browser User-Agent. No API key is needed for the
specs themselves.
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import yaml

SWAGGER_BASE = "https://data.uspto.gov/swagger"
INITIALIZER_URL = f"{SWAGGER_BASE}/swagger-initializer.js"
BASELINE_PATH = Path(__file__).parent / "uspto_api_baseline.json"

# Non-browser user agents get the SPA/WAF shell instead of the file.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    if body.lstrip().lower().startswith("<!doctype html"):
        raise RuntimeError(
            f"{url} returned the HTML shell instead of the file — "
            "the WAF may have started blocking this User-Agent."
        )
    return body


def discover_definitions() -> dict:
    """Parse the Swagger initializer for {name: url} of every API definition."""
    js = fetch(INITIALIZER_URL)
    definitions = {}
    # Entries look like: { url: "https://...yaml", name: "Open Data Portal API" }
    import re
    for match in re.finditer(
        r'url:\s*"(?P<url>https://[^"]+)"\s*,\s*name:\s*"(?P<name>[^"]+)"', js
    ):
        definitions[match.group("name")] = match.group("url")
    if not definitions:
        raise RuntimeError(
            "No API definitions found in swagger-initializer.js — "
            "USPTO may have restructured the Swagger page."
        )
    return definitions


def extract_paths(spec_url: str) -> list:
    """Fetch one OpenAPI definition and return its sorted endpoint paths."""
    spec = yaml.safe_load(fetch(spec_url))
    paths = spec.get("paths") or {}
    return sorted(paths.keys())


def snapshot_catalog() -> dict:
    definitions = discover_definitions()
    catalog = {}
    for name, url in sorted(definitions.items()):
        catalog[name] = {"url": url, "paths": extract_paths(url)}
    return catalog


def diff_catalog(baseline: dict, current: dict) -> list:
    """Return a list of human-readable drift lines (empty = no drift)."""
    lines = []
    for name in sorted(set(current) - set(baseline)):
        lines.append(f"NEW API DEFINITION: {name} ({current[name]['url']})")
        for path in current[name]["paths"]:
            lines.append(f"  new endpoint: {path}")
    for name in sorted(set(baseline) - set(current)):
        lines.append(f"REMOVED API DEFINITION: {name}")
    for name in sorted(set(baseline) & set(current)):
        old = set(baseline[name]["paths"])
        new = set(current[name]["paths"])
        for path in sorted(new - old):
            lines.append(f"NEW ENDPOINT in {name}: {path}")
        for path in sorted(old - new):
            lines.append(f"REMOVED ENDPOINT in {name}: {path}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline with the current live catalog.",
    )
    args = parser.parse_args()

    try:
        current = snapshot_catalog()
    except Exception as exc:  # noqa: BLE001 - report any fetch/parse failure
        print(f"ERROR: could not read the USPTO API catalog: {exc}")
        return 1

    if args.update_baseline:
        BASELINE_PATH.write_text(json.dumps(current, indent=2) + "\n")
        total = sum(len(v["paths"]) for v in current.values())
        print(
            f"Baseline updated: {len(current)} API definitions, "
            f"{total} endpoints -> {BASELINE_PATH}"
        )
        return 0

    if not BASELINE_PATH.exists():
        print(
            f"ERROR: no baseline at {BASELINE_PATH}. "
            "Run with --update-baseline first."
        )
        return 1

    baseline = json.loads(BASELINE_PATH.read_text())
    drift = diff_catalog(baseline, current)

    if not drift:
        total = sum(len(v["paths"]) for v in current.values())
        print(
            f"OK: USPTO catalog unchanged "
            f"({len(current)} API definitions, {total} endpoints)."
        )
        return 0

    print("USPTO API CATALOG DRIFT DETECTED\n")
    for line in drift:
        print(line)
    print(
        "\nNext steps: wrap new endpoints as tools (CLAUDE.md 'Adding a New "
        "Tool'), delete tools for removed endpoints (Server Integrity Rule), "
        "live-verify, add smoke tests, bump the version, then run "
        "'python scripts/check_uspto_api_coverage.py --update-baseline'."
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
