"""Opt-in loopback-only storage proof for the optional create operation contract."""
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4
import pytest
from azure.core.exceptions import ServiceResponseError
import project_video_routes as routes
from project_models import Project, ProjectRole
from project_store import create_project, add_membership
from project_security import require_project_role
from test_azurite_synthetic_journey import journey

pytestmark = pytest.mark.skipif(os.getenv('AZURITE_SYNTHETIC_TEST') != '1', reason='Opt-in local Azurite only')

def setup_membership(client, base, table, monkeypatch):
    pid=base.split('/')[2]
    actor=routes.verify_project_token('offline-fixture',table)
    create_project(table,Project(pid,'Offline replay fixture',actor.user_id))
    add_membership(table,pid,actor.user_id,ProjectRole.MEMBER)
    monkeypatch.setattr(routes,'require_project_role',require_project_role)
    return pid,actor.user_id

def test_real_conditional_create_concurrent_replay_and_revocation(journey,monkeypatch):
    client,base,table,queue,container=journey
    pid,uid=setup_membership(client,base,table,monkeypatch)
    payload={'prompt':'Offline replay fixture','operation_id':str(uuid4())}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:client.post(base,json=payload),range(16)))
    assert {r.status_code for r in results}=={201}
    assert len({r.json()['job_id'] for r in results})==1
    rows=list(table.query_entities("PartitionKey eq 'video'"))
    assert len(rows)==1 and rows[0]['prompt']==payload['prompt']
    assert client.post(base,json={**payload,'prompt':'Different'}).status_code==409
    table.delete_entity(partition_key=f'project-members:{pid}',row_key=f'member:{uid}')
    assert client.post(base,json=payload).status_code==403
    assert len(list(table.query_entities("PartitionKey eq 'video'")))==1
    assert not list(queue.receive_messages(max_messages=1))

def test_real_committed_row_with_lost_response_can_be_replayed(journey,monkeypatch):
    client,base,table,queue,container=journey
    setup_membership(client,base,table,monkeypatch)
    payload={'prompt':'Lost response fixture','operation_id':str(uuid4())}
    class LoseAck:
        def get_entity(self,**kwargs): return table.get_entity(**kwargs)
        def create_entity(self,**kwargs):
            table.create_entity(**kwargs)  # real commit precedes synthetic lost ACK
            raise ServiceResponseError('Synthetic withheld acknowledgement')
    with patch.object(routes,'get_jobs_table_client',return_value=LoseAck()):
        assert client.post(base,json=payload).status_code==503
        rows=list(table.query_entities("PartitionKey eq 'video'"))
        assert len(rows)==1 and rows[0]['status']=='created'
        result=client.post(base,json=payload)
        assert result.status_code==201 and result.json()['job_id']==rows[0]['RowKey']
    assert len(list(table.query_entities("PartitionKey eq 'video'")))==1
    assert not list(queue.receive_messages(max_messages=1))
