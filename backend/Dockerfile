FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY video_routes.py .
COPY job_admin_auth.py .
COPY job_management.py .
COPY job_admin_routes.py .
COPY storage.py .
COPY jobs.py .
COPY worker.py .
COPY job_queue.py .
COPY consumer.py .
COPY queue_lease.py .
COPY stale_jobs.py .
COPY worker_service.py .
COPY worker_cli.py .
COPY provider_config.py .
COPY video_provider.py .
COPY render_request.py .
COPY provider_errors.py .
COPY provider_execution.py .
COPY provider_credentials.py .
COPY provider_status.py .
COPY provider_cli.py .
COPY fal_provider.py .
COPY fal_config.py .
COPY fal_polling.py .
COPY video_download.py .
COPY video_delivery.py .
COPY fal_renderer.py .
COPY render_pipeline.py .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

COPY api_access.py .

COPY worker_retry.py .

COPY render_budget.py .

COPY operational_events.py .

COPY runtime_security.py .

COPY operations_cli.py .

# Bulk 01-03 project and budget orchestration modules.
# Project API remains disabled until ENABLE_PROJECT_ROUTES=true
# and a trusted OIDC issuer, audience, mapping table are configured.
COPY project_models.py project_access.py project_store.py project_security.py .
COPY project_oidc.py project_api_routes.py .
COPY finance_controls.py fal_budget_guard.py .
COPY financial_outbox.py safe_reconciliation.py budget_hold_events.py .
COPY job_scheduler.py held_jobs.py held_job_dispatch.py held_job_coordinator.py .
COPY provider_submission_ledger.py fal_safe_submission.py fal_safe_recovery.py .
COPY budgeted_fal_execution.py budgeted_worker_service.py .

COPY fal_billing_reconciliation.py .
