from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from project_models import (
    ProjectRole,
    new_project,
    validate_id,
)

from project_access import (
    Actor,
    Action,
    AccessDenied,
    authorize,
    distinct_admin_approvals,
)

from project_store import create_project, add_membership


def uid():
    return str(uuid4())


def test_create_project():
    project = new_project("Demo", uid())
    assert project.name == "Demo"


def test_invalid_project_name():
    with pytest.raises(ValueError):
        new_project("", uid())


def test_invalid_uuid():
    with pytest.raises(ValueError):
        validate_id("invalid")


def test_unauthenticated_denied():
    actor = Actor(uid(), False)
    with pytest.raises(AccessDenied):
        authorize(actor, Action.READ, ProjectRole.OWNER)


def test_owner_can_request_budget():
    actor = Actor(uid(), True)
    assert authorize(
        actor, Action.REQUEST_BUDGET, ProjectRole.OWNER
    )


def test_member_cannot_request_budget():
    actor = Actor(uid(), True)
    with pytest.raises(AccessDenied):
        authorize(
            actor, Action.REQUEST_BUDGET,
            ProjectRole.MEMBER
        )


def test_admin_approval_restricted():
    actor = Actor(uid(), True)
    with pytest.raises(AccessDenied):
        authorize(actor, Action.ADMIN_APPROVAL)


def test_two_distinct_approvers():
    assert distinct_admin_approvals(
        uid(), [uid(), uid()]
    )


def test_duplicate_approvers_denied():
    approver = uid()
    assert not distinct_admin_approvals(
        uid(), [approver, approver]
    )


def test_requester_self_approval_denied():
    requester = uid()
    assert not distinct_admin_approvals(
        requester, [requester, uid()]
    )


def test_create_project_storage():
    table = MagicMock()
    project = new_project("Demo", uid())
    entity = create_project(table, project)
    assert entity["PartitionKey"] == "projects"
    table.create_entity.assert_called_once()


def test_create_membership_storage():
    table = MagicMock()
    entity = add_membership(
        table, uid(), uid(), ProjectRole.MEMBER
    )
    assert entity["role"] == "member"
