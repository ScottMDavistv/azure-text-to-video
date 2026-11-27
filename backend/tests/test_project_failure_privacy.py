"""Public route regression: stored diagnostics never enter the member DTO."""
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient
from main import app
from project_access import Actor
import project_video_routes as routes

PID = '96d6a192-ab26-4c5f-9161-ea7d71c3291e'
JID = '7737c46d-9320-4c31-91ad-85c70893ae63'
UID = '74731bf0-ae16-4fc1-9972-eb2a9296c76c'

@pytest.mark.parametrize('diagnostic', [
    'private-token-and-blob-url', {'private': 'secret'}, ['secret'], None,
    'ProviderTimeoutError',
])
def test_status_route_has_fixed_failure_category(monkeypatch, diagnostic):
    monkeypatch.setenv('ENABLE_AUTHENTICATED_VIDEO_JOBS', 'true')
    monkeypatch.setenv('ENABLE_PROJECT_ROUTES', 'true')
    table = MagicMock()
    table.get_entity.return_value = {
        'RowKey': JID, 'status': 'failed', 'prompt': 'Test',
        'createdAt': '2026-11-27T00:00:00Z', 'createdBy': UID,
        'projectId': PID, 'errorType': diagnostic, 'error': 'private-detail',
    }
    with patch.object(routes, 'get_jobs_table_client', return_value=table), \
         patch.object(routes, 'verify_project_token', return_value=Actor(UID, True)), \
         patch.object(routes, 'require_project_role'), TestClient(app) as client:
        result = client.get(f'/projects/{PID}/jobs/{JID}',
                            headers={'Authorization': 'Bearer synthetic'})
    assert result.status_code == 200
    assert result.json() == {'job_id': JID, 'status': 'failed', 'prompt': 'Test',
                             'created_at': '2026-11-27T00:00:00Z',
                             'error_type': 'RenderFailed'}
