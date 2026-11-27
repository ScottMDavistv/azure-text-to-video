"""Revocation through the HTTP entry point with real project/membership lookup.

Identity verification and storage transport are fixtures; authorization is real.
No invitation, administrator provisioning, or directory deletion is performed.
"""
from unittest.mock import MagicMock, patch
import pytest
from azure.core.exceptions import ResourceNotFoundError
from fastapi.testclient import TestClient
from main import app
from project_access import Actor
import project_video_routes as routes

PID = '96d6a192-ab26-4c5f-9161-ea7d71c3291e'
OTHER = '3197e350-be68-48cc-9d5f-27c4a6dc5aa1'
JID = '7737c46d-9320-4c31-91ad-85c70893ae63'
UID = '74731bf0-ae16-4fc1-9972-eb2a9296c76c'

@pytest.mark.parametrize('endpoint,method', [
    ('', 'post'), (f'/{JID}', 'get'), (f'/{JID}/queue', 'post'),
    (f'/{JID}/video/metadata', 'get'), (f'/{JID}/video', 'get'),
])
def test_same_session_denied_after_exact_membership_removed(monkeypatch, endpoint, method):
    for name in ['ENABLE_AUTHENTICATED_VIDEO_JOBS', 'ENABLE_PROJECT_ROUTES',
                 'ENABLE_VIDEO_DOWNLOADS', 'ENABLE_SYNTHETIC_VIDEO_DISPATCH']:
        monkeypatch.setenv(name, 'true')
    monkeypatch.setenv('AZURE_SYNTHETIC_JOBS_QUEUE', 'textvideo-synthetic-only')
    monkeypatch.setenv('VIDEO_RENDER_PROVIDER', 'disabled')
    rows = {
        ('projects', PID): {'RowKey': PID, 'name': 'Fixture', 'ownerId': UID, 'status': 'active'},
        (f'project-members:{PID}', f'member:{UID}'): {'role': 'member'},
        (f'project-members:{OTHER}', f'member:{UID}'): {'role': 'owner'},
        ('video', JID): {'RowKey': JID, 'projectId': PID, 'createdBy': UID,
                        'status': 'completed', 'prompt': 'Test', 'createdAt': '2026-11-27T00:00:00Z'},
    }
    table = MagicMock()
    def get_entity(*, partition_key, row_key):
        try: return dict(rows[(partition_key, row_key)])
        except KeyError: raise ResourceNotFoundError('Fixture absent') from None
    table.get_entity.side_effect = get_entity
    with patch.object(routes, 'get_jobs_table_client', return_value=table), \
         patch.object(routes, 'verify_project_token', return_value=Actor(UID, True)), \
         patch.object(routes, 'get_video_resource') as blob, \
         patch('jobs.transition_job') as transition, TestClient(app) as client:
        headers = {'Authorization': 'Bearer same-synthetic-session'}
        # Establish that this exact identity was admitted before removal.
        assert client.get(f'/projects/{PID}/jobs/{JID}', headers=headers).status_code == 200
        del rows[(f'project-members:{PID}', f'member:{UID}')]
        response = client.request(method, f'/projects/{PID}/jobs{endpoint}',
                                  headers=headers, **({'json': {'prompt': 'Test'}} if not endpoint else {}))
        assert response.status_code == 403
        assert response.json() == {'detail': 'Project access denied'}
        # Ownership, other-project membership, and the prior session do not bypass removal.
        table.create_entity.assert_not_called()
        blob.assert_not_called()
        transition.assert_not_called()
