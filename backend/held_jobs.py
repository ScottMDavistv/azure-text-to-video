"""Persistent held-job coordination. No automatic provider execution.

Uses Azure Table ETags for conditional transitions and one stable
row per job. A periodic scheduler must call reconsider_held_job
after budget changes; no background service is started here.
"""

from datetime import datetime, timezone
from uuid import UUID

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError, ResourceExistsError
from azure.data.tables import UpdateMode

PARTITION = "fal-held-jobs"


class HeldJobDenied(RuntimeError):
    pass


def _id(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise HeldJobDenied("Canonical job/project ID required") from exc
    return value


def _update(table, original, **changes):
    try:
        table.update_entity(
            entity={**dict(original), **changes},
            mode=UpdateMode.REPLACE,
            etag=original.metadata["etag"],
            match_condition=MatchConditions.IfNotModified,
        )
    except HttpResponseError as exc:
        raise HeldJobDenied("Concurrent change; retry read, not provider submission") from exc


def hold_job(table, job_id, project_id, estimated_cents,
             *, reason, priority="normal", owner_id):
    """Create once; caller must verify ownership and job state."""
    from finance_controls import check_money

    _id(job_id)
    _id(project_id)
    _id(owner_id)
    check_money(estimated_cents)

    if reason not in ("project_budget", "global_fal_limit"):
        raise HeldJobDenied("Invalid hold reason")
    if priority not in ("normal", "high"):
        raise HeldJobDenied("Invalid priority")

    entity = {
        "PartitionKey": PARTITION,
        "RowKey": job_id,
        "jobId": job_id,
        "projectId": project_id,
        "ownerId": owner_id,
        "estimatedCents": estimated_cents,
        "holdReason": reason,
        "priority": priority,
        "status": "held",
        "heldAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    try:
        table.create_entity(entity)
    except ResourceExistsError as exc:
        raise HeldJobDenied("Existing job hold must be reconciled") from exc
    return entity


def cancel_held_job(table, job_id, *, actor_id,
                    authorize_cancellation):
    """Only an authorized requester may cancel; no provider submission."""
    _id(job_id)
    _id(actor_id)
    record = table.get_entity(partition_key=PARTITION, row_key=job_id)
    if record["status"] != "held":
        raise HeldJobDenied("Job no longer held")
    if not callable(authorize_cancellation) or (
        authorize_cancellation(actor_id, record["projectId"], job_id) is not True
    ):
        raise HeldJobDenied("Cancellation not authorized")
    _update(table, record, status="cancelled",
            updatedAt=datetime.now(timezone.utc).isoformat())
    return "cancelled"


def reconsider_held_job(table, job_id, *, verify_budget):
    """Mark eligible only after trusted financial-capacity verification.

    Eligibility is NOT a reservation. A dispatcher must perform a
    separate atomic reservation before ever contacting FAL.
    """
    _id(job_id)
    record = table.get_entity(partition_key=PARTITION, row_key=job_id)
    if record["status"] != "held":
        raise HeldJobDenied("Job no longer held")
    if not callable(verify_budget) or (
        verify_budget(record["projectId"], record["estimatedCents"]) is not True
    ):
        return "held"
    _update(table, record, status="eligible",
            updatedAt=datetime.now(timezone.utc).isoformat())
    return "eligible"


def list_project_holds(table, project_id):
    """Admin/owner authorization must precede this call."""
    _id(project_id)
    # Service-side partition filter; filter project ID again on returned data.
    rows = table.query_entities(
        query_filter="PartitionKey eq @partition and projectId eq @project",
        parameters={"partition": PARTITION, "project": project_id},
    )
    return [dict(r) for r in rows if r.get("projectId") == project_id]


def reconsider_project_holds(table, project_id, *, verify_budget, limit=100):
    """Batch reconsider one project's held jobs after budget approval.

    Called by an explicit budget-approved handler or scheduled scan.
    This does not itself reserve funds or submit to FAL.
    """
    _id(project_id)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise HeldJobDenied("Invalid batch size")
    if not callable(verify_budget):
        raise HeldJobDenied("Trusted financial capacity verifier required")
    rows = list_project_holds(table, project_id)
    results = {"eligible": 0, "held": 0, "conflicts": 0}
    for row in rows[:limit]:
        if row.get("status") != "held":
            continue
        try:
            status = reconsider_held_job(
                table, row["jobId"], verify_budget=verify_budget,
            )
            results[status] += 1
        except HeldJobDenied:
            results["conflicts"] += 1
    return results


def held_job_dashboard(table, project_id, *, now_time=None):
    """Trusted-caller dashboard projection (authentication is external)."""
    from datetime import datetime
    _id(project_id)
    current = now_time or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise HeldJobDenied("Timezone-aware clock required")
    output = []
    for row in list_project_holds(table, project_id):
        if row.get("status") not in ("held", "eligible", "reserving", "reserved"):
            continue
        held_at = datetime.fromisoformat(row["heldAt"].replace("Z", "+00:00"))
        elapsed = max(0, int((current - held_at).total_seconds()))
        output.append({
            "job_id": row["jobId"],
            "status": row["status"],
            "hold_reason": row["holdReason"],
            "estimated_cents": row["estimatedCents"],
            "priority": row["priority"],
            "waiting_seconds": elapsed,
            "actions": ["cancel"] if row["status"] == "held" else [],
        })
    return sorted(output, key=lambda row: (-row["waiting_seconds"], row["job_id"]))
