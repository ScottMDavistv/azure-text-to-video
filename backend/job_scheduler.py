"""Pure, deterministic scheduling for budget-held FAL jobs.

This is a candidate selector, not a live Azure queue consumer.
Callers must perform an ETag-conditional claim after selection.
"""

from datetime import datetime, timezone
from uuid import UUID

HIGH = "high"
NORMAL = "normal"
PRIORITIES = {HIGH, NORMAL}


def _uid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Canonical UUID required")
    return value


def _time(value):
    if not isinstance(value, str):
        raise ValueError("ISO timestamp required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timezone required")
    return parsed.astimezone(timezone.utc)


def _eligible(job):
    if job.get("status") not in ("held", "eligible"):
        return False
    if job.get("holdReason") not in ("project_budget", "global_fal_limit"):
        return False
    _uid(job["jobId"])
    _uid(job["projectId"])
    if job.get("priority") not in PRIORITIES:
        raise ValueError("Invalid priority")
    _time(job["heldAt"])
    return True


def choose_next(held_jobs, *, last_project_id=None,
                now=None, starvation_minutes=120):
    """High priority first; age-promote older normal jobs.

    Across projects at the same effective priority and age class,
    prefer a project other than the last one served when possible.
    Never infer financial capacity: caller must reserve atomically.
    """
    if isinstance(starvation_minutes, bool) or not isinstance(
        starvation_minutes, (int, float)
    ) or starvation_minutes <= 0:
        raise ValueError("Invalid starvation window")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Timezone required")
    now = now.astimezone(timezone.utc)

    candidates = [j for j in held_jobs if _eligible(j)]
    if not candidates:
        return None

    def rank(j):
        waiting = max(0, (now - _time(j["heldAt"])).total_seconds() / 60)
        promoted = waiting >= starvation_minutes
        effective_high = promoted or j["priority"] == HIGH
        return (0 if promoted else 1 if effective_high else 2,
                _time(j["heldAt"]), j["jobId"])

    # A promoted job wins ahead of high-priority jobs, preventing starvation.
    best_class = min(rank(j)[0] for j in candidates)
    group = [j for j in candidates if rank(j)[0] == best_class]
    alternatives = [j for j in group if j["projectId"] != last_project_id]
    if alternatives:
        group = alternatives
    return min(group, key=lambda j: (rank(j)[1], j["jobId"]))
