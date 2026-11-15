"""Read-only paid-job recovery scan and evidence-gated finalization.

No FAL POST, polling or billing ingestion. Existing settled financial
records may be finalized using the idempotent conditional job updater.
Ambiguous provider/financial states remain for manual reconciliation.
"""

from finance_controls import check_id, load, FinanceDenied
from provider_submission_ledger import read_intent
from paid_job_finalization import classify_paid_job_recovery, finalize_paid_job


class RecoveryBlocked(RuntimeError):
    pass


def inspect_paid_job(*, job_id, jobs_table, finance_table,
                     submission_table, allow_finalize=False):
    check_id(job_id)
    job = jobs_table.get_entity(partition_key="video", row_key=job_id)
    try:
        reservation = load(finance_table, "reservation:" + job_id)
    except FinanceDenied:
        reservation = None
    try:
        intent = read_intent(submission_table, job_id)
    except Exception:
        intent = None
    state = classify_paid_job_recovery(job, intent, reservation)
    if state == "ready_to_finalize" and allow_finalize is True:
        result = finalize_paid_job(
            job_id=job_id, jobs_table=jobs_table, finance_table=finance_table,
            submission_table=submission_table,
        )
        return {"recovery": state, "finalization": result}
    return {"recovery": state, "finalization": None}


def inspect_paid_jobs(*, job_ids, jobs_table, finance_table, submission_table,
                      allow_finalize=False):
    if not isinstance(job_ids, (list, tuple)) or len(job_ids) > 100:
        raise RecoveryBlocked("Trusted bounded job IDs required")
    for job_id in job_ids:
        check_id(job_id)
    results = []
    for job_id in job_ids:
        results.append(inspect_paid_job(
            job_id=job_id, jobs_table=jobs_table,
            finance_table=finance_table, submission_table=submission_table,
            allow_finalize=allow_finalize,
        ))
    return results
