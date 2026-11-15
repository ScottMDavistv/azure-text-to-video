"""Project authorization policies.

These are policy primitives, not authenticated API routes.
"""

from dataclasses import dataclass
from enum import Enum

from project_models import ProjectRole, validate_id


class AccessDenied(PermissionError):
    pass


class Action(str, Enum):
    READ = "read"
    CREATE_JOB = "create_job"
    CANCEL_JOB = "cancel_job"
    REQUEST_BUDGET = "request_budget"
    MANAGE_MEMBERS = "manage_members"
    ADMIN_APPROVAL = "admin_approval"


@dataclass(frozen=True)
class Actor:
    user_id: str
    authenticated: bool
    system_admin: bool = False

    def __post_init__(self):
        validate_id(self.user_id)


PERMISSIONS = {
    ProjectRole.MEMBER: {
        Action.READ,
        Action.CREATE_JOB,
    },
    ProjectRole.OWNER: {
        Action.READ,
        Action.CREATE_JOB,
        Action.CANCEL_JOB,
        Action.REQUEST_BUDGET,
        Action.MANAGE_MEMBERS,
    },
    ProjectRole.ADMIN: {
        Action.READ,
        Action.CREATE_JOB,
        Action.CANCEL_JOB,
        Action.REQUEST_BUDGET,
        Action.MANAGE_MEMBERS,
    },
}


def authorize(actor, action, role=None):
    if not isinstance(actor, Actor):
        raise AccessDenied("Identity required")

    if not actor.authenticated:
        raise AccessDenied("Authentication required")

    if not isinstance(action, Action):
        raise AccessDenied("Unknown action")

    if action == Action.ADMIN_APPROVAL:
        if not actor.system_admin:
            raise AccessDenied("Administrator required")
        return True

    if not isinstance(role, ProjectRole):
        raise AccessDenied("Project membership required")

    if action not in PERMISSIONS[role]:
        raise AccessDenied("Permission denied")

    return True


def distinct_admin_approvals(requester_id, approver_ids):
    """Check distinct IDs only.

    Does not verify administrator status or approval
    records. Those checks belong to Bulk 02.
    """
    validate_id(requester_id)

    if not isinstance(approver_ids, list):
        return False

    if len(approver_ids) < 2:
        return False

    for approver in approver_ids:
        validate_id(approver)

    return (
        len(set(approver_ids)) >= 2
        and requester_id not in approver_ids
    )
