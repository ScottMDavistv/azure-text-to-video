# Video Render Tool — Copilot coding agent instructions

Follow the repository's single-owner security governance in `docs/single-owner-security-governance.md`.

## Scope and delivery

- Implement only the approved, unblocked GitHub issue you have been assigned.
- Prefer a small, short-lived branch and a single focused draft PR into `main`.
- Reference the issue, exact acceptance criteria, tests, and limitations in the PR.
- Preserve existing backend, security, Azurite and synthetic 300-second tests.
- If an issue depends on unfinished API or data contracts, stop and report the dependency. Do not invent an incompatible contract.
- Do not close issues or add owner acceptance markers.

## Security and financial invariants

- No Azure deployment or resource/IAM changes without separate owner authorization.
- Never call FAL or any paid video/AI provider; do not enable paid provider or worker flags.
- Do not weaken reservation-before-submit, provider receipt validation, financial outbox, deduplication, identity verification, audit trails or project budgets.
- Do not bypass required GitHub checks, auto-approve, self-merge, or push directly to protected `main`.
- Do not store API tokens, secrets, access tokens or payment credentials in code, logs, tests, comments or PRs.
- Changes involving finance, IAM, auth, security workflows, providers, paid rendering, deployment or policy require the owner decision gate. You may propose a draft PR, but not independently authorize execution or merge.

## Verification

- Tests must validate both success and failure paths; evidence must come from the current PR head.
- Use simulated, non-billable fixtures to test long-form video workflows.
- If blocked, state the decision needed, recommended option, risks and alternatives in the assigned issue and stop.
- Do not treat successful CI or a 300-second synthetic fixture as proof that real text-to-video generation works.
