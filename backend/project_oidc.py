"""Optional OIDC identity verifier for project-scoped endpoints.

Disabled without explicit issuer, audience and JWKS settings.
Sub/issuer map to a pre-provisioned internal UUID, never a client role.
No credential or identity mapping is created automatically.
"""

import hashlib
import os
from urllib.parse import urlparse

import jwt
from azure.core.exceptions import ResourceNotFoundError
from project_access import Actor
from project_models import validate_id


class ProjectIdentityError(PermissionError):
    pass


def oidc_config():
    issuer = os.getenv("PROJECT_OIDC_ISSUER", "").strip()
    audience = os.getenv("PROJECT_OIDC_AUDIENCE", "").strip()
    jwks_url = os.getenv("PROJECT_OIDC_JWKS_URL", "").strip()
    if not issuer or not audience or not jwks_url:
        raise ProjectIdentityError("Project identity is not configured")
    for url in (issuer, jwks_url):
        parsed = urlparse(url)
        if (parsed.scheme != "https" or not parsed.hostname or
                parsed.username or parsed.password or parsed.fragment):
            raise ProjectIdentityError("OIDC URLs must be trusted HTTPS URLs")
    if urlparse(jwks_url).hostname != urlparse(issuer).hostname:
        raise ProjectIdentityError("OIDC JWKS host must match issuer host")
    return issuer, audience, jwks_url


def verify_project_token(token, table, *, jwks_client_factory=None):
    """Verify RS256 JWT against configured issuer/audience/JWKS.

    Production callers must ensure the issuer and JWKS are trusted
    deployment settings. Identity-mapping rows must be provisioned
    via a separate trusted admin process.
    """
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
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=30,
        )
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise ProjectIdentityError("Invalid or expired identity token") from exc

    subject = claims.get("sub")
    if not isinstance(subject, str) or not 1 <= len(subject) <= 256:
        raise ProjectIdentityError("Invalid identity subject")
    digest = hashlib.sha256((issuer + "|" + subject).encode()).hexdigest()
    try:
        mapping = table.get_entity(
            partition_key="project-oidc-users",
            row_key=digest,
        )
    except ResourceNotFoundError as exc:
        raise ProjectIdentityError("Unregistered project identity") from exc
    if mapping.get("active") is not True:
        raise ProjectIdentityError("Inactive project identity")
    return Actor(user_id=validate_id(mapping.get("userId")),
                 authenticated=True)
