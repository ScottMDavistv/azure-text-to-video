import os

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.data.tables import TableServiceClient


STORAGE_ACCOUNT = os.getenv("AZURE_STORAGE_ACCOUNT", "sttextvideo1794671698")
BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER", "videos")
JOBS_TABLE = os.getenv("AZURE_JOBS_TABLE", "VideoJobs")


def get_credential():
    client_id = os.getenv("AZURE_CLIENT_ID")
    return DefaultAzureCredential(
        managed_identity_client_id=client_id
    )


def get_blob_container_client():
    service = BlobServiceClient(
        account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
        credential=get_credential(),
    )
    return service.get_container_client(BLOB_CONTAINER)


def get_jobs_table_client():
    service = TableServiceClient(
        endpoint=f"https://{STORAGE_ACCOUNT}.table.core.windows.net",
        credential=get_credential(),
    )
    return service.get_table_client(JOBS_TABLE)


def upload_rendered_video(job_id, content):
    from uuid import UUID
    from azure.storage.blob import ContentSettings

    validated_id = str(UUID(str(job_id)))
    blob_name = f"renders/{validated_id}.mp4"

    blob = get_blob_container_client().get_blob_client(blob_name)

    blob.upload_blob(
        content,
        overwrite=False,
        content_settings=ContentSettings(
            content_type="video/mp4"
        ),
    )

    return blob_name
