# Hybrid multi-agent coordination — Video Render Tool

Status: **draft PR #17**. This is a task routing and evidence system, not an autonomous coding runner.

## Roles

- **Design Management** owns the approved-scope backlog and orders work by priority and dependency.
- **Frontend**, **Backend**, **Infrastructure**, and **Integration** agents each read their issue queue.
- A ChatGPT development chat or external coding agent is **not** automatically invoked by GitHub Issues. The owner starts its session and provides the GitHub assignment, or separately configures an authorized runner.
- The coordinator never starts coding agents or authorizes merges.

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
- No frontend or backend agents are invoked automatically; that would require a separate runner and authorization.
- GitHub permissions still govern who can modify the task manifest and issue bodies; the single-owner process must review changes to this manifest before merge.
- The queue's prioritization is deterministic (P0, P1, P2), not an AI architectural assessment. ChatGPT can interpret the report on demand without using a paid API.
- A task can be accepted only by explicit owner action; the coordinator does not independently establish provenance of a marker.
