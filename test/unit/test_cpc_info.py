"""Unit tests for the CPC scheme lookup behind get_cpc_info.

These are unit tests: the scheme-page HTML parsing runs against a small
synthetic fixture mirroring the real markup of
https://www.uspto.gov/web/patents/classification/cpc/html/cpc-G01S.html,
and the tool-level tests monkeypatch the CpcSchemeClient (or the shared
request_bytes helper) so no network calls are made.
"""
import pytest

from patent_mcp_server import patents as patents_mod
from patent_mcp_server.patents import get_cpc_info
from patent_mcp_server.uspto import cpc_scheme as cpc_scheme_mod
from patent_mcp_server.uspto.cpc_scheme import (
    CpcSchemeClient,
    build_hierarchy,
    normalize_cpc_symbol,
    parse_scheme_html,
    subclass_of,
)


# ----------------------------------------------------------------------
# Fixture HTML — trimmed copy of the real scheme-page markup
# ----------------------------------------------------------------------

def _item(symbol, title_html, indent=None):
    indent_cell = (
        f'<td width="30" title="Indent level is {indent}"><div>'
        f'<span class="indent"><b>. </b></span></div></td>'
        if indent is not None else ""
    )
    return (
        f'<table class="classItem subclassgt8" id="{symbol}"><tr>'
        f'<td></td>{indent_cell}'
        f'<td class="symbol"><div><span class="symbol">'
        f'<span class="alink">{symbol}</span></span></div></td>'
        f'<td><div><div class="class-title">{title_html}'
        f'<span class="date-revised"> [2021-01]</span></div>'
        f'<div class="notes-and-warnings"><div class="note">NOTES<p></p>'
        f'</div><ul><li>This group covers noise we must not parse.</li>'
        f'</ul></div></div></td>'
        f'</tr></table>'
    )


SCHEME_HTML = "".join([
    _item("G01S", '<span class="ipc-text">RADIO DIRECTION-FINDING</span>; '
                  '<span class="ipc-text">ANALOGOUS ARRANGEMENTS</span>'),
    _item("G01S17/00", '<span class="ipc-text">Systems using the reflection '
                       'or reradiation of electromagnetic waves other than '
                       'radio waves, e.g. lidar systems</span>'),
    _item("G01S17/02", '<span class="ipc-text">Systems using the reflection '
                       'of electromagnetic waves</span>', indent=1),
    _item("G01S17/06", '<span class="ipc-text">Systems determining position '
                       'data of a target</span>', indent=2),
    _item("G01S17/08", '<span class="ipc-text">for measuring distance only '
                       '</span>', indent=3),
    _item("G01S17/32", '<span class="ipc-text">using transmission of '
                       'continuous waves</span>', indent=4),
    _item("G01S17/34", '<span class="ipc-text">a sibling entry</span>',
          indent=5),
])


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------

@pytest.mark.unit
def test_normalize_cpc_symbol():
    assert normalize_cpc_symbol("g01s 17/32") == "G01S17/32"
    assert normalize_cpc_symbol("  G01S\t17/32 ") == "G01S17/32"
    assert normalize_cpc_symbol("") == ""


@pytest.mark.unit
def test_subclass_of():
    assert subclass_of("G01S17/32") == "G01S"
    assert subclass_of("G01S") == "G01S"
    assert subclass_of("G01") is None
    assert subclass_of("G") is None
    assert subclass_of("") is None


@pytest.mark.unit
def test_parse_scheme_html_extracts_symbols_titles_indents():
    entries = parse_scheme_html(SCHEME_HTML)
    assert [e["symbol"] for e in entries] == [
        "G01S", "G01S17/00", "G01S17/02", "G01S17/06",
        "G01S17/08", "G01S17/32", "G01S17/34",
    ]
    by_symbol = {e["symbol"]: e for e in entries}
    # Multi-span titles join with their separators; notes and the
    # date-revised marker are stripped.
    assert by_symbol["G01S"]["title"] == (
        "RADIO DIRECTION-FINDING; ANALOGOUS ARRANGEMENTS"
    )
    assert "NOTES" not in by_symbol["G01S17/00"]["title"]
    assert "[2021-01]" not in by_symbol["G01S17/32"]["title"]
    assert by_symbol["G01S17/32"]["title"] == (
        "using transmission of continuous waves"
    )
    assert by_symbol["G01S"]["indent"] == 0
    assert by_symbol["G01S17/00"]["indent"] == 0
    assert by_symbol["G01S17/32"]["indent"] == 4


@pytest.mark.unit
def test_build_hierarchy_walks_parent_chain():
    entries = parse_scheme_html(SCHEME_HTML)
    chain = build_hierarchy(entries, "G01S17/32")
    assert [e["symbol"] for e in chain] == [
        "G01S", "G01S17/00", "G01S17/02", "G01S17/06",
        "G01S17/08", "G01S17/32",
    ]


@pytest.mark.unit
def test_build_hierarchy_subclass_and_main_group():
    entries = parse_scheme_html(SCHEME_HTML)
    assert [e["symbol"] for e in build_hierarchy(entries, "G01S")] == ["G01S"]
    assert [e["symbol"] for e in build_hierarchy(entries, "G01S17/00")] == [
        "G01S", "G01S17/00",
    ]


@pytest.mark.unit
def test_build_hierarchy_unknown_symbol_returns_none():
    entries = parse_scheme_html(SCHEME_HTML)
    assert build_hierarchy(entries, "G01S99/99") is None


# ----------------------------------------------------------------------
# CpcSchemeClient — lookup and caching (request_bytes mocked)
# ----------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_client_lookup_and_cache(monkeypatch):
    calls = []

    async def fake_request_bytes(client, url, *, headers=None, context=""):
        calls.append(url)
        return {
            "content": SCHEME_HTML.encode("utf-8"),
            "content_type": "text/html",
            "size_bytes": len(SCHEME_HTML),
        }

    monkeypatch.setattr(cpc_scheme_mod, "request_bytes", fake_request_bytes)

    async with CpcSchemeClient() as client:
        result = await client.lookup("G01S17/32")
        assert result["definition"] == "using transmission of continuous waves"
        assert result["subclass"] == "G01S"
        assert result["subclass_title"].startswith("RADIO DIRECTION-FINDING")
        assert [e["symbol"] for e in result["hierarchy"]][-1] == "G01S17/32"

        # Second lookup in the same subclass must hit the in-memory cache.
        result2 = await client.lookup("G01S17/08")
        assert result2["definition"] == "for measuring distance only"
        assert len(calls) == 1
        assert "cpc-G01S.html" in calls[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_client_lookup_symbol_not_in_scheme(monkeypatch):
    async def fake_request_bytes(client, url, *, headers=None, context=""):
        return {
            "content": SCHEME_HTML.encode("utf-8"),
            "content_type": "text/html",
            "size_bytes": len(SCHEME_HTML),
        }

    monkeypatch.setattr(cpc_scheme_mod, "request_bytes", fake_request_bytes)
    async with CpcSchemeClient() as client:
        result = await client.lookup("G01S99/99")
        assert result.get("error") is True
        assert result["error_code"] == "CPC_SYMBOL_NOT_FOUND"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_client_lookup_rejects_partial_codes():
    async with CpcSchemeClient() as client:
        result = await client.lookup("G01")
        assert result.get("error") is True
        assert result["error_code"] == "CPC_SYMBOL_INVALID"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_client_fetch_error_not_cached(monkeypatch):
    async def failing_request_bytes(client, url, *, headers=None, context=""):
        return {"error": True, "message": "boom", "status_code": 503}

    monkeypatch.setattr(cpc_scheme_mod, "request_bytes", failing_request_bytes)
    async with CpcSchemeClient() as client:
        result = await client.lookup("G01S17/32")
        assert result.get("error") is True
        assert client._scheme_cache == {}


# ----------------------------------------------------------------------
# get_cpc_info tool — merge and fallback behavior
# ----------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_cpc_info_full_symbol_merges_live_definition(monkeypatch):
    async def fake_lookup(code):
        assert code == "G01S17/32"
        return {
            "symbol": "G01S17/32",
            "subclass": "G01S",
            "subclass_title": "RADIO DIRECTION-FINDING",
            "definition": "using transmission of continuous waves",
            "hierarchy": [
                {"symbol": "G01S", "indent": 0,
                 "title": "RADIO DIRECTION-FINDING"},
                {"symbol": "G01S17/32", "indent": 4,
                 "title": "using transmission of continuous waves"},
            ],
        }

    monkeypatch.setattr(patents_mod.cpc_scheme_client, "lookup", fake_lookup)

    result = await get_cpc_info("G01S17/32")
    # Backward-compatible static keys survive...
    assert result["code"] == "G01S17/32"
    assert result["section"] == "G"
    assert result["section_title"] == "Physics"
    assert result["subsection_title"] == "Measuring; Testing"
    # ...and the real definition is added.
    assert result["definition"] == "using transmission of continuous waves"
    assert result["subclass"] == "G01S"
    assert len(result["hierarchy"]) == 2
    assert "source" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_cpc_info_falls_back_to_static_on_live_failure(monkeypatch):
    async def failing_lookup(code):
        return {"error": True, "message": "USPTO unreachable"}

    monkeypatch.setattr(patents_mod.cpc_scheme_client, "lookup", failing_lookup)

    result = await get_cpc_info("G01S17/32")
    assert result["code"] == "G01S17/32"
    assert result["section_title"] == "Physics"
    assert result["subsection_title"] == "Measuring; Testing"
    assert "definition" not in result
    assert "note" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_cpc_info_section_letter_stays_static(monkeypatch):
    async def must_not_be_called(code):  # pragma: no cover
        raise AssertionError("live lookup must not run for section letters")

    monkeypatch.setattr(
        patents_mod.cpc_scheme_client, "lookup", must_not_be_called
    )
    result = await get_cpc_info("G")
    assert result["title"] == "Physics"
    assert "subsections" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_cpc_info_class_level_stays_static(monkeypatch):
    async def must_not_be_called(code):  # pragma: no cover
        raise AssertionError("live lookup must not run for class-level codes")

    monkeypatch.setattr(
        patents_mod.cpc_scheme_client, "lookup", must_not_be_called
    )
    result = await get_cpc_info("G06")
    assert result["code"] == "G06"
    assert result["subsection_title"] == "Computing; Calculating; Counting"
    assert "definition" not in result
