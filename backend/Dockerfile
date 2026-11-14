FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
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

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
