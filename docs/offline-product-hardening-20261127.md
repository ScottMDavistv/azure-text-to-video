# Offline product hardening review

This candidate starts at `1a4c25c93562572958d760abc0b26799c966cf52` and changes no workflow, deployment, identity provisioning or live feature flag. It does not approve public exposure or a hosted run.

The member status response now uses the fixed `RenderFailed` category for a failed job. Administrative request/conflict errors also use fixed public messages. Detailed exceptions remain outside those response contracts.

The browser client limits each request to 30 seconds by default, covering token acquisition, fetch and body parsing. The injectable deadline must be 1–120,000 milliseconds. Timeout means the outcome can be unknown, especially for create/queue: the client does not retry a mutation automatically. Status responses must match the requested job. MP4 bodies are counted incrementally against authorized metadata before a Blob is constructed; declared Content-Length alone does not bound allocation.

Automatic status observation pauses after ten minutes or three consecutive errors. A successful observation resets the error streak. These are client observation limits, not a render deadline, failure classification or production SLA. Explicit **Resume status checks** resumes reads of the same job. Existing authorization/not-found terminal stops remain terminal. Duplicate actions in the same render tick are refused synchronously. Validation errors receive focus and prompt limits are associated with the input.

Revocation regressions use the HTTP entry point and real membership lookup with an in-memory storage fixture: removing exactly the member row denies create, status, queue, metadata and bytes for the same session, even when project ownership and another project's membership remain. They do not implement a directory offboarding policy or invitation/admin flow.

Manual stale recovery now requires literal `True`, a canonical job UUID and an integer threshold of 1–10,080 minutes before storage access. The existing ETag update remains. A boolean confirmation is an operator attestation, not independent evidence that the worker stopped.

The private review package contains the full requirements matrix, exact Git bundle and patch, test commands, results and limitations. Hosted acceptance, production admin/MFA policy, Entra invitations, private storage routing and permissions, registry delivery, spend approval and cleanup remain separate gates. No mocked identity or browser fixture establishes real tenant, Azure or hosted behavior.

## Second offline pass

Worker claims, state transitions and manual stale recovery now refuse missing, blank, padded or wildcard concurrency ETags before writing. A specific observed storage version remains mandatory; this does not introduce automatic recovery or establish live distributed correctness.

The studio recognizes the existing administrative `cancelled` status as terminal, stops polling it, and offers no queue/video actions for that job. No new cancellation authority or endpoint is introduced.

Creation has no existing server idempotency key or member lookup route for a lost server-generated job ID. After a dispatched create has an unknown outcome (including timeout, transport failure, malformed response or server unavailability), the client marks that project uncertain and blocks another creation in the current mounted session. An explicit refusal before creation is treated separately. The last confirmed job is retained and clearly distinguished from the uncertain request. The hold survives project switching within that session, including aborting a dispatched request during a switch. The owner is directed to authorized operator reconciliation, not to blindly retry or label the job failed. This safeguard is in memory only: reload, logout, another tab or another device are not protected by server-side deduplication. Durable recovery/idempotency requires a separately reviewed server contract and concurrent/ambiguous-commit tests; no broad persistence was added here.


## Final session-only Studio integration

The subsequent optional server operation-ID contract is now used by the Studio. One explicit same-account/project retry reuses the immutable submitted prompt and operation ID held in memory. Recovery restores the known job without automatic queueing; an explicit queue action only targets that recovered ID. Previous sections describe the earlier checkpoints. See `create-operation-replay.md` for the current contract and reload/device limitations.
