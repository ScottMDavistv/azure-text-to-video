# Hybrid multi-agent coordination — Video Render Tool

Status: **merged on main via PR #17**. Design Management is the sole source of approved workstream priorities and dependencies. PR #19 added a separate *opt-in* execution controller; code in the repository is not evidence that dispatch is activated.

## Roles

- **Design Management** owns the approved-scope backlog and orders work by priority and dependency.
- **Frontend**, **Backend**, **Infrastructure**, and **Integration** agents each read their issue queue.
- A ChatGPT development chat or external coding agent is **not** automatically invoked by GitHub Issues. The owner starts its session and provides the GitHub assignment, or separately configures an authorized runner.
- The Design Management coordinator owns the single approved task manifest; its trusted, separately gated execution controller can assign Copilot only after owner authorization and GitHub activation. Neither component authorizes merges or owner acceptance.

## Source of truth

`docs/design-management-tasks.json` stores five initial approved-development tasks. Only tasks with `approval: approved-development`, `sensitive: false`, and a valid agent, ID, priority, dependencies and acceptance criteria are eligible for automatic issue creation.

These declarations represent the owner's **approved development task scope**, not authorization for deployment, paid providers, financial policy, security credentials, or merge.

The scheduled default-branch audit creates one GitHub issue per ready approved task, using stable `<!-- design-agent-task:<id> -->` markers. It checks both open and closed issues before creating a new one; API/pagination errors fail closed. Dependent tasks stay held until all prerequisites are *explicitly* owner accepted.

## Completing a dependency

1. A development agent links a draft PR in its issue. Tests must pass on the PR's current head.
2. The owner follows existing single-owner security governance for the actual code review. This coordinator does not merge or approve PRs.
3. After a separately authorized merge, the coordinator can identify linked PRs and inspect required GitHub workflow results for the exact merge commit SHA. CI success is **only** a candidate for review, not proof of task acceptance.
4. The owner independently verifies the issue's acceptance criteria and records `<!-- design-agent-accepted:<id> -->` in that issue's body, then closes it with reason **completed**. A closed issue without this marker does **not** unblock dependents.
5. The next scheduled audit creates newly eligible downstream issues. The audit never places an acceptance marker or closes issues.

## Agent work queues

Dashboard: GitHub issue #16; section `Development agent work queues`.

Statuses:
- `unpublished`: no issue yet
- `ready`: issue open and dependencies accepted
- `blocked`: issue open and prerequisites not accepted
- `accepted-recorded`: owner marker and GitHub completed state
- `closed-unverified`: closed without acceptance marker

GitHub artifact `design-management-queues.json` contains the same queues and completion candidates.

## ChatGPT development-agent instructions

In each development chat:

> Read the current work queue in GitHub issue #16 and any linked task issues marked for your assigned area. Take the highest-priority ready task. Respect dependencies, acceptance criteria, and single-owner security governance. Implement on a development branch, run appropriate tests, open a draft PR, and link evidence to the issue. Do not merge, deploy to Azure, change financial/identity controls, enable paid providers, or incur paid API usage without separate authorization. Do not mark an issue accepted or completed.

These instructions guide each separately started ChatGPT chat, but do not create automatic cross-chat communication.

## Governance / limitations

- Task creation is the sole additional GitHub write operation; no PR comments, PR approvals, merges, Azure writes or payments.
- Existing single-owner safeguards and mandatory security checks remain unchanged.
- No coding agent is invoked by the audit alone. Automatic assignment requires the separately gated GitHub Actions controller, explicit owner authorization, a working scoped Copilot token, and enabled GitHub Actions variables; it is never inferred from a merged workflow.
- GitHub permissions still govern who can modify the task manifest and issue bodies; the single-owner process must review changes to this manifest before merge.
- The queue's prioritization is deterministic (P0, P1, P2), not an AI architectural assessment. ChatGPT can interpret the report on demand without using a paid API.
- A task can be accepted only by explicit owner action; the coordinator does not independently establish provenance of a marker.

## Central-controller policy — owner decision 2026-11-16

- **Single authoritative planner:** Design Management reads `docs/design-management-tasks.json` and publishes issue #16. Backend, frontend, infrastructure and integration are queues, **not independent policy controllers**.
- **Execution mechanism:** The existing opt-in `autonomous-engineering.yml` may assign eligible GitHub Copilot tasks only after all runtime toggles and credentials have been verified. The planner remains authoritative and dispatch never bypasses accepted dependencies.
- **Two-agent maximum:** Count issues assigned to the observed GitHub `Copilot` bot (and historical `copilot-swe-agent` aliases) and open PRs authored by or assigned to a recognized Copilot bot; fail conservatively if an issue and linked PR are both open. Never dispatch an already-assigned task. Do not assign milestones as coding tasks. One workstream cannot silently authorize another's security or deployment changes.
- **Included credits only:** No paid overages. Verify Copilot account usage/overage restrictions externally before enabling dispatch; the controller does not have a quota API and cannot guarantee credit availability.
- **Synthetic-generation priority:** Use `dispatch_order` (lower first) within priority class. In order: API contract verification, synthetic scene/job orchestration, frontend prompt-to-download, full 300s integration; Infrastructure Reader-only reviews remain P1 and never auto-dispatched. Work already in a PR stays under review; do not spawn overlapping tasks.
- **Human gates:** Signed-off acceptance marker and closed-completed GitHub task prerequisite, CI on exact head, owner decisions for security/auth/finance/deployment; do not automatically merge sensitive PRs. Keep GitHub Copilot workflow approvals and protected main, Azure write RBAC unassigned, paid FAL off.
- **Proof of activation:** A real main-branch Design Management audit must publish a timestamped issue #16 report; a real controller dispatch must record an eligible, newly assigned task without duplicate assignment or paid service use. A skipped workflow or a dry-run is not proof of activation.
- **Status:** Owner has authorized controlled assignment and synthetic prioritization; actual unattended dispatch remains blocked until repository variables, scoped token, included-credit controls and main-branch workflow verification are demonstrated.
