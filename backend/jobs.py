from azure.data.tables import UpdateMode
from storage import get_jobs_table_client

VALID_TRANSITIONS = {
    "created": {"queued"},
    "queued": {"processing"},
    "processing": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
}


def transition_job(job_id, next_status, **fields):
    table = get_jobs_table_client()

    entity = table.get_entity(
        partition_key="video",
        row_key=str(job_id),
    )

    current_status = entity["status"]

    if next_status not in VALID_TRANSITIONS.get(current_status, set()):
        raise ValueError(
            f"Cannot transition from {current_status} to {next_status}"
        )

    update = {
        "PartitionKey": "video",
        "RowKey": str(job_id),
        "status": next_status,
        **fields,
    }

    table.update_entity(
        entity=update,
        mode=UpdateMode.MERGE,
    )

    return update
