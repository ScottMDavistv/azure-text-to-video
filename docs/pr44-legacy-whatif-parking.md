> Superseded in this offline candidate by [solo-operator-preview-offline.md](solo-operator-preview-offline.md). The guarded route is now preview-only and unconditionally disabled; the historical removal/deploy-chain description below records the prior parked baseline, not current workflow behavior. Broad foundation apply remains denied.

# PR #44 legacy inline What-If parking

The legacy standalone `whatif` dispatch route is parked: `whatif_admission`
and `owner_whatif` are removed from the executable guarded worker workflow,
as are the standalone operation choice and its target/digest inputs. Validation
rejects the removed operation. No replacement caller or callee is installed.

The exact removed approval, identity-separation, expiry and image-only logic
remains in Git history at `947313ae564b2091bd552aa18170f05defe332d4`, path
`.github/workflows/guarded-azure-worker-deploy.yml`. The admission verifier and
existing negative-path/approval tests remain unchanged. Historical code is not
permission to restore or execute it.

The existing `validate → preview → whatif → deploy` chain is retained. Its
`preview`, deployment-precondition `whatif`, and `deploy` jobs are unchanged.
Deployment still requires manual main dispatch by Scott, matching reviewed SHA,
confirmation, enabled-deployment and protected-main flags, validation, preview,
and image-only checks. Removing the standalone route does not bypass any need
or add a deployment route. The existing deployment-precondition What-If is not
the removed standalone owner route.

The landed #92 scanner, byte pins, real-YAML fixtures and tests remain unchanged.
Both reserved callee extensions remain absent from executable workflows.
The proposed pair exists only as inert fixtures. Exact callee commit provenance,
protected staging, GitHub workflow validation/context behavior, environment and
OIDC configuration, and live Azure evidence remain unverified. This parking
change authorizes no publication, workflow run, activation or deployment.
