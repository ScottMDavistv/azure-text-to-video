"""Regression cases from Claude's November 16 receipt identity findings."""
import pytest

from finance_controls import FinanceDenied
from finance_recovery import provider_request_key, receipt_key


def test_receipt_identity_casefolds_to_same_unique_row():
    assert receipt_key("fal", "R-1") == receipt_key("fal", "r-1")


@pytest.mark.parametrize("bad", ["R-1 ", " R-1", "R-1\u200b", "R-1\x00", "  "])
def test_receipt_rejects_ambiguous_ids(bad):
    with pytest.raises(FinanceDenied):
        receipt_key("fal", bad)


def test_provider_request_row_scoped_only_to_provider_and_request():
    assert provider_request_key("fal", "request-1") == provider_request_key("fal", "request-1")
    assert provider_request_key("fal", "request-1") != provider_request_key("fal", "request-2")


@pytest.mark.parametrize("bad", [" request", "request ", "request\u200b", ""])
def test_provider_request_rejects_ambiguous_identity(bad):
    with pytest.raises(FinanceDenied):
        provider_request_key("fal", bad)


@pytest.mark.parametrize("variant", ["REQUEST-1", "Request-1", "ＲＥＱＵＥＳＴ-１"])
def test_provider_request_case_and_nfkc_variants_share_guard_row(variant):
    assert provider_request_key("fal", "request-1") == provider_request_key("fal", variant)
