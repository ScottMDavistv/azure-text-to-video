"""Atomic financial ledger primitives.

Not connected to live FAL execution.

All financial records use one Azure Table partition
to permit atomic conditional batch transactions.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from azure.core import MatchConditions
from azure.core.exceptions import (
    HttpResponseError,
    ResourceNotFoundError,
)
from azure.data.tables import UpdateMode

PARTITION = "render-finance-v1"
MAX_CENTS = 10**12


class FinanceDenied(RuntimeError):
    pass


def check_id(value):
    try:
        if (
            not isinstance(value, str)
            or str(UUID(value)) != value
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise FinanceDenied("Invalid identifier") from exc
    return value


def check_money(value, allow_zero=False):
    minimum = 0 if allow_zero else 1
    if (
        type(value) is not int
        or not minimum <= value <= MAX_CENTS
    ):
        raise FinanceDenied(
            "Amount must be bounded integer cents"
        )
    return value


def now():
    return datetime.now(timezone.utc).isoformat()


def scope_key(project_id=None):
    if project_id is None:
        return "global:fal"
    return "project:" + check_id(project_id)


def load(table, key):
    try:
        entity = table.get_entity(
            partition_key=PARTITION,
            row_key=key,
        )
    except ResourceNotFoundError as exc:
        raise FinanceDenied(
            "Financial record not found"
        ) from exc

    metadata = getattr(entity, "metadata", {}) or {}
    if not metadata.get("etag"):
        raise FinanceDenied("Missing concurrency ETag")

    return entity


def replace(entity, **changes):
    return (
        "update",
        {**dict(entity), **changes},
        {
            "mode": UpdateMode.REPLACE,
            "etag": entity.metadata["etag"],
            "match_condition":
                MatchConditions.IfNotModified,
        },
    )


def create(entity):
    return ("create", entity)


def audit(event, **fields):
    return {
        "PartitionKey": PARTITION,
        "RowKey": "audit:" + str(uuid4()),
        "eventType": event,
        "createdAt": now(),
        **fields,
    }


def commit(table, actions):
    try:
        table.submit_transaction(actions)
    except (HttpResponseError, ResourceNotFoundError) as exc:
        raise FinanceDenied(
            "Atomic transaction failed; "
            "reconciliation required before retry"
        ) from exc


def require_admin(actor, admin_lookup):
    if (
        not getattr(actor, "authenticated", False)
        or not callable(admin_lookup)
    ):
        raise FinanceDenied("Trusted identity required")

    check_id(getattr(actor, "user_id", None))

    if admin_lookup(actor.user_id) is not True:
        raise FinanceDenied(
            "Verified administrator required"
        )


def provision_limit(
    table, actor, scope, ceiling_cents, admin_lookup
):
    """Create a limit once. Never overwrite existing data."""
    require_admin(actor, admin_lookup)
    check_money(ceiling_cents)

    if scope != "global:fal":
        if (
            not isinstance(scope, str)
            or not scope.startswith("project:")
        ):
            raise FinanceDenied("Invalid budget scope")
        check_id(scope[len("project:"):])

    entity = {
        "PartitionKey": PARTITION,
        "RowKey": scope,
        "ceilingCents": ceiling_cents,
        "reservedCents": 0,
        "spentCents": 0,
        "schemaVersion": 1,
    }

    commit(table, [
        create(entity),
        create(audit(
            "limit_provisioned",
            actorId=actor.user_id,
            scope=scope,
        )),
    ])
    return entity


def reserve(
    table, project_id, job_id, estimated_cents
):
    """Reserve project and global spending atomically.

    Caller must first verify job ownership and authorization.
    """

    check_id(project_id)
    check_id(job_id)
    amount = check_money(estimated_cents)

    project = load(table, scope_key(project_id))
    global_limit = load(table, "global:fal")

    for limit in (project, global_limit):
        available = (
            limit["ceilingCents"]
            - limit["spentCents"]
            - limit["reservedCents"]
        )
        if available < amount:
            raise FinanceDenied(
                "Insufficient spending capacity"
            )

    reservation = {
        "PartitionKey": PARTITION,
        "RowKey": "reservation:" + job_id,
        "projectId": project_id,
        "reservedCents": amount,
        "actualCents": 0,
        "state": "reserved",
        "createdAt": now(),
    }

    commit(table, [
        replace(
            project,
            reservedCents=(
                project["reservedCents"] + amount
            ),
        ),
        replace(
            global_limit,
            reservedCents=(
                global_limit["reservedCents"] + amount
            ),
        ),
        create(reservation),
        create(audit(
            "spend_reserved",
            projectId=project_id,
            jobId=job_id,
            amountCents=amount,
        )),
    ])

    return reservation


def settle(table, job_id, actual_cents):
    """Settle confirmed provider charges.

    Zero is permitted only when the caller has verified
    that no provider charge occurred. Do not call this
    for uncertain or in-flight FAL requests.
    """
    check_id(job_id)
    actual = check_money(
        actual_cents, allow_zero=True
    )

    record = load(table, "reservation:" + job_id)

    if record["state"] != "reserved":
        raise FinanceDenied(
            "Reservation already closed"
        )

    amount = record["reservedCents"]

    if actual > amount:
        raise FinanceDenied(
            "Actual charge exceeds reservation; "
            "manual reconciliation required"
        )

    project = load(
        table, scope_key(record["projectId"])
    )
    global_limit = load(table, "global:fal")

    updates = []

    for limit in (project, global_limit):
        if limit["reservedCents"] < amount:
            raise FinanceDenied(
                "Inconsistent reserved balance"
            )

        updates.append(replace(
            limit,
            reservedCents=(
                limit["reservedCents"] - amount
            ),
            spentCents=(
                limit["spentCents"] + actual
            ),
        ))

    commit(table, updates + [
        replace(
            record,
            state="settled",
            actualCents=actual,
            settledAt=now(),
        ),
        create(audit(
            "spend_settled",
            jobId=job_id,
            projectId=record["projectId"],
            actualCents=actual,
        )),
    ])

    return {
        "job_id": job_id,
        "state": "settled",
        "actual_cents": actual,
    }


def request_increase(
    table,
    actor,
    project_id,
    proposed_cents,
    request_id,
    *,
    role=None,
    global_fal=False,
    admin_lookup=None,
):
    """Request a new absolute spending ceiling."""
    from project_access import (
        Action, AccessDenied, authorize
    )

    check_id(request_id)
    check_money(proposed_cents)

    if global_fal:
        require_admin(actor, admin_lookup)
        scope = "global:fal"
    else:
        scope = scope_key(project_id)
        try:
            authorize(
                actor, Action.REQUEST_BUDGET, role
            )
        except AccessDenied as exc:
            raise FinanceDenied(
                "Project budget permission required"
            ) from exc

    current = load(table, scope)

    if proposed_cents <= current["ceilingCents"]:
        raise FinanceDenied(
            "New ceiling must exceed current limit"
        )

    request = {
        "PartitionKey": PARTITION,
        "RowKey": "increase:" + request_id,
        "scope": scope,
        "requesterId": actor.user_id,
        "proposedCents": proposed_cents,
        "status": "pending",
        "firstApproverId": "",
        "secondApproverId": "",
        "createdAt": now(),
    }

    commit(table, [
        create(request),
        create(audit(
            "increase_requested",
            requestId=request_id,
            actorId=actor.user_id,
            scope=scope,
        )),
    ])

    return request


def approve_increase(
    table, actor, request_id, admin_lookup
):
    """Two distinct verified administrator approvals."""
    require_admin(actor, admin_lookup)
    check_id(request_id)

    request = load(
        table, "increase:" + request_id
    )

    if request["status"] != "pending":
        raise FinanceDenied(
            "Request is not pending"
        )

    if actor.user_id in (
        request["requesterId"],
        request["firstApproverId"],
    ):
        raise FinanceDenied(
            "Self-approval or duplicate approval"
        )

    event = create(audit(
        "increase_approval",
        requestId=request_id,
        actorId=actor.user_id,
    ))

    if not request["firstApproverId"]:
        commit(table, [
            replace(
                request,
                firstApproverId=actor.user_id,
            ),
            event,
        ])
        return "awaiting_second_approval"

    limit = load(table, request["scope"])

    if (
        request["proposedCents"]
        <= limit["ceilingCents"]
    ):
        raise FinanceDenied(
            "Stale increase request"
        )

    commit(table, [
        replace(
            request,
            secondApproverId=actor.user_id,
            status="approved",
            approvedAt=now(),
        ),
        replace(
            limit,
            ceilingCents=request["proposedCents"],
        ),
        event,
    ])

    return "approved"
