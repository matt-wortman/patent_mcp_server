"""Unit tests for inventor name normalization (Last, First → First Last)."""
import pytest

from patent_mcp_server.patents import _normalize_inventor_name


@pytest.mark.unit
def test_comma_form_gets_reordered():
    assert _normalize_inventor_name("Jodele, Sonata") == "Sonata Jodele"


@pytest.mark.unit
def test_already_first_last_is_unchanged():
    assert _normalize_inventor_name("Sonata Jodele") == "Sonata Jodele"


@pytest.mark.unit
def test_single_word_unchanged():
    assert _normalize_inventor_name("Smith") == "Smith"


@pytest.mark.unit
def test_whitespace_around_comma():
    assert _normalize_inventor_name("  Jodele ,  Sonata  ") == "Sonata Jodele"


@pytest.mark.unit
def test_compound_surname_not_auto_rewritten():
    # "Van Der Berg" (no comma) is ambiguous — leave alone.
    assert _normalize_inventor_name("Van Der Berg") == "Van Der Berg"


@pytest.mark.unit
def test_comma_with_multi_word_first_name():
    # "Smith, John Paul" → "John Paul Smith"
    assert _normalize_inventor_name("Smith, John Paul") == "John Paul Smith"


@pytest.mark.unit
def test_empty_or_none_passthrough():
    assert _normalize_inventor_name("") == ""
    assert _normalize_inventor_name(None) is None


@pytest.mark.unit
def test_trailing_comma_no_first_name():
    # Malformed — don't raise; return something usable.
    out = _normalize_inventor_name("Jodele,")
    assert isinstance(out, str)
