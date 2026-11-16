# Bulk 03 orchestration foundation

**Development-only.** The current live worker is unchanged. This code
does not enable a provider, install schedules, start background pollers,
or deploy to Azure.

## Added

- `fal_budget_guard.py`: reservation-by-job/project verifier backed by
  durable Azure Table balances; advisory availability check.
- `held_jobs.py`: persistent holds with reason, estimated cost, owner,
  priority, wait time, conditional cancellation and reconsideration.
- `job_scheduler.py`: deterministic High Priority scheduling, cross-project
  alternation, and age-based anti-starvation policy.
- `test_bulk03_orchestration.py`: focused offline behavior tests.

## What remains before considering Bulk 03 production-complete

The existing `worker_service.py` / `fal_renderer.py` flow still
does not pass a trusted project context or durable budget reservation
through to `fal_safe_submission.py`. Specifically:

1. Enforce validated authenticated owner/admin membership at API and
   worker boundaries; never trust client-supplied authorization callbacks.
2. Wire an ETag-conditional held-job dispatcher to atomic
   `finance_controls.reserve`; avoid selecting without a claim.
3. Persist notifications and audit events, and automatically reconsider
   eligible holds after approved budgets (not just callable primitives).
4. Persist enough provider response metadata to resume safe polling
   without resubmitting; reconcile uncertain responses manually.
5. Reconcile verified provider costs, including over-reservation amounts
   and provider charges when outcomes are uncertain.
6. Perform concurrency/integration tests against Azure Tables, plus
   simulated crash and duplicate-queue delivery cases.
7. Update Docker/worker deployment manifests only after the safety gates.

No live render worker changes or production spending-cap activation
are included in this commit.
