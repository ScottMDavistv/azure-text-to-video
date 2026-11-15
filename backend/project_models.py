"""Project identity, roles, and validation."""

from dataclasses import dataclass
from enum import Enum
from uuid import UUID, uuid4


class ProjectRole(str, Enum):
    OWNER = "owner"
    MEMBER = "member"
    ADMIN = "admin"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


def validate_id(value):
    """Require a canonical UUID string."""
    if not isinstance(value, str):
        raise ValueError("ID must be a string")

    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("Invalid UUID") from exc

    if str(parsed) != value:
        raise ValueError("ID must be canonical UUID")

    return value


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    owner_id: str
    status: ProjectStatus = ProjectStatus.ACTIVE

    def __post_init__(self):
        validate_id(self.project_id)
        validate_id(self.owner_id)

        if not isinstance(self.name, str):
            raise ValueError("Invalid project name")

        if not 1 <= len(self.name.strip()) <= 120:
            raise ValueError("Project name length invalid")

        if not isinstance(self.status, ProjectStatus):
            raise ValueError("Invalid project status")


def new_project(name, owner_id):
    validate_id(owner_id)

    if not isinstance(name, str):
        raise ValueError("Invalid project name")

    return Project(
        project_id=str(uuid4()),
        name=name.strip(),
        owner_id=owner_id,
    )
