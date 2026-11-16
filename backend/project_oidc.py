"""Entra access-token authentication for project-scoped endpoints.

Fail closed on unconfigured identity, invalid signatures, wrong tenant,
missing delegated scope, or absent internal user mapping.
The API is disabled by default until separately approved.
"""

import hashlib
import os
from uuid import UUID
from urllib.parse import urlparse

import jwt
from azure.core.exceptions import ResourceNotFoundError, AzureError
from project_access import Actor
from project_models import validate_id


class ProjectIdentityError(PermissionError):
    """Invalid or unregistered identity: HTTP 401."""


class ProjectScopeDenied(PermissionError):
    """Valid token lacks delegated API permission: HTTP 403."""


REQUIRED_SCOPE = "access_as_user"


def oidc_config():
    issuer = os.getenv("PROJECT_OIDC_ISSUER", "").strip()
    audience = os.getenv("PROJECT_OIDC_AUDIENCE", "").strip()
    jwks_url = os.getenv("PROJECT_OIDC_JWKS_URL", "").strip()
    if not issuer or not audience or not jwks_url:
        raise ProjectIdentityError("Project identity is not configured")
    # Enforce single-tenant Entra v2 issuer and its exact trusted JWKS.
    prefix = "https://login.microsoftonline.com/"
    suffix = "/v2.0"
    if not issuer.startswith(prefix) or not issuer.endswith(suffix):
        raise ProjectIdentityError("Entra v2 single-tenant issuer required")
    tenant = issuer[len(prefix):-len(suffix)]
    try:
        if str(UUID(tenant)) != tenant:
            raise ValueError("Noncanonical tenant")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProjectIdentityError("Verified tenant UUID required") from exc
    if jwks_url != prefix + tenant + "/discovery/v2.0/keys":
        raise ProjectIdentityError("Entra signing keys must match tenant")
    # Audience is the application client ID or configured API application ID URI.
    try:
        app_id = audience[6:] if audience.startswith("api://") else audience
        if str(UUID(app_id)) != app_id:
            raise ValueError("Noncanonical application ID")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProjectIdentityError("Verified API audience required") from exc
    return issuer, audience, jwks_url


def validate_delegated_scope(claims):
    """Only user-delegated tokens may access /projects.

    Application-only roles (roles claim) do not establish a human user.
    The scp claim must be a space-separated string containing an exact
    access_as_user token, not a substring or an app role.
    """
    if not isinstance(claims, dict):
        raise ProjectIdentityError("Malformed access-token claims")
    raw = claims.get("scp")
    if not isinstance(raw, str) or REQUIRED_SCOPE not in raw.split():
        raise ProjectScopeDenied("Required delegated API scope missing")


def verify_entra_access_claims(token, *, jwks_client_factory=None):
    """Verify a signed Entra API access token without requiring user mapping."""
    issuer, audience, jwks_url = oidc_config()
    if not isinstance(token, str) or not 1 <= len(token) <= 8192:
        raise ProjectIdentityError("Invalid bearer token")
    factory = jwks_client_factory or jwt.PyJWKClient
    try:
        signing_key = factory(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, signing_key.key,
            algorithms=["RS256"],
            issuer=issuer, audience=audience,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "tid"]},
            leeway=30,
        )
    except (jwt.PyJWTError, ValueError, TypeError, AttributeError) as exc:
        raise ProjectIdentityError("Invalid or expired API access token") from exc

    expected_tenant = issuer.split("/")[3]
    if claims.get("tid") != expected_tenant:
        raise ProjectIdentityError("Access token is from a different tenant")
    validate_delegated_scope(claims)

    subject = claims.get("sub")
    if not isinstance(subject, str) or not 1 <= len(subject) <= 256:
        raise ProjectIdentityError("Invalid identity subject")
    return {"issuer": issuer, "subject": subject, "tenant": expected_tenant}


def verify_project_token(token, table, *, jwks_client_factory=None):
    """Verify delegated access and resolve an active registered internal user."""
    identity = verify_entra_access_claims(token, jwks_client_factory=jwks_client_factory)
    issuer, subject = identity["issuer"], identity["subject"]
    digest = hashlib.sha256((issuer + "|" + subject).encode()).hexdigest()
    try:
        mapping = table.get_entity(
            partition_key="project-oidc-users",
            row_key=digest,
        )
    except ResourceNotFoundError as exc:
        raise ProjectIdentityError("Unregistered project identity") from exc
    # Propagate other Azure data failures rather than report false 401.
    if mapping.get("active") is not True:
        raise ProjectIdentityError("Inactive project identity")
    try:
        internal_id = validate_id(mapping.get("userId"))
    except ValueError as exc:
        raise ProjectIdentityError("Invalid registered user identity") from exc
    return Actor(user_id=internal_id, authenticated=True)
