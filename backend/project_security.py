"""Fail-closed trusted project and job authorization boundary.

There is no user identity provider configured in the existing app.
Only server-verified identity resolvers may create Actor objects.
Never read a user ID, role, or approval flag from request JSON.
"""

from dataclasses import dataclass

from project_access import Action, AccessDenied, Actor, authorize
from project_models import ProjectRole, validate_id
from project_store import get_membership_role, get_project


class IdentityUnavailable(AccessDenied):
    pass


def resolve_verified_actor(principal, *, verify_principal):
    """Accept only a trusted server-side verifier's response.

    The verifier must validate token signature, issuer, audience, and
    expiry and map the subject to the internal canonical UUID.
    This module intentionally DOES NOT implement OIDC configuration.
    """
    if not callable(verify_principal):
        raise IdentityUnavailable("Identity verifier not configured")
    claims = verify_principal(principal)
    if not isinstance(claims, dict) or claims.get("verified") is not True:
        raise IdentityUnavailable("Unverified principal")
    user_id = validate_id(claims.get("user_id"))
    return Actor(user_id=user_id, authenticated=True)


def require_project_role(table, actor, project_id, action):
    """Read verified membership from the backend table, not caller input."""
    if not isinstance(actor, Actor) or not actor.authenticated:
        raise AccessDenied("Verified actor required")
    validate_id(project_id)
    project = get_project(table, project_id)
    if project is None or project.status.value != "active":
        raise AccessDenied("Active project required")
    role = get_membership_role(table, project_id, actor.user_id)
    # Creation of owner membership is part of future project onboarding.
    authorize(actor, action, role)
    return role


def require_job_access(table, job, actor, action):
    """Require the job's server-recorded project and trusted membership."""
    if not isinstance(job, dict) and not hasattr(job, "get"):
        raise AccessDenied("Job storage record required")
    project_id = validate_id(job.get("projectId"))
    return require_project_role(table, actor, project_id, action)


def authorized_cancellation(project_table, actor, job):
    require_job_access(project_table, job, actor, Action.CANCEL_JOB)
    return True
