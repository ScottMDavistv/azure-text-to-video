"""Bounded offline synthetic worker integration, no Azure or provider calls."""
from unittest.mock import Mock, patch
from uuid import uuid4
import pytest
import synthetic_worker_cli as synthetic
from synthetic_job_renderer import assert_offline, SyntheticRenderError

PID = str(uuid4())
UID = str(uuid4())
JID = str(uuid4())

def owned_job():
    return {"PartitionKey": "video", "RowKey": JID,
            "prompt": "A quiet lake", "status": "queued",
            "projectId": PID, "createdBy": UID}

@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("ENABLE_AUTHENTICATED_VIDEO_JOBS", "true")
    monkeypatch.setenv("ENABLE_PROJECT_ROUTES", "true")
    monkeypatch.setenv("ENABLE_SYNTHETIC_VIDEO_DISPATCH", "true")
    monkeypatch.setenv("VIDEO_RENDER_PROVIDER", "disabled")
    monkeypatch.setenv("ENABLE_PAID_FAL_WORKER", "false")
    monkeypatch.setenv("ENABLE_LIVE_VIDEO_RENDERING", "false")
    monkeypatch.setenv("ENABLE_WORKER_CONSUMPTION", "false")
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_DEV", "false")

def test_worker_requires_explicit_three_flags(monkeypatch):
    for name in ("ENABLE_AUTHENTICATED_VIDEO_JOBS", "ENABLE_PROJECT_ROUTES",
                 "ENABLE_SYNTHETIC_VIDEO_DISPATCH"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SyntheticRenderError):
        synthetic.require_synthetic_readiness()

def test_paid_flags_block_before_queue_or_blob_calls(config, monkeypatch):
    monkeypatch.setenv("ENABLE_PAID_FAL_WORKER", "true")
    with patch.object(synthetic, "consume_one") as queue:
        with pytest.raises(SyntheticRenderError):
            synthetic.run_once()
        queue.assert_not_called()

def test_cross_job_type_or_missing_owner_rejected(config):
    table = Mock()
    table.get_entity.return_value = {**owned_job(), "createdBy": None}
    with patch.object(synthetic, "get_jobs_table_client", return_value=table), \
         patch.object(synthetic, "process_synthetic_job") as worker, \
         pytest.raises(SyntheticRenderError):
        synthetic.process_owned_synthetic_job(JID)
    worker.assert_not_called()

def test_owned_job_calls_only_validated_renderer_and_private_writer(config):
    table = Mock()
    table.get_entity.return_value = owned_job()
    renderer = Mock(return_value=b"synthetic-mp4")
    writer = Mock(return_value="renders/" + JID + ".mp4")
    with patch.object(synthetic, "get_jobs_table_client", return_value=table), \
         patch.object(synthetic, "process_synthetic_job",
                      return_value={"status": "completed"}) as worker:
        result = synthetic.process_owned_synthetic_job(
            JID, renderer=renderer, artifact_writer=writer)
    assert result["status"] == "completed"
    worker.assert_called_once_with(JID, renderer, artifact_writer=writer)

def test_worker_never_autoruns_with_no_once_flag(config):
    with patch.object(synthetic, "run_once") as worker:
        assert synthetic.main([]) == 2
        worker.assert_not_called()

def test_provider_must_remain_disabled(config, monkeypatch):
    assert_offline()
    monkeypatch.setenv("VIDEO_RENDER_PROVIDER", "fal")
    with pytest.raises(SyntheticRenderError):
        assert_offline()

def test_worker_container_preserves_safe_default():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "Dockerfile.worker").read_text()
    assert 'CMD ["python", "worker_runtime.py", "--check"]' in source
    assert "ffmpeg" in source
    assert "synthetic_worker_cli.py --once" not in source
