"""Azure Table project persistence primitives.

The caller must enforce authentication and authorization.
No API routes are exposed by this module.
"""

from datetime import datetime, timezone

from azure.core.exceptions import ResourceNotFoundError

from project_models import (
    Project,
    ProjectRole,
    ProjectStatus,
    validate_id,
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def create_project(table, project):
    if not isinstance(project, Project):
        raise ValueError("Invalid project")

    entity = {
        "PartitionKey": "projects",
        "RowKey": project.project_id,
        "name": project.name,
        "ownerId": project.owner_id,
        "status": project.status.value,
        "schemaVersion": 1,
        "createdAt": utc_now(),
    }

    table.create_entity(entity=entity)
    return entity


def get_project(table, project_id):
    validate_id(project_id)

    try:
        entity = table.get_entity(
            partition_key="projects",
            row_key=project_id,
        )
    except ResourceNotFoundError:
        return None

    return Project(
        project_id=entity["RowKey"],
        name=entity["name"],
        owner_id=entity["ownerId"],
        status=ProjectStatus(entity["status"]),
    )


def add_membership(table, project_id, user_id, role):
    validate_id(project_id)
    validate_id(user_id)

    if not isinstance(role, ProjectRole):
        raise ValueError("Invalid role")

    entity = {
        "PartitionKey": f"project-members:{project_id}",
        "RowKey": f"member:{user_id}",
        "role": role.value,
        "schemaVersion": 1,
        "createdAt": utc_now(),
    }

    table.create_entity(entity=entity)
    return entity


def get_membership_role(table, project_id, user_id):
    validate_id(project_id)
    validate_id(user_id)

    try:
        entity = table.get_entity(
            partition_key=f"project-members:{project_id}",
            row_key=f"member:{user_id}",
        )
    except ResourceNotFoundError:
        return None

    return ProjectRole(entity["role"])
