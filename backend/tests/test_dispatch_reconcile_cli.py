"""Offline scheduled-dispatch runner and alert regression tests."""
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from dispatch_reconcile_cli import run_cli, run_once


def test_check_does_not_initialize_azure(monkeypatch, capsys):
    monkeypatch.delenv("ENABLE_DISPATCH_RECONCILIATION", raising=False)
    with patch("dispatch_recovery.get_jobs_table_client") as storage:
        assert run_cli(["--check"]) == 0
        storage.assert_not_called()
    assert '"azure_calls": false' in capsys.readouterr().out


def test_live_flag_required_before_storage(monkeypatch):
    monkeypatch.delenv("ENABLE_DISPATCH_RECONCILIATION", raising=False)
    with patch("dispatch_recovery.get_jobs_table_client") as storage:
        with pytest.raises(SystemExit):
            run_cli(["--run-once"])
        storage.assert_not_called()


def test_paid_flag_blocks_run_before_storage(monkeypatch):
    monkeypatch.setenv("ENABLE_DISPATCH_RECONCILIATION", "true")
    monkeypatch.setenv("ENABLE_PAID_FAL_WORKER", "true")
    with patch("dispatch_recovery.get_jobs_table_client") as storage:
        with pytest.raises(RuntimeError, match="Unsafe Stage A"):
            run_cli(["--run-once"])
        storage.assert_not_called()


def test_reconcile_across_pages_and_emit_summary_without_job_ids():
    first = str(uuid4())
    second = str(uuid4())
    fake = MagicMock(side_effect=[
        {"inspected": 1, "results": [{"job_id": first, "outcome": "sent"}],
         "next_cursor": first},
        {"inspected": 1, "results": [{"job_id": second, "outcome": "held"}],
         "next_cursor": None},
    ])
    logger = MagicMock()
    summary = run_once(reconcile=fake, batch_size=2, logger=logger)
    assert summary["inspected"] == 2
    assert summary["pages"] == 2
    assert summary["sent"] == 1
    assert summary["held"] == 1
    assert summary["alert"] is False
    assert fake.call_args_list[1].kwargs["cursor"] == first
    emitted = logger.info.call_args.args[0]
    assert first not in emitted and second not in emitted


def test_uncertainty_emits_operator_alert():
    logger = MagicMock()
    result = run_once(
        reconcile=lambda **kw: {
            "inspected": 1,
            "results": [{"outcome": "uncertain", "job_id": str(uuid4())}],
            "next_cursor": None},
        logger=logger)
    assert result["alert"] is True
    logger.error.assert_called_once()


def test_capped_pagination_emits_operator_alert():
    def page(**kw):
        return {"inspected": 1, "results": [
            {"outcome": "held", "job_id": str(uuid4())}],
            "next_cursor": str(uuid4())}
    result = run_once(reconcile=page, max_pages=1, logger=MagicMock())
    assert result["incomplete"] is True
    assert result["alert"] is True


def test_repeated_cursor_rejected():
    cursor = str(uuid4())
    with pytest.raises(RuntimeError, match="pagination"):
        run_once(reconcile=lambda **kw: {
            "inspected": 1, "results": [{"outcome": "held"}],
            "next_cursor": cursor}, max_pages=3, logger=MagicMock())


@pytest.mark.parametrize("kwargs", [
    {"batch_size": True}, {"batch_size": 0}, {"batch_size": 51},
    {"max_pages": True}, {"max_pages": 0}, {"max_pages": 101},
])
def test_invalid_runner_bounds_before_dispatch(kwargs):
    service = MagicMock()
    with pytest.raises(ValueError):
        run_once(reconcile=service, **kwargs)
    service.assert_not_called()
