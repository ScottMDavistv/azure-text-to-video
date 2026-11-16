# Stage A Azure handoff checklist (not yet deployed)

## Mandatory prerequisites

- [ ] Operator reviews the immutable container digest and verified tests
- [ ] Create separate user-assigned identity with least privilege to approved storage tables and queues
- [ ] Verify no FAL credential is supplied to the Stage A job
- [ ] Verify the manual job runs only `stage_a_runtime_cli --check`
- [ ] Validate Entra tenant issuer/audience/JWKS and mapping onboarding in nonproduction
- [ ] Prove inbox deduplication and manual recovery for ambiguous effects
- [ ] Provision a trusted server-selected list of eligible project IDs
- [ ] Test scheduler leasing and ETag conflicts against real Azure Tables
- [ ] Observe finance and reservation reconciliation audit entries
- [ ] Integrate provider billing evidence through a separately authenticated source
- [ ] Simulate crash after charge settlement and before job completion
- [ ] Validate no route or Docker entrypoint can issue an unreserved paid POST
- [ ] Approve a second-stage scheduled reconciliation deployment separately
- [ ] Review Bulk 01 and Bulk 02 dependency pull requests and enforce required checks

The template is intentionally manual and check-only. This is not a
ready-to-run, automatically scheduled deployment manifest.
