# Single-owner security governance — Video Render Tool

Status: adopted for **development-only code review**. This replaces the earlier
requirement for a second human *code reviewer*, not the separate two-distinct-
administrator approval policy for spending changes.

## Boundaries

- **Ordinary backend**: Owner may merge a pull request after the exact-head
  backend and security checks pass, GitHub rules are satisfied, and any reported
  findings have been resolved. A PR is required; direct pushes to main are not
  the approved path.
- **Security-sensitive backend** (authentication, authorization, finance,
  credentials, rendering providers, job charging, deployment and policy):
  require security workflow checks plus a documented, manual owner risk decision
  identifying the exact head SHA, potential failure modes, negative-path test
  evidence, unresolved findings and rollback strategy. AI/static checks do **not**
  constitute independent human review; mark that limitation explicitly.
- **Critical/high unresolved security findings**: do not merge. Remediate and
  rerun exact-head checks, or obtain an explicitly recorded owner exception
  *without weakening mandatory status checks*. No exceptions are authorized by
  this document.
- **Production/Azure deployment**: never implied by a code merge. Separate,
  explicit owner authorization and deployment readiness evidence required.
- **Paid FAL**: stays disabled by default. No paid provider activation or
  rendering requests without separate owner authorization, trusted provider
  charge/receipt verification, proven reservation-before-submit financial gate,
  Azure identity controls and release checks.
- **Two-administrator spending approvals** remain enforced. A one-person owner
  cannot impersonate two separate administrators. Requests requiring both are
  blocked until a separately authorized and implemented financial policy change.

## Automated independent checks

1. Existing required backend suite, container startup, concurrency and Azurite
   financial/regression tests must pass on the exact PR head.
2. Dedicated security workflow runs static analysis (Bandit), package
   vulnerability audit (pip-audit), security invariants/secret-pattern checks,
   and no-paid-execution flags. Automated checks are distinct analysis methods,
   **not** an independent human or full penetration test.
3. Review the GitHub Actions output including scanner versions and errors. An
   error in a security scanner is **not** an automatic clean verdict.
4. For changed high-risk files, manually inspect entrypoints, caller privilege,
   trust boundaries, concurrency, duplicate requests, failures and rollback.
5. Re-run mandatory checks whenever the head changes.

## Owner security review record (attach to high-risk PR)

- **Commit SHA:** exact PR head
- **Change classification:** ordinary / security-sensitive
- **Threats/abuse paths considered:** concrete list
- **Negative-path tests and CI URLs:** concrete proof
- **Static/dependency/secret scan results:** evidence, not assumed
- **Known limitations:** e.g., unverified provider receipts or IAM
- **Residual risk:** accepted or rejected, with reason
- **Rollback/disable plan:** tested and actionable
- **Owner decision:** approve development-only merge or block
- **Azure deployment:** not authorized unless separately recorded
- **Paid FAL activation:** not authorized unless separately recorded

## GitHub administration follow-up

Existing repository ruleset `Protect main - Backend Tests` requires a PR and
`Backend tests (Python 3.12)`, with zero required approving reviews. Add the
new `Security checks (Python 3.12)` as another **required** status context in
the main-branch ruleset after validating its initial run. The GitHub connector
does not expose a ruleset admin write operation, so creation of this workflow
alone does not enforce it as a mandatory merge gate.

Do not remove existing required checks or bypass branch protection. Review
the workflow permissions, alerts and dependency policy periodically.
