"""Security regression coverage for all four Bulk 03 P0 blockers."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4
import pytest

from azure.core.exceptions import HttpResponseError
from fal_renderer import FalVideoRenderer
from worker_service import run_once
from financial_outbox import (
    OutboxError, recover_expired_delivery, recover_stalled_notifications,
)
from reconciliation_runner import (
    ReconciliationDisabled, recover_notification_leases,
)
from paid_job_finalization import (
    FinalizationBlocked, reconcile_billing_and_finalize,
)


def uid():
    return str(uuid4())


class Record(dict):
    def __init__(self, **fields):
        super().__init__(**fields)
        self.metadata = {"etag": 'W/"1"'}


def expired_delivery():
    return Record(
        PartitionKey="render-notification-outbox", RowKey="evt-1",
        status="delivering", attempts=1,
        leaseExpiresAt=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
    )


def test_p0_legacy_fal_rejected_before_queue_poll():
    queue, provider = MagicMock(), MagicMock()
    with pytest.raises(ValueError, match="Legacy FAL"):
        run_once(FalVideoRenderer(provider), queue_client=queue)
    queue.receive_messages.assert_not_called()
    provider.submit.assert_not_called()


def test_p0_stalled_delivery_requeued_only_after_lease_expiry():
    table = MagicMock()
    table.get_entity.return_value = expired_delivery()
    result = recover_expired_delivery(
        table, "evt-1", confirm_idempotent_sink=True
    )
    assert result == "pending"
    update = table.update_entity.call_args.kwargs
    assert update["etag"] == 'W/"1"'
    assert update["entity"]["status"] == "pending"


def test_p0_crash_after_send_requires_idempotent_sink():
    table = MagicMock()
    table.get_entity.return_value = expired_delivery()
    with pytest.raises(OutboxError, match="Idempotent"):
        recover_expired_delivery(
            table, "evt-1", confirm_idempotent_sink=False
        )
    table.update_entity.assert_not_called()


def test_p0_unexpired_lease_never_replayed():
    table = MagicMock()
    row = expired_delivery()
    row["leaseExpiresAt"] = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
    table.get_entity.return_value = row
    with pytest.raises(OutboxError, match="not expired"):
        recover_expired_delivery(
            table, "evt-1", confirm_idempotent_sink=True,
        )
    table.update_entity.assert_not_called()


def test_p0_concurrent_recovery_etag_conflict_blocks():
    table = MagicMock()
    table.get_entity.return_value = expired_delivery()
    table.update_entity.side_effect = HttpResponseError("ETag conflict")
    with pytest.raises(OutboxError, match="Concurrent"):
        recover_expired_delivery(
            table, "evt-1", confirm_idempotent_sink=True,
        )


def test_p0_missing_lease_requires_manual_review():
    table = MagicMock()
    table.get_entity.return_value = Record(status="delivering")
    with pytest.raises(OutboxError, match="manual reconciliation"):
        recover_expired_delivery(
            table, "evt-1", confirm_idempotent_sink=True,
        )
    table.update_entity.assert_not_called()


def test_p0_recovery_scan_is_bounded():
    table = MagicMock()
    table.query_entities.return_value = [expired_delivery()]
    with patch("financial_outbox.recover_expired_delivery", return_value="pending") as repair:
        result = recover_stalled_notifications(
            table, confirm_idempotent_sink=True, limit=1,
        )
    assert result == {"recovered": 1, "needs_review": 0}
    repair.assert_called_once()


def test_p0_notification_recovery_requires_deduplicating_consumer():
    with pytest.raises(OutboxError):
        recover_notification_leases(
            enabled=True, outbox_table=MagicMock(),
            downstream_deduplicates_by_event_id=False,
        )


def test_p0_notification_recovery_disabled_by_default():
    table = MagicMock()
    with pytest.raises(ReconciliationDisabled):
        recover_notification_leases(
            enabled=False, outbox_table=table,
            downstream_deduplicates_by_event_id=True,
        )
    table.query_entities.assert_not_called()


def test_p0_settled_charge_can_finalize_after_crash_without_new_settlement():
    jid = uid()
    jobs, finance, submission = MagicMock(), MagicMock(), MagicMock()
    with (
        patch("paid_job_finalization.load", return_value={
            "state": "settled", "actualCents": 45
        }),
        patch("paid_job_finalization.finalize_paid_job",
              return_value={"status": "completed"}) as finalize,
        patch("fal_billing_reconciliation.reconcile_verified_fal_charge") as settle,
    ):
        result = reconcile_billing_and_finalize(
            job_id=jid, jobs_table=jobs, finance_table=finance,
            submission_table=submission, actual_cents=45,
            provider_receipt={}, verify_provider_receipt=lambda *a: False,
        )
    assert result["status"] == "completed"
    settle.assert_not_called()
    finalize.assert_called_once()


def test_p0_conflicting_settled_charge_blocks_finalization():
    with patch("paid_job_finalization.load", return_value={
        "state": "settled", "actualCents": 99,
    }):
        with pytest.raises(FinalizationBlocked, match="Conflicting"):
            reconcile_billing_and_finalize(
                job_id=uid(), jobs_table=MagicMock(), finance_table=MagicMock(),
                submission_table=MagicMock(), actual_cents=42,
                provider_receipt={}, verify_provider_receipt=lambda *a: True,
            )


def test_p0_verified_receipt_settles_before_job_completion():
    events = []
    with (
        patch("paid_job_finalization.load", return_value={"state": "reserved"}),
        patch("fal_billing_reconciliation.reconcile_verified_fal_charge",
              side_effect=lambda *args, **kw: events.append("settle")),
        patch("paid_job_finalization.finalize_paid_job",
              side_effect=lambda **kw: events.append("finish") or {"status": "completed"}),
    ):
        reconcile_billing_and_finalize(
            job_id=uid(), jobs_table=MagicMock(), finance_table=MagicMock(),
            submission_table=MagicMock(), actual_cents=42,
            provider_receipt={}, verify_provider_receipt=lambda *a: True,
        )
    assert events == ["settle", "finish"]
