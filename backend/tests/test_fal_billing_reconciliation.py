from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from fal_billing_reconciliation import (
    BillingReviewRequired, reconcile_verified_fal_charge,
)


def uid():
    return str(uuid4())


def test_unsubmitted_job_never_settles():
    with patch("fal_billing_reconciliation.read_intent",
               return_value={"state": "submitting", "providerRequestId": ""}):
        with pytest.raises(BillingReviewRequired):
            reconcile_verified_fal_charge(
                MagicMock(), MagicMock(), uid(),
                actual_cents=10, provider_receipt={},
                verify_provider_receipt=lambda *a: True,
            )


def test_provider_overage_needs_manual_review():
    with (
        patch("fal_billing_reconciliation.read_intent",
              return_value={"state": "submitted", "providerRequestId": "req_1"}),
        patch("fal_billing_reconciliation.load",
              return_value={"state": "reserved", "reservedCents": 50}),
        patch("fal_billing_reconciliation.settle_verified_charge") as settle,
    ):
        with pytest.raises(BillingReviewRequired):
            reconcile_verified_fal_charge(
                MagicMock(), MagicMock(), uid(), actual_cents=100,
                provider_receipt={}, verify_provider_receipt=lambda *a: True,
            )
    settle.assert_not_called()


def test_unverified_receipt_never_settles():
    with (
        patch("fal_billing_reconciliation.read_intent",
              return_value={"state": "submitted", "providerRequestId": "req_1"}),
        patch("fal_billing_reconciliation.load",
              return_value={"state": "reserved", "reservedCents": 100}),
        patch("fal_billing_reconciliation.settle_verified_charge") as settle,
    ):
        with pytest.raises(BillingReviewRequired):
            reconcile_verified_fal_charge(
                MagicMock(), MagicMock(), uid(), actual_cents=50,
                provider_receipt={}, verify_provider_receipt=lambda *a: False,
            )
    settle.assert_not_called()


def test_valid_receipt_bound_to_provider_request_and_job():
    seen = []
    job_id = uid()
    with (
        patch("fal_billing_reconciliation.read_intent",
              return_value={"state": "submitted", "providerRequestId": "req_123"}),
        patch("fal_billing_reconciliation.load",
              return_value={"state": "reserved", "reservedCents": 100}),
        patch("fal_billing_reconciliation.settle_verified_charge",
              return_value={"state": "settled"}) as settle,
    ):
        result = reconcile_verified_fal_charge(
            MagicMock(), MagicMock(), job_id, actual_cents=75,
            provider_receipt={"receipt": "trusted"},
            verify_provider_receipt=lambda *a: seen.append(a) or True,
        )
    assert result["state"] == "settled"
    # The adapter must receive the actual verifier; only finance_controls.settle
    # may invoke it after confirming an open reservation.
    assert seen == []
    assert settle.call_args.kwargs["job_id"] == job_id
    assert settle.call_args.kwargs["provider_request_id"] == "req_123"
    assert settle.call_args.kwargs["provider_receipt"] == {"receipt": "trusted"}
    assert callable(settle.call_args.kwargs["verify_provider_receipt"])


def test_missing_provider_receipt_fails_closed():
    with (
        patch("fal_billing_reconciliation.read_intent",
              return_value={"state": "submitted", "providerRequestId": "req_1"}),
        patch("fal_billing_reconciliation.load",
              return_value={"state": "reserved", "reservedCents": 100}),
        patch("fal_billing_reconciliation.settle_verified_charge") as settle,
    ):
        with pytest.raises(BillingReviewRequired):
            reconcile_verified_fal_charge(
                MagicMock(), MagicMock(), uid(), actual_cents=45,
                provider_receipt=None, verify_provider_receipt=lambda *a: True)
    settle.assert_not_called()
