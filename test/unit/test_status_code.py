"""Unit tests for the DSAPI-backed get_status_code tool.

These are unit tests: we monkeypatch the dsapi_client.search coroutine so no
network calls are made. The point is to verify the mapping logic (DSAPI
result → {code, description, stage}) and the error paths.
"""
import pytest

from patent_mcp_server import patents as patents_mod
from patent_mcp_server.patents import (
    _derive_status_stage,
    _lookup_status_code_via_dsapi,
    get_status_code,
)


# ----------------------------------------------------------------------
# Stage derivation (pure)
# ----------------------------------------------------------------------

@pytest.mark.unit
def test_derive_status_stage_examination():
    assert _derive_status_stage("30") == "examination"


@pytest.mark.unit
def test_derive_status_stage_granted():
    assert _derive_status_stage("150") == "unknown"  # 150 > 89
    assert _derive_status_stage("50") == "granted"


@pytest.mark.unit
def test_derive_status_stage_bad_input():
    assert _derive_status_stage("abc") == "unknown"
    assert _derive_status_stage("") == "unknown"


# ----------------------------------------------------------------------
# Helpers for mocking DSAPI
# ----------------------------------------------------------------------

def _fake_dsapi_response(code: str, description: str):
    return {
        "response": {
            "start": 0,
            "numFound": 1,
            "docs": [
                {"appl_status_code": code, "status_description": description}
            ],
        }
    }


def _empty_dsapi_response():
    return {"response": {"start": 0, "numFound": 0, "docs": []}}


# ----------------------------------------------------------------------
# get_status_code — the regression that motivated this fix
# ----------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_status_code_150_returns_patented_case(monkeypatch):
    """get_status_code("150") should return "Patented Case" (stress-test bug)."""

    async def fake_search(*, dataset, version, criteria, rows=5, **kwargs):
        assert "150" in criteria
        return _fake_dsapi_response("150", "Patented Case")

    monkeypatch.setattr(patents_mod.dsapi_client, "search", fake_search)

    result = await get_status_code("150")
    assert result["code"] == "150"
    assert result["description"] == "Patented Case"
    # 150 is outside the derived-stage ranges; that's fine — we preserve
    # the description, which is what callers actually use.
    assert "stage" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_status_code_30_returns_expected_shape(monkeypatch):
    async def fake_search(**kwargs):
        return _fake_dsapi_response("30", "Docketed New Case - Ready for Examination")

    monkeypatch.setattr(patents_mod.dsapi_client, "search", fake_search)

    result = await get_status_code("30")
    assert result["code"] == "30"
    assert "Docketed" in result["description"]
    assert result["stage"] == "examination"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_status_code_unknown_returns_clean_error(monkeypatch):
    async def fake_search(**kwargs):
        return _empty_dsapi_response()

    monkeypatch.setattr(patents_mod.dsapi_client, "search", fake_search)

    result = await get_status_code("9999")
    assert result.get("error") is True
    assert "9999" in result.get("message", "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lookup_propagates_upstream_error(monkeypatch):
    """If DSAPI itself returns an error envelope, we should pass it through."""

    async def fake_search(**kwargs):
        return {"error": True, "message": "upstream down", "status_code": 503}

    monkeypatch.setattr(patents_mod.dsapi_client, "search", fake_search)

    result = await _lookup_status_code_via_dsapi("30")
    assert result.get("error") is True
    assert result.get("status_code") == 503
