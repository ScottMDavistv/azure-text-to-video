"""Trusted Entra authorization boundary for financial operations.

Only server-verified signed delegated tokens can initiate financial mutations.
The administrator digest allowlist is provided by a privileged deployment
configuration, NEVER by request JSON or the authenticated frontend.
"""
import hashlib
import re

from finance_controls import (
    FinanceDenied, approve_increase, provision_limit, request_increase,
)
from project_oidc import verify_entra_access_claims, verify_project_token


def _verified(table, token, *, jwks_client_factory=None):
    if not isinstance(token, str):
        raise FinanceDenied("Signed delegated access token required")
    try:
        actor = verify_project_token(token, table, jwks_client_factory=jwks_client_factory)
        claims = verify_entra_access_claims(token, jwks_client_factory=jwks_client_factory)
    except PermissionError as exc:
        raise FinanceDenied("Invalid or unregistered Entra identity") from exc
    if not actor.authenticated:
        raise FinanceDenied("Authenticated financial actor required")
    digest = hashlib.sha256((claims["issuer"] + "|" + claims["subject"]).encode()).hexdigest()
    return actor, digest


def _admin_lookup(table, token, allowed_admin_digests, *, jwks_client_factory=None):
    actor, digest = _verified(table, token, jwks_client_factory=jwks_client_factory)
    if (not isinstance(allowed_admin_digests, frozenset) or
            not allowed_admin_digests or
            any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v)
                for v in allowed_admin_digests)):
        raise FinanceDenied("Trusted administrator allowlist required")
    # This must be sourced from operator-controlled environment, not caller input.
    if digest not in allowed_admin_digests:
        raise FinanceDenied("Verified finance administrator required")
    return actor, lambda user_id: user_id == actor.user_id


def request_project_increase_verified(table, *, project_id, request_id,
                                      proposed_cents, access_token,
                                      jwks_client_factory=None):
    actor, _ = _verified(table, access_token, jwks_client_factory=jwks_client_factory)
    return request_increase(
        table, actor, project_id, proposed_cents, request_id,
        project_table=table,
    )


def request_global_increase_verified(table, *, request_id, proposed_cents,
                                     access_token, allowed_admin_digests,
                                     jwks_client_factory=None):
    actor, admin_lookup = _admin_lookup(
        table, access_token, allowed_admin_digests,
        jwks_client_factory=jwks_client_factory,
    )
    return request_increase(
        table, actor, None, proposed_cents, request_id,
        global_fal=True, admin_lookup=admin_lookup,
    )


def approve_increase_verified(table, *, request_id, access_token,
                              allowed_admin_digests, jwks_client_factory=None):
    actor, admin_lookup = _admin_lookup(
        table, access_token, allowed_admin_digests,
        jwks_client_factory=jwks_client_factory,
    )
    return approve_increase(table, actor, request_id, admin_lookup)


def provision_limit_verified(table, *, scope, ceiling_cents, access_token,
                             allowed_admin_digests, jwks_client_factory=None):
    actor, admin_lookup = _admin_lookup(
        table, access_token, allowed_admin_digests,
        jwks_client_factory=jwks_client_factory,
    )
    return provision_limit(table, actor, scope, ceiling_cents, admin_lookup)
