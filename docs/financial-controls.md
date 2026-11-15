# Bulk 02 — Financial Controls

Status: Development foundation. Not deployed.

## Included

- Integer-cent financial accounting
- Project-level and global FAL budget ceilings
- Atomic multi-record reservation operations
- ETag-based concurrent modification protection
- Reservation settlement
- Budget increase requests
- Two distinct administrator approval checks
- Persistent financial audit events
- Offline tests

## Critical integration requirements

These modules are NOT wired to rendering endpoints.

Before production:
1. Integrate trusted user identity and membership.
2. Establish secure administrative provisioning.
3. Create an authorized project budget.
4. Provision the global FAL spending ceiling.
5. Reserve before provider submission.
6. Verify provider identity and request cost.
7. Reconcile actual provider charges.
8. Preserve uncertain requests without releasing funds.
9. Add authorization and recovery integration tests.
10. Validate Azure Table transactional behavior.

## Limitations

- One partition provides atomicity but may create
  a throughput bottleneck at high job volume.
- ETag and transaction behavior requires Azure tests.
- Reservation settlement requires verified billing data.
- No automated provider billing reconciliation exists.
- No temporary spending-limit expiration exists yet.
- No notification delivery is implemented yet.
- No financial administration UI exists yet.
- No integration with the FAL worker exists yet.
- The administrator lookup must come from a trusted
  identity service, not client-supplied values.
- No audit log immutability guarantees are established.
- No live budget enforcement is active.

## Safety

No Azure resources are created.
No live FAL requests are made.
No infrastructure or application deployment occurs.
The main branch is unchanged.
