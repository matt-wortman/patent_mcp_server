"""Unit tests for PPUBS query rewriting and search-total resolution.

Pure internal logic, no network. The behaviors under test were
live-verified against ppubs.uspto.gov on 2026-08-27:

- The backend has no "AN" (assignee) index — its assignee-name index is
  "AS" (Raytheon.an. -> 0 results, Raytheon.as. -> 11,725 in USPAT), so
  the client rewrites ".an." to ".as." before sending a query.
- searchWithBeFamily's "numFound" is the number of family groups on the
  returned page, not the query total (Marron.in. with pageCount=20 gave
  numFound=20 while /api/searches/counts reported numResults=207 and the
  response itself reported numberOfFamilies=176). The client replaces
  "numFound" with the counts endpoint's document total.
"""

import pytest

from patent_mcp_server.uspto.ppubs_uspto_gov import (
    rewrite_field_aliases,
    resolve_search_total,
)
from patent_mcp_server.util.response import ResponseEnvelope

pytestmark = pytest.mark.unit


# --- rewrite_field_aliases ----------------------------------------------

def test_an_is_rewritten_to_as():
    assert rewrite_field_aliases("Raytheon.an.") == "Raytheon.as."


def test_an_is_rewritten_case_insensitively():
    assert rewrite_field_aliases("Raytheon.AN.") == "Raytheon.as."


def test_an_rewrite_inside_boolean_query():
    assert (
        rewrite_field_aliases('"intra-pixel quadrature".ttl. AND Raytheon.an.')
        == '"intra-pixel quadrature".ttl. AND Raytheon.as.'
    )


def test_quoted_assignee_phrase_keeps_field_rewrite():
    assert (
        rewrite_field_aliases('"Raytheon Company".an.')
        == '"Raytheon Company".as.'
    )


def test_text_inside_quotes_is_not_rewritten():
    # ".an." inside a quoted phrase is part of the phrase, not a field code.
    assert (
        rewrite_field_aliases('"contains .an. literally"')
        == '"contains .an. literally"'
    )


def test_working_field_codes_are_untouched():
    for q in ["Smith.in.", '"coherent ladar".ttl.', "Raytheon.aanm.",
              "G06N3/08.cpc.", '("6103599").pn.']:
        assert rewrite_field_aliases(q) == q


def test_docstring_example_query_is_fixed():
    # The tool docstring's advertised example must reach the backend in
    # a form that has real indexes on both sides of the AND.
    assert rewrite_field_aliases("Smith.in. AND IBM.an.") == "Smith.in. AND IBM.as."


# --- resolve_search_total -----------------------------------------------

# Shape of a live searchWithBeFamily response (Marron.in., pageCount=20).
_LIVE_SHAPE = {"numFound": 20, "totalResults": 21, "numberOfFamilies": 176}


def test_counts_total_wins():
    assert resolve_search_total(_LIVE_SHAPE, 207) == 207


def test_falls_back_to_query_wide_family_count():
    assert resolve_search_total(_LIVE_SHAPE, None) == 176


def test_falls_back_to_numfound_when_nothing_else_present():
    assert resolve_search_total({"numFound": 5}, None) == 5


def test_zero_counts_total_is_respected():
    # 0 is a real answer (query matched nothing), not a missing value.
    assert resolve_search_total(_LIVE_SHAPE, 0) == 0


def test_empty_response_yields_zero():
    assert resolve_search_total({}, None) == 0


# --- envelope integration (pure dict in, pure dict out) -----------------

def test_envelope_total_and_count_after_numfound_override():
    # run_query overwrites "numFound" with the counts total before the
    # envelope is built; given that corrected raw dict, the envelope must
    # report count = documents on this page and total = query-wide total.
    raw = {
        "numFound": 207,          # corrected by run_query
        "familiesOnPage": 20,     # preserved raw per-page value
        "totalResults": 21,
        "numberOfFamilies": 176,
        "patents": [{"guid": f"US-{i}-B2"} for i in range(21)],
    }
    envelope = ResponseEnvelope.from_ppubs(raw, offset=0, limit=20)

    assert envelope["success"] is True
    assert envelope["count"] == 21
    assert envelope["total"] == 207
    assert envelope["has_more"] is True
