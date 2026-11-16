"""Administrator-approved, replay-safe first-user project onboarding.

No HTTP route or background worker exposes this operation. All identity and
administrator checks must succeed before the first storage write. The storage
model spans multiple partitions: create-only deterministic entities and an
audit-start record enable conflict detection and safe recovery, NOT atomicity.
Never log or persist access tokens.
"""

import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid5

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

from project_models import ProjectRole, validate_id
from project_oidc import verify_entra_access_claims


class OnboardingBlocked(RuntimeError):
    pass


# Immutable namespace solely for stable dev onboarding identifiers.
_NAMESPACE = UUID("fc8a9953-6ca7-4f84-8a39-ddac36d0bd3e")


def _read(table, partition, row):
    try:
        return table.get_entity(partition_key=partition, row_key=row)
    except ResourceNotFoundError:
        return None


def _create_once(table, entity, *, expected):
    """Create only, accepting a replay if *every* security field matches."""
    try:
        table.create_entity(entity=entity)
        return "created"
    except ResourceExistsError:
        previous = _read(table, entity["PartitionKey"], entity["RowKey"])
        if previous is None or any(previous.get(key) != value for key, value in expected.items()):
            raise OnboardingBlocked("Existing onboarding entity conflicts with approved request")
        return "replayed"


def _require_approval(authorize_operator, operator_id, enrollment_id, digest, project_id):
    if not callable(authorize_operator):
        raise OnboardingBlocked("Trusted administrative approval resolver required")
    try:
        result = authorize_operator(
            operator_id=operator_id,
            enrollment_id=enrollment_id,
            subject_digest=digest,
            project_id=project_id,
            role="owner",
        )
    except Exception as exc:
        raise OnboardingBlocked("Administrative approval unavailable") from exc
    if result is not True:
        raise OnboardingBlocked("Explicit operator approval required")


def onboard_first_owner(
    table, *, token, operator_id, enrollment_id, project_name,
    project_id, authorize_operator, jwks_client_factory=None,
):
    """Onboard an approved owner using a real cryptographically checked token.

    Caller supplies an operator approved enrollment and a stable project UUID.
    The trusted resolver MUST check an independent admin identity, a durable
    approval bound to this digest/project/role, and any required separation
    of duties. The supplied operator_id alone conveys no permissions.
    """
    operator_id = validate_id(operator_id)
    enrollment_id = validate_id(enrollment_id)
    project_id = validate_id(project_id)
    if not isinstance(project_name, str) or not 1 <= len(project_name.strip()) <= 120:
        raise OnboardingBlocked("Valid project name required")
    if project_name != project_name.strip():
        raise OnboardingBlocked("Project name must be normalized")
    # Verify the signed issuer, audience, tid, expiry and delegated scope
    # before consulting either authorization or storage.
    identity = verify_entra_access_claims(
        token, jwks_client_factory=jwks_client_factory,
    )
    digest = hashlib.sha256(
        (identity["issuer"] + "|" + identity["subject"]).encode("utf-8")
    ).hexdigest()
    user_id = str(uuid5(_NAMESPACE, "user:" + digest))
    expected_project_id = str(uuid5(_NAMESPACE, "project:" + enrollment_id))
    if project_id != expected_project_id:
        raise OnboardingBlocked("Project ID must match the approved enrollment")
    _require_approval(authorize_operator, operator_id, enrollment_id, digest, project_id)

    audit_partition = "project-onboarding-audit"
    audit_row = "first-owner:" + enrollment_id
    now = datetime.now(timezone.utc).isoformat()
    audit = {
        "PartitionKey": audit_partition, "RowKey": audit_row,
        "eventType": "first_owner_onboarding_started",
        "operatorId": operator_id, "enrollmentId": enrollment_id,
        "subjectDigest": digest, "userId": user_id, "projectId": project_id,
        "projectName": project_name, "role": "owner",
        "createdAt": now,
    }
    project = {
        "PartitionKey": "projects", "RowKey": project_id,
        "name": project_name, "ownerId": user_id,
        "status": "active", "schemaVersion": 1, "createdAt": now,
    }
    membership = {
        "PartitionKey": f"project-members:{project_id}",
        "RowKey": "member:" + user_id, "role": ProjectRole.OWNER.value,
        "schemaVersion": 1, "createdAt": now,
    }
    mapping = {
        "PartitionKey": "project-oidc-users", "RowKey": digest,
        "userId": user_id, "active": True,
        "enrollmentId": enrollment_id, "projectId": project_id,
        "schemaVersion": 1, "createdAt": now,
    }
    completed = {
        "PartitionKey": audit_partition,
        "RowKey": "first-owner-completed:" + enrollment_id,
        "eventType": "first_owner_onboarding_completed",
        "operatorId": operator_id, "enrollmentId": enrollment_id,
        "subjectDigest": digest, "userId": user_id, "projectId": project_id,
        "createdAt": now,
    }

    # A previously provisioned mapping from any other onboarding must not
    # be silently associated with another project or reactivated.
    previous = _read(table, mapping["PartitionKey"], mapping["RowKey"])
    if previous is not None and any(
        previous.get(key) != mapping[key]
        for key in ("userId", "active", "enrollmentId", "projectId")
    ):
        raise OnboardingBlocked("Identity already mapped or revoked; manual review required")

    records = (
        (audit, ("eventType", "operatorId", "enrollmentId", "subjectDigest",
                 "userId", "projectId", "projectName", "role")),
        (project, ("name", "ownerId", "status", "schemaVersion")),
        (membership, ("role", "schemaVersion")),
        # Last authorizing write: never map a user to an incomplete project.
        (mapping, ("userId", "active", "enrollmentId", "projectId", "schemaVersion")),
        (completed, ("eventType", "operatorId", "enrollmentId", "subjectDigest",
                     "userId", "projectId")),
    )
    results = {}
    for entity, fields in records:
        results[entity["RowKey"]] = _create_once(
            table, entity, expected={field: entity[field] for field in fields},
        )
    return {
        "status": "onboarded",
        "user_id": user_id, "project_id": project_id,
        "enrollment_id": enrollment_id,
        "replayed": all(status == "replayed" for status in results.values()),
    }


def project_id_for_enrollment(enrollment_id):
    """Stable ID to bind independent administrator approval to the project."""
    return str(uuid5(_NAMESPACE, "project:" + validate_id(enrollment_id)))
