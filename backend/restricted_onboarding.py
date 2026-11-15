"""Restricted first-owner approval and execution, no HTTP/public entry point.

Uses signed delegated Entra API access tokens for operator/approver/target.
A deployment-controlled SHA-256 allowlist establishes administrator authority.
Azure Table Storage stores immutable, create-only proposal and approval rows.
A separate administrator approves the proposal before an administrator can
execute it. Each operation requires explicit identity verification. No raw
JWT, name or email is saved to storage.
"""

import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid5, UUID

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

from first_user_onboarding import (
    OnboardingBlocked, onboard_first_owner, project_id_for_enrollment,
)
from project_models import validate_id
from project_oidc import verify_entra_access_claims

ADMIN_NAMESPACE = UUID("aab38021-c812-4bdc-9d51-73e87e3b39e9")
APPROVAL_TTL = timedelta(hours=24)
PARTITION = "first-owner-approval-v1"


def identity_digest(identity):
    return hashlib.sha256(
        (identity["issuer"] + "|" + identity["subject"]).encode("utf-8")
    ).hexdigest()


def trusted_admin_identity(token, allowed_digests, *, jwks_client_factory=None):
    """Allowed digests must originate outside the user-controlled request."""
    if not isinstance(allowed_digests, frozenset) or not allowed_digests:
        raise OnboardingBlocked("Operator administrator allowlist not configured")
    if any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)
           for v in allowed_digests):
        raise OnboardingBlocked("Invalid administrator allowlist")
    identity = verify_entra_access_claims(token, jwks_client_factory=jwks_client_factory)
    digest = identity_digest(identity)
    if not any(hmac.compare_digest(digest, allowed) for allowed in allowed_digests):
        raise OnboardingBlocked("Verified user is not an enrollment administrator")
    return digest


def _get(table, row):
    try:
        return table.get_entity(partition_key=PARTITION, row_key=row)
    except ResourceNotFoundError:
        return None


def _create_bound(table, entity, fields):
    try:
        table.create_entity(entity=entity)
    except ResourceExistsError:
        prior = _get(table, entity["RowKey"])
        if prior is None or any(prior.get(field) != entity[field] for field in fields):
            raise OnboardingBlocked("Stored administrative approval conflicts with request")
        return False
    return True


def _time(now):
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise OnboardingBlocked("Timezone-aware clock required")
    return now.astimezone(timezone.utc)


def _parse_expiry(entity, now):
    try:
        expiry = datetime.fromisoformat(entity["expiresAt"])
    except (TypeError, ValueError, KeyError) as exc:
        raise OnboardingBlocked("Invalid approval expiry") from exc
    if expiry.tzinfo is None or now >= expiry:
        raise OnboardingBlocked("Approval expired; start a new enrollment")


def proposal_details(enrollment_id, target_token, project_name, *,
                     jwks_client_factory=None):
    validate_id(enrollment_id)
    if (not isinstance(project_name, str) or
            not 1 <= len(project_name.strip()) <= 120 or
            project_name != project_name.strip()):
        raise OnboardingBlocked("Normalized project name required")
    target = verify_entra_access_claims(
        target_token, jwks_client_factory=jwks_client_factory)
    return {
        "enrollmentId": enrollment_id,
        "projectId": project_id_for_enrollment(enrollment_id),
        "projectName": project_name,
        "subjectDigest": identity_digest(target),
        "role": "owner",
    }


def propose_enrollment(table, *, enrollment_id, target_token, project_name,
                       proposer_token, allowed_admin_digests,
                       jwks_client_factory=None, now=None):
    proposer = trusted_admin_identity(proposer_token, allowed_admin_digests,
                                      jwks_client_factory=jwks_client_factory)
    details = proposal_details(enrollment_id, target_token, project_name,
                               jwks_client_factory=jwks_client_factory)
    if proposer == details["subjectDigest"]:
        raise OnboardingBlocked("Target may not propose their own owner enrollment")
    timestamp = _time(now)
    proposal = {
        "PartitionKey": PARTITION, "RowKey": "proposal:" + enrollment_id,
        **details,
        "proposerDigest": proposer,
        "createdAt": timestamp.isoformat(),
        "expiresAt": (timestamp + APPROVAL_TTL).isoformat(),
    }
    _create_bound(table, proposal, (
        "enrollmentId", "projectId", "projectName", "subjectDigest",
        "role", "proposerDigest", "createdAt", "expiresAt"))
    return {"enrollment_id": enrollment_id, "project_id": details["projectId"],
            "status": "proposed"}


def approve_enrollment(table, *, enrollment_id, approver_token,
                       allowed_admin_digests, jwks_client_factory=None, now=None):
    validate_id(enrollment_id)
    approver = trusted_admin_identity(approver_token, allowed_admin_digests,
                                      jwks_client_factory=jwks_client_factory)
    request = _get(table, "proposal:" + enrollment_id)
    if request is None:
        raise OnboardingBlocked("Enrollment proposal not found")
    _parse_expiry(request, _time(now))
    if approver in (request.get("proposerDigest"), request.get("subjectDigest")):
        raise OnboardingBlocked("An independent administrator must approve")
    if request.get("role") != "owner" or request.get("projectId") != project_id_for_enrollment(enrollment_id):
        raise OnboardingBlocked("Invalid proposed owner/project")
    approval = {
        "PartitionKey": PARTITION, "RowKey": "approval:" + enrollment_id,
        "enrollmentId": enrollment_id, "projectId": request["projectId"],
        "projectName": request["projectName"],
        "subjectDigest": request["subjectDigest"], "role": "owner",
        "proposerDigest": request["proposerDigest"],
        "approverDigest": approver,
        "approvedAt": _time(now).isoformat(),
        "expiresAt": request["expiresAt"],
    }
    _create_bound(table, approval, (
        "enrollmentId", "projectId", "projectName", "subjectDigest",
        "role", "proposerDigest", "approverDigest", "approvedAt", "expiresAt"))
    return {"enrollment_id": enrollment_id, "status": "approved"}


def execute_enrollment(table, *, enrollment_id, target_token, operator_token,
                       allowed_admin_digests, jwks_client_factory=None, now=None):
    validate_id(enrollment_id)
    operator = trusted_admin_identity(operator_token, allowed_admin_digests,
                                      jwks_client_factory=jwks_client_factory)
    proposal = _get(table, "proposal:" + enrollment_id)
    approval = _get(table, "approval:" + enrollment_id)
    if proposal is None or approval is None:
        raise OnboardingBlocked("Independent proposal and approval required")
    timestamp = _time(now)
    _parse_expiry(proposal, timestamp)
    _parse_expiry(approval, timestamp)
    values = ("enrollmentId", "projectId", "projectName", "subjectDigest",
              "role", "proposerDigest", "expiresAt")
    if any(proposal.get(k) != approval.get(k) for k in values):
        raise OnboardingBlocked("Proposal and approval do not match")
    if approval.get("role") != "owner" or proposal.get("projectId") != project_id_for_enrollment(enrollment_id):
        raise OnboardingBlocked("Unapproved project or role")
    if len({proposal.get("proposerDigest"), approval.get("approverDigest"),
            proposal.get("subjectDigest")}) != 3:
        raise OnboardingBlocked("Independent admin approval required")
    signed = verify_entra_access_claims(target_token, jwks_client_factory=jwks_client_factory)
    if identity_digest(signed) != proposal["subjectDigest"]:
        raise OnboardingBlocked("Target identity does not match approved proposal")
    if operator == proposal["subjectDigest"]:
        raise OnboardingBlocked("Target cannot execute own onboarding")
    # Legacy service accepts canonical operator ID, not arbitrary user input.
    operator_id = str(uuid5(ADMIN_NAMESPACE, operator))

    def approved_operator(**check):
        return (
            check.get("operator_id") == operator_id and
            check.get("enrollment_id") == enrollment_id and
            check.get("project_id") == proposal["projectId"] and
            check.get("subject_digest") == proposal["subjectDigest"] and
            check.get("role") == "owner"
        )

    return onboard_first_owner(
        table, token=target_token, operator_id=operator_id,
        enrollment_id=enrollment_id,
        project_id=proposal["projectId"],
        project_name=proposal["projectName"],
        authorize_operator=approved_operator,
        jwks_client_factory=jwks_client_factory,
    )
