# Bulk 03 — implementation and release gate

Branch: `feature/bulk-03-integrated`. Development implementation.
Do not deploy or activate paid FAL requests based on passing unit tests.

## Delivered in code

- `fal_safe_submission.py`: pre-submission guard, durable intent claim,
  no automatic resubmission on uncertain POST or persistence error.
- `fal_budget_guard.py`: verify job-specific project and global budget
  reservation by reading durable financial records.
- `provider_submission_ledger.py`: persist validated original Fal polling
  URLs with request ID in a conditional update.
- `fal_safe_recovery.py`: resume *polling* a submitted request without
  repeating the paid submission.
- `held_jobs.py`: persistent holds, cancellation authorization hook,
  project dashboard projection and batch budget reconsideration.
- `job_scheduler.py`: High Priority preference, age-promotion to
  prevent starvation, and alternating project preference.
- `held_job_dispatch.py`: conditional claim before budget reservation
  and non-mutating diagnosis of uncertain reservations.
- `held_job_coordinator.py`: advisory capacity filtering and explicit
  budget-approval reconsideration hook.
- `budgeted_fal_execution.py`: explicitly opt-in worker adapter requiring
  claimed job, trusted authorization and reserved funds; verified-only
  financial settlement boundary.
- Offline tests and Python 3.12 GitHub Actions test workflow.

## Production readiness — NOT approved

These requirements still need implementation and verification:

1. Connect authenticated project membership to API and worker. Project
   IDs and authorization callbacks must come only from trusted storage.
2. Connect the opt-in executor and coordinator to a real durable
   scheduler and worker trigger, retaining a kill switch.
3. Make per-project fairness consistent across multiple scheduler
   replicas with durable state; the current algorithm is a pure
   candidate selector and cannot guarantee fairness under races.
4. Add durable notification delivery/outbox and audit around held-job
   transitions, budget changes, and recovery decisions.
5. Implement automatic periodic reconsideration after approved budget
   increases; the explicit callable hook alone is not a background
   service.
6. Establish reliable actual Fal billing ingestion and reconciliation
   of charges exceeding estimates or uncertain submission outcomes.
7. Exercise real Azure Table concurrency tests, failpoints, provider
   crash recovery and duplicate queue deliveries.
8. Update Docker and infrastructure manifests only after safety review.
9. Add API integration tests and project-scoped authorization tests.
10. Validate the full 108-decision requirements matrix against the
    authoritative V5 roadmap, which is not part of this branch.

## Enforced release policy

- Main remains protected by required `Backend tests (Python 3.12)`.
- No merge before independent review of the pending integration work.
- No Azure deployments, external Fal calls or paid spending were
  initiated by this change set.
- Treat green offline unit tests as *development verification* only.
