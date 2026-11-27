"""Optional create-operation contract through HTTP; atomic local storage fixture."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from unittest.mock import patch
from uuid import uuid4
import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError, ServiceResponseError
from fastapi.testclient import TestClient
from main import app
from project_access import Actor
import project_video_routes as routes

PID, UID, OP = (str(uuid4()) for _ in range(3))

class AtomicTable:
    def __init__(self):
        self.lock = Lock()
        self.member = True
        self.projects = {PID}
        self.members = {UID}
        self.rows = {}
        self.writes = 0
        self.lose_ack = False
        self.unreadable = False
        self.revoke_on_conflict = False
    def create_entity(self, *, entity):
        with self.lock:
            key = (entity['PartitionKey'], entity['RowKey'])
            if key in self.rows:
                if self.revoke_on_conflict: self.member = False
                raise ResourceExistsError('Fixture row exists')
            self.rows[key] = dict(entity)
            self.writes += 1
            if self.lose_ack: raise ServiceResponseError('Synthetic lost create acknowledgement')
    def get_entity(self, *, partition_key, row_key):
        if partition_key == 'projects' and row_key in self.projects:
            return {'RowKey':row_key,'name':'Fixture','ownerId':UID,'status':'active'}
        if partition_key.removeprefix('project-members:') in self.projects and row_key.removeprefix('member:') in self.members and self.member:
            return {'role':'member'}
        if partition_key == 'video' and self.unreadable:
            raise ServiceResponseError('Synthetic read unavailable')
        try: return dict(self.rows[(partition_key,row_key)])
        except KeyError: raise ResourceNotFoundError('Fixture absent') from None

@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv('ENABLE_AUTHENTICATED_VIDEO_JOBS','true')
    monkeypatch.setenv('ENABLE_PROJECT_ROUTES','true')
    table = AtomicTable()
    with patch.object(routes,'get_jobs_table_client',return_value=table), \
         patch.object(routes,'verify_project_token',return_value=Actor(UID,True)), \
         TestClient(app) as client:
        yield client,table

def post(client, **changes):
    return client.post(f'/projects/{PID}/jobs', json={'prompt':'  Fixture prompt  ','operation_id':OP,**changes},
                       headers={'Authorization':'Bearer synthetic'})

def test_concurrent_same_operation_creates_exactly_one_row(api):
    client,table=api
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses=list(pool.map(lambda _:post(client),range(16)))
    assert {r.status_code for r in responses} == {201}
    assert len({r.json()['job_id'] for r in responses}) == 1
    assert table.writes == len(table.rows) == 1
    row=next(iter(table.rows.values()))
    assert row['prompt']=='Fixture prompt'
    assert set(row)=={'PartitionKey','RowKey','prompt','status','createdAt','projectId','createdBy','dispatchQueueType','createOperationId'}

def test_committed_row_lost_ack_replays_without_second_creation(api):
    client,table=api
    table.lose_ack=True
    assert post(client).status_code==503
    assert table.writes==len(table.rows)==1
    assert next(iter(table.rows.values()))['status']=='created'
    result=post(client)
    assert result.status_code==201
    assert result.json()['job_id']==next(iter(table.rows.values()))['RowKey']
    assert table.writes==1

def test_same_key_different_content_conflicts_without_overwrite(api):
    client,table=api
    first=post(client)
    assert post(client,prompt='Different').status_code==409
    assert post(client).json()==first.json()
    assert table.writes==1

@pytest.mark.parametrize('revoke_on_conflict',[False,True])
def test_membership_revocation_denies_replay(api,revoke_on_conflict):
    client,table=api
    assert post(client).status_code==201
    table.revoke_on_conflict=revoke_on_conflict
    table.member=revoke_on_conflict
    assert post(client).status_code==403
    assert table.writes==1

def test_nonmember_cannot_create(api):
    client,table=api
    table.member=False
    assert post(client).status_code==403
    assert table.writes==0

def test_legacy_clients_keep_independent_random_creations(api):
    client,table=api
    headers={'Authorization':'Bearer synthetic'}
    results=[client.post(f'/projects/{PID}/jobs',json={'prompt':'Same'},headers=headers) for _ in range(2)]
    assert all(r.status_code==201 for r in results)
    assert results[0].json()['job_id']!=results[1].json()['job_id']
    assert all('createOperationId' not in row for row in table.rows.values())

def test_ambiguous_conflict_read_fails_closed(api):
    client,table=api
    assert post(client).status_code==201
    table.unreadable=True
    assert post(client).status_code==503
    assert table.writes==1

@pytest.mark.parametrize('field,value',[('createdBy',str(uuid4())),('projectId',str(uuid4())),
 ('createOperationId',None),('dispatchQueueType','other'),('status','unknown'),('createdAt',None)])
def test_mismatched_or_malformed_stored_result_is_not_replayed(api,field,value):
    client,table=api
    assert post(client).status_code==201
    next(iter(table.rows.values()))[field]=value
    assert post(client).status_code in (409,503)
    assert table.writes==1

def test_replay_reports_current_state_without_requeue(api):
    client,table=api
    assert post(client).status_code==201
    next(iter(table.rows.values()))['status']='completed'
    assert post(client).json()['status']=='completed'
    assert table.writes==1

@pytest.mark.parametrize('operation_id',['',OP.upper(),'not-an-id',7])
def test_invalid_operation_is_rejected(api,operation_id):
    client,table=api
    assert post(client,operation_id=operation_id).status_code==422
    assert table.writes==0


def test_operation_namespace_is_bound_to_verified_actor_and_project(api):
    client,table=api
    first=post(client).json()['job_id']
    other_user,other_project=str(uuid4()),str(uuid4())
    table.members.add(other_user)
    with patch.object(routes,'verify_project_token',return_value=Actor(other_user,True)):
        second=post(client).json()['job_id']
    table.projects.add(other_project)
    third=client.post(f'/projects/{other_project}/jobs',json={'prompt':'Fixture prompt','operation_id':OP},
                      headers={'Authorization':'Bearer synthetic'})
    assert third.status_code==201
    assert len({first,second,third.json()['job_id']})==3
    assert table.writes==3


def test_unauthenticated_or_other_project_request_never_replays(api):
    client,table=api
    assert post(client).status_code==201
    assert client.post(f'/projects/{PID}/jobs',json={'prompt':'Fixture prompt','operation_id':OP}).status_code==401
    assert client.post(f'/projects/{uuid4()}/jobs',json={'prompt':'Fixture prompt','operation_id':OP},
                       headers={'Authorization':'Bearer synthetic'}).status_code==403
    assert table.writes==1


def test_ambiguous_error_before_commit_does_not_invent_a_job(api):
    client,table=api
    with patch.object(table,'create_entity',side_effect=ServiceResponseError('Synthetic unavailable')):
        assert post(client).status_code==503
    assert table.writes==0
    assert post(client).status_code==201
    assert table.writes==1
