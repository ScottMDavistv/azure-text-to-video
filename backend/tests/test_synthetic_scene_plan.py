from copy import deepcopy
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from azure.core.exceptions import ResourceNotFoundError
from fastapi import HTTPException

from fal_renderer import FalVideoRenderer
import video_routes
from synthetic_scene_plan import (
    ScenePlanValidationError,
    build_scene_plan_from_job,
    validate_scene_plan,
)
from worker import process_synthetic_job


def stored_job(job_id=None):
    return {
        "RowKey": job_id or str(uuid4()),
        "status": "queued",
        "prompt": "Synthetic test prompt",
    }


def test_scene_plan_is_deterministic_and_matches_fixture_contract():
    job = stored_job()

    first = build_scene_plan_from_job(job)
    second = build_scene_plan_from_job(job)

    assert first == second
    assert first["schema_version"] == 1
    assert first["job_id"] == job["RowKey"]
    assert [scene["order"] for scene in first["scenes"]] == [1, 2, 3, 4, 5]
    assert sum(scene["duration_seconds"] for scene in first["scenes"]) == 300
    assert first["video"]["width"] == 320
    assert first["audio"]["sample_rate_hz"] == 16000
    assert first["artifact"] == {
        "name": f"renders/{job['RowKey']}.mp4",
        "content_type": "video/mp4",
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda plan: plan.update(schema_version=2), "Unsupported scene-plan version"),
        (
            lambda plan: plan.update(total_duration_seconds=299),
            "exactly 300 seconds",
        ),
        (
            lambda plan: plan["scenes"][0].update(duration_seconds=59),
            "durations must total",
        ),
        (
            lambda plan: plan["scenes"][0].update(order=2),
            "orders must be sequential",
        ),
        (
            lambda plan: plan["scenes"][0]["visual"].update(color="white"),
            "Unsupported synthetic scene color",
        ),
        (
            lambda plan: plan["artifact"].update(name="other.mp4"),
            "artifact name",
        ),
    ],
)
def test_invalid_scene_plans_are_rejected(mutate, message):
    plan = build_scene_plan_from_job(stored_job())
    invalid_plan = deepcopy(plan)
    mutate(invalid_plan)

    with pytest.raises(ScenePlanValidationError, match=message):
        validate_scene_plan(invalid_plan)


def test_stored_job_validation_rejects_nonqueued_and_invalid_prompt():
    job = stored_job()
    job["status"] = "processing"
    with pytest.raises(ScenePlanValidationError, match="must be queued"):
        build_scene_plan_from_job(job)

    job["status"] = "queued"
    job["prompt"] = ""
    with pytest.raises(ScenePlanValidationError, match="1..2000"):
        build_scene_plan_from_job(job)


def test_synthetic_worker_passes_plan_to_injected_renderer_and_writer():
    job = stored_job()
    renderer = Mock(return_value=b"synthetic-mp4")
    writer = Mock(return_value=f"renders/{job['RowKey']}.mp4")

    with (
        patch("worker.claim_job", return_value=job),
        patch("worker.transition_job") as transition,
        patch("render_pipeline.create_render_pipeline") as paid_pipeline,
    ):
        result = process_synthetic_job(
            job["RowKey"],
            renderer,
            artifact_writer=writer,
        )

    assert result["status"] == "completed"
    plan = renderer.call_args.args[0]
    assert plan["job_id"] == job["RowKey"]
    writer.assert_called_once_with(job["RowKey"], b"synthetic-mp4")
    assert transition.call_args.args[1] == "completed"
    paid_pipeline.assert_not_called()


def test_duplicate_delivery_is_skipped_after_first_atomic_claim():
    job = stored_job()
    renderer = Mock(return_value=b"synthetic-mp4")
    writer = Mock(return_value=f"renders/{job['RowKey']}.mp4")

    with (
        patch("worker.claim_job", side_effect=[job, None]) as claim,
        patch("worker.transition_job"),
    ):
        first = process_synthetic_job(
            job["RowKey"], renderer, artifact_writer=writer
        )
        duplicate = process_synthetic_job(
            job["RowKey"], renderer, artifact_writer=writer
        )

    assert first["status"] == "completed"
    assert duplicate["status"] == "skipped"
    assert claim.call_count == 2
    renderer.assert_called_once()
    writer.assert_called_once()


def test_failed_synthetic_render_marks_job_failed_without_writing():
    job = stored_job()
    renderer = Mock(side_effect=RuntimeError("synthetic render failed"))
    writer = Mock()

    with (
        patch("worker.claim_job", return_value=job),
        patch("worker.transition_job") as transition,
        pytest.raises(RuntimeError, match="synthetic render failed"),
    ):
        process_synthetic_job(
            job["RowKey"], renderer, artifact_writer=writer
        )

    assert transition.call_args.args[1] == "failed"
    writer.assert_not_called()


def test_nonexistent_job_does_not_render_or_write():
    renderer = Mock()
    writer = Mock()

    with (
        patch(
            "worker.claim_job",
            side_effect=ResourceNotFoundError("missing job"),
        ),
        pytest.raises(ResourceNotFoundError),
    ):
        process_synthetic_job(
            str(uuid4()), renderer, artifact_writer=writer
        )

    renderer.assert_not_called()
    writer.assert_not_called()


def test_synthetic_worker_rejects_paid_flags_and_fal_renderer(monkeypatch):
    renderer = Mock()
    writer = Mock()
    claim = Mock()
    fal_renderer = FalVideoRenderer(object())

    monkeypatch.setenv("ENABLE_PAID_FAL_WORKER", "true")
    with patch("worker.claim_job", claim):
        with pytest.raises(ValueError, match="ENABLE_PAID_FAL_WORKER"):
            process_synthetic_job(
                str(uuid4()), renderer, artifact_writer=writer
            )
    claim.assert_not_called()

    monkeypatch.setenv("ENABLE_PAID_FAL_WORKER", "false")
    with pytest.raises(ValueError, match="paid provider"):
        process_synthetic_job(
            str(uuid4()), fal_renderer, artifact_writer=writer
        )
    renderer.assert_not_called()
    writer.assert_not_called()


def test_download_remains_disabled_before_storage_access(monkeypatch):
    monkeypatch.delenv("ENABLE_VIDEO_DOWNLOADS", raising=False)
    monkeypatch.delenv("VIDEO_DOWNLOAD_TOKEN", raising=False)

    with (
        patch("video_routes.storage.get_jobs_table_client") as table,
        pytest.raises(HTTPException) as error,
    ):
        video_routes.get_video_metadata(
            str(uuid4()),
            authorization="******",
        )

    assert error.value.status_code == 503
    table.assert_not_called()
