"""Versioned offline scene-plan contract for deterministic synthetic renders."""

from uuid import UUID


SCENE_PLAN_VERSION = 1
TOTAL_DURATION_SECONDS = 300
MAX_SCENES = 30
MAX_SCENE_DURATION_SECONDS = 60
COLORS = ("navy", "teal", "maroon", "purple", "darkgreen")


class ScenePlanValidationError(ValueError):
    """The stored job or scene plan does not satisfy the synthetic contract."""


def build_scene_plan_from_job(job):
    if not isinstance(job, dict):
        raise ScenePlanValidationError("Stored job must be an object")

    job_id = job.get("RowKey")
    try:
        normalized_job_id = str(UUID(str(job_id)))
    except (ValueError, AttributeError, TypeError):
        raise ScenePlanValidationError("Stored job has an invalid job ID") from None

    if normalized_job_id != job_id:
        raise ScenePlanValidationError("Stored job ID must be a canonical UUID")
    if job.get("status") != "queued":
        raise ScenePlanValidationError("Stored job must be queued")

    prompt = job.get("prompt")
    if not isinstance(prompt, str) or not 1 <= len(prompt) <= 2000:
        raise ScenePlanValidationError("Stored job prompt must contain 1..2000 characters")

    plan = {
        "schema_version": SCENE_PLAN_VERSION,
        "job_id": normalized_job_id,
        "total_duration_seconds": TOTAL_DURATION_SECONDS,
        "video": {
            "width": 320,
            "height": 180,
            "frame_rate": 12,
            "codec": "libx264",
            "pixel_format": "yuv420p",
        },
        "audio": {
            "codec": "aac",
            "sample_rate_hz": 16000,
            "channels": 1,
        },
        "scenes": [
            {
                "scene_id": f"scene-{index:03d}",
                "order": index,
                "duration_seconds": 60,
                "visual": {"kind": "solid_color", "color": color},
                "audio": {
                    "kind": "sine_tone",
                    "frequency_hz": 330 + (index - 1) * 20,
                },
            }
            for index, color in enumerate(COLORS, start=1)
        ],
        "artifact": {
            "name": f"renders/{normalized_job_id}.mp4",
            "content_type": "video/mp4",
        },
    }
    return validate_scene_plan(plan)


def validate_scene_plan(plan):
    if not isinstance(plan, dict):
        raise ScenePlanValidationError("Scene plan must be an object")
    schema_version = plan.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != SCENE_PLAN_VERSION
    ):
        raise ScenePlanValidationError("Unsupported scene-plan version")

    job_id = plan.get("job_id")
    try:
        normalized_job_id = str(UUID(str(job_id)))
    except (ValueError, AttributeError, TypeError):
        raise ScenePlanValidationError("Scene plan has an invalid job ID") from None
    if normalized_job_id != job_id:
        raise ScenePlanValidationError("Scene-plan job ID must be a canonical UUID")

    duration = plan.get("total_duration_seconds")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, int)
        or duration != TOTAL_DURATION_SECONDS
    ):
        raise ScenePlanValidationError("Scene plan must total exactly 300 seconds")

    video = plan.get("video")
    if (
        not isinstance(video, dict)
        or any(
            isinstance(video.get(field), bool)
            or not isinstance(video.get(field), int)
            for field in ("width", "height", "frame_rate")
        )
        or video != {
        "width": 320,
        "height": 180,
        "frame_rate": 12,
        "codec": "libx264",
        "pixel_format": "yuv420p",
        }
    ):
        raise ScenePlanValidationError("Unsupported synthetic video requirements")

    audio = plan.get("audio")
    if (
        not isinstance(audio, dict)
        or any(
            isinstance(audio.get(field), bool)
            or not isinstance(audio.get(field), int)
            for field in ("sample_rate_hz", "channels")
        )
        or audio != {
            "codec": "aac",
            "sample_rate_hz": 16000,
            "channels": 1,
        }
    ):
        raise ScenePlanValidationError("Unsupported synthetic audio requirements")

    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= MAX_SCENES:
        raise ScenePlanValidationError("Scene count must be between 1 and 30")

    scene_duration_total = 0
    for expected_order, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            raise ScenePlanValidationError("Each scene must be an object")
        if scene.get("scene_id") != f"scene-{expected_order:03d}":
            raise ScenePlanValidationError("Scene IDs and order must be sequential")
        order = scene.get("order")
        if (
            isinstance(order, bool)
            or not isinstance(order, int)
            or order != expected_order
        ):
            raise ScenePlanValidationError("Scene orders must be sequential")

        scene_duration = scene.get("duration_seconds")
        if (
            isinstance(scene_duration, bool)
            or not isinstance(scene_duration, int)
            or not 1 <= scene_duration <= MAX_SCENE_DURATION_SECONDS
        ):
            raise ScenePlanValidationError("Each scene duration must be between 1 and 60 seconds")
        scene_duration_total += scene_duration

        visual = scene.get("visual")
        if not isinstance(visual, dict) or visual.get("kind") != "solid_color":
            raise ScenePlanValidationError("Each scene requires synthetic solid-color video")
        if visual.get("color") not in COLORS:
            raise ScenePlanValidationError("Unsupported synthetic scene color")

        scene_audio = scene.get("audio")
        frequency = (
            scene_audio.get("frequency_hz")
            if isinstance(scene_audio, dict)
            else None
        )
        if (
            not isinstance(scene_audio, dict)
            or scene_audio.get("kind") != "sine_tone"
            or isinstance(frequency, bool)
            or not isinstance(frequency, int)
            or not 20 <= frequency <= 20000
        ):
            raise ScenePlanValidationError("Each scene requires a 20..20000 Hz synthetic tone")

    if scene_duration_total != TOTAL_DURATION_SECONDS:
        raise ScenePlanValidationError("Scene durations must total exactly 300 seconds")

    artifact = plan.get("artifact")
    if artifact != {
        "name": f"renders/{normalized_job_id}.mp4",
        "content_type": "video/mp4",
    }:
        raise ScenePlanValidationError("Unsupported synthetic artifact name or content type")

    return plan
