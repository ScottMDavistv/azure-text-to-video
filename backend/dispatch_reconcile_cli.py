"""Explicit one-shot dispatch reconciler for an operator-controlled schedule.

There is NO daemon, deployed schedule or paid renderer in this module.
A scheduled Container Apps Job can invoke --run-once after separately approved
deployment and managed-identity permissions.
"""
import argparse
import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone

from dispatch_recovery import reconcile_queued_jobs
from stage_a_runtime import validate_safe_defaults

LOG = logging.getLogger("textvideo.dispatch_recovery")
MAX_PAGES = 100
MAX_BATCH = 50


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def summary_log(summary, logger=None):
    """Aggregate-only telemetry: never log job prompts, secrets or IDs."""
    logger = logger or LOG
    payload = {"event": "dispatch_reconciliation",
               "timestamp": _utc_now(),
               "inspected": summary["inspected"],
               "pages": summary["pages"],
               "sent": summary["sent"],
               "held": summary["held"],
               "uncertain": summary["uncertain"],
               "already_dispatched": summary["already_dispatched"],
               "state_advanced": summary["state_advanced"],
               "incomplete": summary["incomplete"],
               "alert": summary["alert"]}
    logger.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if summary["alert"]:
        logger.error("dispatch_reconciliation_requires_attention")
    return payload


def run_once(*, batch_size=25, max_pages=20, reconcile=None, logger=None):
    """Visit bounded batches, including cursor pagination, once per invocation.

    This routine sends only *queue notifications*, never starts a worker.
    Run in a single operational invocation; per-job conditional leases
    protect concurrent invocations. Return incomplete=true if scan capped.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH:
        raise ValueError("batch_size must be 1..50")
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
        raise ValueError("max_pages must be 1..100")
    reconcile = reconcile or reconcile_queued_jobs
    totals = Counter()
    cursor = None
    seen = set()
    pages = 0
    for _ in range(max_pages):
        batch = reconcile(limit=batch_size, cursor=cursor)
        pages += 1
        if type(batch.get("inspected")) is not int or not 0 <= batch["inspected"] <= batch_size:
            raise RuntimeError("Invalid recovery batch")
        results = batch.get("results")
        if not isinstance(results, list) or len(results) != batch["inspected"]:
            raise RuntimeError("Invalid recovery batch")
        for item in results:
            outcome = item.get("outcome")
            if outcome not in {"sent", "held", "uncertain",
                               "already_dispatched", "state_advanced"}:
                raise RuntimeError("Unknown reconciliation outcome")
            totals[outcome] += 1
        totals["inspected"] += batch["inspected"]
        next_cursor = batch.get("next_cursor")
        if next_cursor is None:
            cursor = None
            break
        if not isinstance(next_cursor, str) or next_cursor in seen:
            raise RuntimeError("Recovery pagination did not advance")
        seen.add(next_cursor)
        cursor = next_cursor
    summary = {
        "inspected": totals["inspected"], "pages": pages,
        "sent": totals["sent"], "held": totals["held"],
        "uncertain": totals["uncertain"],
        "already_dispatched": totals["already_dispatched"],
        "state_advanced": totals["state_advanced"],
        "incomplete": cursor is not None,
    }
    # Any uncertain state or incomplete sweep warrants operator review.
    summary["alert"] = summary["uncertain"] > 0 or summary["incomplete"]
    summary_log(summary, logger=logger)
    return summary


def run_cli(argv=None):
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true",
                      help="offline safety preflight only")
    mode.add_argument("--run-once", action="store_true",
                      help="opt-in queued dispatch reconciliation")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--max-pages", type=int, default=20)
    args = parser.parse_args(argv)

    # Fail closed before establishing any Azure client.
    validate_safe_defaults()
    if args.check:
        print(json.dumps({"mode": "dispatch_reconciliation_dry_run",
                          "paid_fal": False, "azure_calls": False}))
        return 0
    if os.getenv("ENABLE_DISPATCH_RECONCILIATION", "").lower() != "true":
        parser.error("live queue reconciliation requires explicit enable flag")
    logging.basicConfig(level=logging.INFO)
    result = run_once(batch_size=args.batch_size, max_pages=args.max_pages)
    return 2 if result["alert"] else 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
