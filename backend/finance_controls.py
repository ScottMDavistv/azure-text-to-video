"""Atomic financial ledger primitives.

Not connected to live FAL execution.

All financial records use one Azure Table partition
to permit atomic conditional batch transactions.
"""

from datetime import datetime, timedelta, timezone
import random
import time
from uuid import UUID, uuid4

from azure.core import MatchConditions
from azure.core.exceptions import (
    AzureError,
    HttpResponseError,
    ResourceNotFoundError,
)
from azure.data.tables import UpdateMode

PARTITION = "render-finance-v1"
MAX_CENTS = 2**31 - 1  # Azure Tables default Python int encoding: Edm.Int32
MAX_RESERVATION_ATTEMPTS = 32
INCREASE_REQUEST_TTL = timedelta(hours=24)
INCREASE_CLOCK_SKEW = timedelta(minutes=5)


class FinanceDenied(RuntimeError):
    pass


class FinanceConflict(FinanceDenied):
    """Definite ETag precondition failure: transaction not committed."""


class AmbiguousCommit(FinanceDenied):
    op_id = None
    """Write may have committed: reconcile using the same operation ID.

    Never blindly retry provider submission or financial settlement.
    """


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


def audit(event, op_id=None, **fields):
    return {
        "PartitionKey": PARTITION,
        "RowKey": "audit:" + (op_id or str(uuid4())),
        "eventType": event,
        "createdAt": now(),
        **fields,
    }


def operation_applied(table, op_id):
    """Read the operation audit row committed atomically with ledger updates.

    Never mistake a failed verification read for a definite non-commit.
    """
    if not op_id:
        return False
    try:
        table.get_entity(partition_key=PARTITION, row_key="audit:" + op_id)
        return True
    except ResourceNotFoundError:
        return False
    except AzureError as exc:
        error = AmbiguousCommit("Cannot verify financial operation outcome")
        error.op_id = op_id
        raise error from exc


def commit(table, actions, op_id=None):
    try:
        # The Tables SDK may resend a POST after the first batch committed.
        # An atomic audit row differentiates that scenario from a real 409/412.
        table.submit_transaction(actions, retry_total=0)
    except HttpResponseError as exc:
        if exc.status_code in (409, 412) and operation_applied(table, op_id):
            return
        if exc.status_code == 412:
            raise FinanceConflict("Concurrent financial update; retryable") from exc
        if exc.status_code in (400, 403, 404, 409, 413):
            raise FinanceDenied("Atomic financial write rejected") from exc
        error = AmbiguousCommit(
            "Financial write outcome uncertain; reconcile before retry")
        error.op_id = op_id
        raise error from exc
    except AzureError as exc:
        error = AmbiguousCommit(
            "Financial write outcome uncertain; reconcile before retry")
        error.op_id = op_id
        raise error from exc


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

    op_id = str(uuid4())
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
            "limit_provisioned", op_id=op_id,
            actorId=actor.user_id,
            scope=scope,
        )),
    ], op_id=op_id)
    return entity


def reserve(
    table, project_id, job_id, estimated_cents
):
    """Reserve project and global budgets atomically.

    Only definite 412 ETag conflicts may be retried. Network errors,
    timeouts and unknown write outcomes must be reconciled by job ID.
    Caller must first verify job ownership and authorization.
    """
    check_id(project_id)
    check_id(job_id)
    amount = check_money(estimated_cents)

    for attempt in range(MAX_RESERVATION_ATTEMPTS):
        project = load(table, scope_key(project_id))
        global_limit = load(table, "global:fal")

        for limit in (project, global_limit):
            available = (
                limit["ceilingCents"]
                - limit["spentCents"]
                - limit["reservedCents"]
            )
            if available < amount:
                raise FinanceDenied("Insufficient spending capacity")

        op_id = str(uuid4())
        reservation = {
            "PartitionKey": PARTITION,
            "RowKey": "reservation:" + job_id,
            "projectId": project_id,
            "reservedCents": amount,
            "actualCents": 0,
            "state": "reserved",
            "createdAt": now(),
        }
        try:
            commit(table, [
                replace(
                    project,
                    reservedCents=project["reservedCents"] + amount,
                ),
                replace(
                    global_limit,
                    reservedCents=global_limit["reservedCents"] + amount,
                ),
                create(reservation),
                create(audit(
                    "spend_reserved", op_id=op_id,
                    projectId=project_id,
                    jobId=job_id,
                    amountCents=amount,
                )),
            ], op_id=op_id)
            return reservation
        except FinanceConflict:
            if attempt + 1 == MAX_RESERVATION_ATTEMPTS:
                raise FinanceDenied(
                    "Reservation contention exhausted; no commit"
                )
            # Capped jitter reduces synchronized retries on global:fal.
            # Never retry ambiguous errors or duplicate create conflicts.
            time.sleep(min(0.003 * (2 ** min(attempt, 5)), 0.08)
                       * random.uniform(0.5, 1.5))

    raise FinanceDenied("Reservation contention exhausted")


def settle(table, job_id, actual_cents, *, provider_receipt=None,
           verify_provider_receipt=None):
    """Settle confirmed provider charges.

    Zero is permitted only when the caller has verified
    that no provider charge occurred. Do not call this
    for uncertain or in-flight FAL requests.
    """
    check_id(job_id)
    actual = check_money(actual_cents, allow_zero=True)
    if not callable(verify_provider_receipt) or provider_receipt is None:
        raise FinanceDenied("Verified provider billing evidence required")
    try:
        valid = verify_provider_receipt(job_id, actual, provider_receipt)
    except Exception as exc:
        raise FinanceDenied("Provider billing verification failed") from exc
    if valid is not True:
        raise FinanceDenied("Unverified provider billing evidence")

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

    op_id = str(uuid4())
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
            "spend_settled", op_id=op_id,
            jobId=job_id,
            projectId=record["projectId"],
            actualCents=actual,
        )),
    ], op_id=op_id)

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
    project_table=None,
    global_fal=False,
    admin_lookup=None,
):
    """Request a new absolute spending ceiling."""
    from project_access import Action, AccessDenied, authorize, Actor
    from project_store import get_membership_role, get_project

    check_id(request_id)
    check_money(proposed_cents)
    if not isinstance(actor, Actor) or not actor.authenticated:
        raise FinanceDenied("Verified actor required")

    if global_fal:
        require_admin(actor, admin_lookup)
        scope = "global:fal"
    else:
        scope = scope_key(project_id)
        if project_table is None:
            raise FinanceDenied("Trusted project membership table required")
        try:
            project = get_project(project_table, project_id)
            if project is None or project.status.value != "active":
                raise FinanceDenied("Active project required")
            membership = get_membership_role(project_table, project_id, actor.user_id)
            authorize(actor, Action.REQUEST_BUDGET, membership)
        except (AccessDenied, ValueError) as exc:
            raise FinanceDenied("Verified project budget permission required") from exc

    current = load(table, scope)

    if proposed_cents <= current["ceilingCents"]:
        raise FinanceDenied(
            "New ceiling must exceed current limit"
        )

    op_id = str(uuid4())
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
            "increase_requested", op_id=op_id,
            requestId=request_id,
            actorId=actor.user_id,
            scope=scope,
        )),
    ], op_id=op_id)

    return request


def require_fresh_increase(request):
    """A pending request expires after 24h; old rows with no timestamp fail closed."""
    created = request.get("createdAt")
    try:
        if not isinstance(created, str):
            raise ValueError("Missing request timestamp")
        timestamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("Untrusted naive timestamp")
        age = datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)
        if age < -INCREASE_CLOCK_SKEW or age > INCREASE_REQUEST_TTL:
            raise ValueError("Expired or future-dated request")
    except (TypeError, ValueError, OverflowError) as exc:
        raise FinanceDenied("Budget increase request expired or invalid") from exc


def cancel_increase(table, actor, request_id, *, admin_lookup=None):
    """Audit a pending increase cancellation with the financial batch."""
    check_id(request_id)
    if getattr(actor, "authenticated", False) is not True:
        raise FinanceDenied("Verified actor required")
    check_id(getattr(actor, "user_id", None))
    request = load(table, "increase:" + request_id)
    if request.get("status") != "pending":
        raise FinanceDenied("Request is not pending")
    if actor.user_id != request.get("requesterId"):
        require_admin(actor, admin_lookup)
    op_id = str(uuid4())
    commit(table, [
        replace(request, status="cancelled", cancelledBy=actor.user_id,
                cancelledAt=now()),
        create(audit("increase_cancelled", op_id=op_id,
                     requestId=request_id, actorId=actor.user_id,
                     scope=request.get("scope", ""))),
    ], op_id=op_id)
    return "cancelled"


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

    require_fresh_increase(request)

    if actor.user_id in (
        request["requesterId"],
        request["firstApproverId"],
    ):
        raise FinanceDenied(
            "Self-approval or duplicate approval"
        )

    op_id = str(uuid4())
    event = create(audit(
        "increase_approval", op_id=op_id,
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
        ], op_id=op_id)
        return "awaiting_second_approval"

    first_approver = check_id(request["firstApproverId"])
    if admin_lookup(first_approver) is not True:
        raise FinanceDenied("Original administrator approval has been revoked")

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
    ], op_id=op_id)

    return "approved"
