# Bulk 03 — FAL submission safety (partial implementation)

Status: **Not deployed. Not enabled. Not integrated into the live worker.**

This integration branch is based on Bulk 02 commit 59c747c.
The financial-control modules are now present but not connected to the
FAL worker or the guarded submission verifier.

## Included

- `submit_once` is fail-closed and requires a trusted reservation
  verifier for the exact job before creating the FAL submission intent.
- Persist submission intent and conditionally claim it before sending
  the first provider request.
- Persist provider request ID after accepted submission.
- No automatic retry if submission or persistence becomes uncertain.
- Guarded polling helper reuses the one saved submission response.
- Unit tests for ordering, denial, uncertainty, and duplicate prevention.

## Critical remaining work

1. Implement trusted project/job authorization and durable reservation
   verification (the callback is a seam, not financial enforcement).
2. Wire guarded submission into actual worker path; currently the
   original FalVideoRenderer still calls submit_and_wait directly.
3. Design recovery of submitted work after worker crash without
   duplicate provider submission; persist validated status/response
   URLs or derive them safely from trusted identifiers.
4. Verify FAL cost reconciliation and actual charges, including
   the possible actual-cost-over-reservation case.
5. Implement held-job persistence, cancellation, priority fairness,
   anti-starvation, and automatic reconsideration.
6. Azure Table concurrency and FAL simulated integration tests.
7. Update backend Dockerfiles when activating any new module.
8. Keep live rendering flags disabled until end-to-end approval.

No main branch changes, Azure deployments, paid requests, or
automatic activation are performed by this code.
