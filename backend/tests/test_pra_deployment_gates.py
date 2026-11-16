"""Regression checks for Phase 1 deployment and paid-provider gates.

Source-only infrastructure tests do not establish deployed Azure RBAC.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from api_access import api_auth_required, check_api_authorization
from budgeted_worker_service import (
    PaidWorkerDisabled, process_reserved_job, run_budgeted_once,
)

ROOT = Path(__file__).resolve().parents[2]


def test_nonbillable_worker_has_no_fal_secret_and_separate_identity():
    template = (ROOT / "infra/worker-job.bicep").read_text()
    assert "param identityName string = 'id-textvideo-worker-dev'" in template
    assert "id-textvideo-api-dev" not in template
    assert "falKeyVaultSecretUri" not in template
    assert "fal-api-key" not in template
    assert "name: 'FAL_KEY'" not in template
    assert "secrets: []" in template


def test_deployed_jobs_api_requires_keyvault_backed_auth():
    template = (ROOT / "infra/backend-app.bicep").read_text()
    assert "param jobsApiTokenSecretUri string" in template
    assert "keyVaultUrl: jobsApiTokenSecretUri" in template
    assert "secretRef: 'jobs-api-access-token'" in template
    assert "name: 'REQUIRE_JOB_API_AUTH'" in template
    assert "name: 'ALLOW_UNAUTHENTICATED_DEV'" in template
    assert "value: 'false'" in template


def test_legacy_jobs_default_deny_without_dev_opt_out(monkeypatch):
    monkeypatch.delenv("ALLOW_UNAUTHENTICATED_DEV", raising=False)
    monkeypatch.setenv("REQUIRE_JOB_API_AUTH", "false")
    assert api_auth_required() is True
    monkeypatch.setenv("API_ACCESS_TOKEN", "Z" * 48)
    assert not check_api_authorization(None)
    assert check_api_authorization("Bearer " + "Z" * 48)


def test_budgeted_worker_fails_before_claim_with_all_enables(monkeypatch):
    monkeypatch.setenv("ENABLE_PAID_FAL_WORKER", "true")
    jobs = MagicMock()
    client = MagicMock()
    with pytest.raises(PaidWorkerDisabled, match="readiness"):
        process_reserved_job(
            "00000000-0000-0000-0000-000000000001",
            enabled=True, jobs_table=jobs, finance_table=MagicMock(),
            submission_table=MagicMock(), client=client,
            authorize_job=lambda *_: True,
        )
    jobs.get_entity.assert_not_called()
    jobs.update_entity.assert_not_called()
    client.submit.assert_not_called()


def test_budgeted_queue_fails_before_consuming_when_not_ready(monkeypatch):
    monkeypatch.setenv("ENABLE_PAID_FAL_WORKER", "true")
    queue = MagicMock()
    with pytest.raises(PaidWorkerDisabled, match="readiness"):
        run_budgeted_once(
            enabled=True, queue_client=queue, jobs_table=MagicMock(),
            finance_table=MagicMock(), submission_table=MagicMock(),
            client=MagicMock(), authorize_job=lambda *_: True,
        )
    queue.receive_messages.assert_not_called()
