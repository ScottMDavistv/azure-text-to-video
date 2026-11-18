"""L-2 unit coverage without Azurite: binding runs before any finance read
or write and the submission ledger is threaded through every caller."""
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from budgeted_fal_execution import settle_verified_charge
from fal_billing_reconciliation import reconcile_verified_fal_charge
from finance_controls import FinanceDenied
from finance_recovery import require_durable_submission, settle_unique_receipt
from provider_submission_ledger import PARTITION as SUBMISSION_PARTITION


def intent(job, request="req-A", state="submitted", **overrides):
    record = {"PartitionKey": SUBMISSION_PARTITION, "RowKey": job,
              "state": state, "providerRequestId": request}
    record.update(overrides)
    return record


def test_matching_durable_submission_is_accepted():
    job = str(uuid4())
    with patch("finance_recovery.read_intent", return_value=intent(job)):
        assert require_durable_submission(object(), job, "req-A") == "req-A"


@pytest.mark.parametrize("record", [
    {"state": "submitting"},
    {"state": "prepared", "providerRequestId": ""},
    {"providerRequestId": ""},
    {"providerRequestId": None},
    {"providerRequestId": "req-B"},
    {"providerRequestId": "REQ-A"},
    {"RowKey": str(uuid4())},
    {"PartitionKey": "render-finance-v1"},
])
def test_unbound_durable_submission_is_denied(record):
    job = str(uuid4())
    with patch("finance_recovery.read_intent", return_value=intent(job, **record)):
        with pytest.raises(FinanceDenied):
            require_durable_submission(object(), job, "req-A")


@pytest.mark.parametrize("value", [None, "not-a-mapping", 42])
def test_malformed_ledger_result_is_denied(value):
    with patch("finance_recovery.read_intent", return_value=value):
        with pytest.raises(FinanceDenied):
            require_durable_submission(object(), str(uuid4()), "req-A")


def test_ledger_error_is_denied():
    with patch("finance_recovery.read_intent", side_effect=RuntimeError("x")):
        with pytest.raises(FinanceDenied, match="unavailable"):
            require_durable_submission(object(), str(uuid4()), "req-A")


def test_binding_failure_happens_before_verifier_and_finance_access():
    job = str(uuid4())
    finance = MagicMock()
    verifier = MagicMock(return_value=True)
    with patch("finance_recovery.read_intent",
               return_value=intent(job, request="req-B")):
        with pytest.raises(FinanceDenied, match="durable submission"):
            settle_unique_receipt(
                finance, submission_table=MagicMock(), job_id=job,
                actual_cents=10, provider_request_id="req-A",
                receipt_id="receipt-1", provider_receipt={"x": 1},
                verify_provider_receipt=verifier)
    verifier.assert_not_called()
    finance.get_entity.assert_not_called()
    finance.submit_transaction.assert_not_called()


def test_settlement_without_submission_ledger_fails_closed():
    finance = MagicMock()
    with pytest.raises(FinanceDenied, match="ledger required"):
        settle_unique_receipt(
            finance, job_id=str(uuid4()), actual_cents=10,
            provider_request_id="req-A", receipt_id="receipt-1",
            provider_receipt={"x": 1}, verify_provider_receipt=lambda *a: True)
    finance.get_entity.assert_not_called()
    finance.submit_transaction.assert_not_called()


def test_bridge_forwards_submission_ledger():
    subs = object()
    with patch("finance_recovery.settle_unique_receipt",
               return_value={"state": "settled"}) as ledger:
        settle_verified_charge(
            finance_table=MagicMock(), submission_table=subs, job_id=str(uuid4()),
            charge_cents=5, provider_request_id="req-A",
            provider_receipt={"receipt_id": "r"},
            verify_provider_receipt=lambda *a: True)
    assert ledger.call_args.kwargs["submission_table"] is subs


def test_reconciliation_forwards_its_submission_ledger():
    job, subs = str(uuid4()), object()
    with (
        patch("fal_billing_reconciliation.read_intent",
              return_value=intent(job)),
        patch("fal_billing_reconciliation.load",
              return_value={"state": "reserved", "reservedCents": 50}),
        patch("fal_billing_reconciliation.settle_verified_charge",
              return_value={"state": "settled"}) as bridge,
    ):
        reconcile_verified_fal_charge(
            MagicMock(), subs, job, actual_cents=20,
            provider_receipt={"receipt_id": "r"},
            verify_provider_receipt=lambda *a: True)
    assert bridge.call_args.kwargs["submission_table"] is subs
    assert bridge.call_args.kwargs["provider_request_id"] == "req-A"
