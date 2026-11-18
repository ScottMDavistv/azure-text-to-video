"""
Secret-free runtime security configuration report.

Status only. No credentials are included in output.

This does not audit Azure networking, managed identities,
IAM roles or deployed infrastructure.
"""

from api_access import (
    api_auth_configured,
    api_auth_required,
)
from job_admin_auth import (
    admin_endpoints_enabled,
    job_admin_token_configured,
)
from video_routes import downloads_enabled


def get_runtime_security_status():
    job_auth_required = api_auth_required()

    return {
        "job_api_auth_required": job_auth_required,
        "job_api_auth_configured": (
            api_auth_configured()
            if job_auth_required
            else False
        ),
        "admin_endpoints_enabled": (
            admin_endpoints_enabled()
        ),
        "admin_token_configured": (
            job_admin_token_configured()
        ),
        "video_downloads_enabled": downloads_enabled(),
        "user_ownership_authorization_implemented": False,
        "public_exposure_approved": False,
    }
