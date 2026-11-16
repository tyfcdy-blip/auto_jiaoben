import io
import zipfile
import pytest
from workbench.config import Settings
from workbench.storage import safe_name
from workbench.security import Problem


def test_login_logout_and_secret_redaction(client, admin):
    me = client.get("/api/v1/auth/me", headers=admin)
    assert me.status_code == 200
    assert "password_hash" not in me.json()
    assert client.post("/api/v1/auth/logout", headers=admin).status_code == 200
    assert client.get("/api/v1/auth/me", headers=admin).status_code == 401


def test_login_rate_limit_and_no_password_echo(client):
    for _ in range(5):
        response = client.post("/api/v1/auth/login", json={"username": "test-admin", "password": "never-echo-secret"})
        assert response.status_code == 401
        assert "never-echo-secret" not in response.text
    assert client.post("/api/v1/auth/login", json={"username": "test-admin", "password": "test-password-123"}).status_code == 429


def test_project_isolation_and_viewer_cannot_write(client, admin, project):
    user = client.post("/api/v1/users", headers=admin, json={"username": "viewer", "display_name": "查看者", "password": "another-password-123"}).json()
    response = client.post("/api/v1/auth/login", json={"username": "viewer", "password": "another-password-123"})
    viewer = {"Authorization": "Bearer "+response.json()["token"]}
    assert client.get("/api/v1/projects", headers=viewer).json()["items"] == []
    body = {"name": "越权程序", "project_id": project["id"]}
    assert client.post("/api/v1/programs", headers=viewer, json=body).status_code == 404
    client.post(f'/api/v1/projects/{project["id"]}/members', headers=admin, json={"user_id": user["id"], "role": "viewer"})
    assert client.get("/api/v1/projects", headers=viewer).json()["items"][0]["role"] == "viewer"
    assert client.post("/api/v1/programs", headers=viewer, json=body).status_code == 403


def test_disabled_user_loses_existing_session(client, admin):
    user = client.post("/api/v1/users", headers=admin, json={"username": "temporary", "display_name": "测试", "password": "another-password-123"}).json()
    login = client.post("/api/v1/auth/login", json={"username": "temporary", "password": "another-password-123"}).json()
    client.patch('/api/v1/users/'+user["id"], headers=admin, json={"active": False})
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer "+login["token"]}).status_code == 401


def test_published_version_is_immutable(client, admin, registered):
    response = client.post('/api/v1/versions/'+registered["version"]["id"]+'/bundle', headers=admin, content=b"replacement")
    assert response.status_code == 409


def test_unsafe_bundle_rejected(client, admin, project):
    program = client.post('/api/v1/programs', headers=admin, json={"project_id": project["id"], "name": "包校验"}).json()
    version = client.post(f'/api/v1/programs/{program["id"]}/versions', headers=admin,
                          json={"name": "1", "command": ["python", "main.py"],
                                "parameter_schema": {"type": "object", "additionalProperties": False}}).json()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("main.py", "print(1)")
        archive.writestr("../escape.py", "print(2)")
    assert client.post(f'/api/v1/versions/{version["id"]}/bundle', headers=admin, content=buffer.getvalue()).status_code == 422


@pytest.mark.parametrize("filename", ["../a", "C:\\secret", "CON", "file.", "a/b", "test\x00"])
def test_unsafe_names(filename):
    with pytest.raises(Problem):
        safe_name(filename)


def test_strict_parameters_and_reserved_commands(client, admin, registered):
    task = registered["task"]
    path = f'/api/v1/tasks/{task["id"]}/runs'
    assert client.post(path, headers=admin, json={"event_id": "invalid", "parameters": {"command": "powershell"}}).status_code == 422
    assert client.post(path, headers=admin, json={"event_id": "invalid", "parameters": {"unknown": 1}}).status_code == 422
    assert client.post(path, headers=admin, json={"event_id": "invalid", "node_id": "x"}).status_code == 422


def test_node_token_cannot_use_management_api(client, admin, project):
    node = client.post('/api/v1/nodes', headers=admin, json={"project_id": project["id"], "name": "测试节点"}).json()
    assert "token" in node
    assert "token" not in client.get('/api/v1/nodes', headers=admin).json()["items"][0]
    response = client.post('/api/v1/projects', headers={"Authorization": "Bearer "+node["token"]}, json={"name": "非法项目"})
    assert response.status_code == 403


def test_production_requires_postgresql_and_secrets():
    with pytest.raises(ValueError):
        Settings(env="production", database_url="sqlite:///bad.db")
    with pytest.raises(ValueError):
        Settings(allow_side_effects=True)


def test_openapi_is_real_schema(client):
    document = client.get('/api/openapi.json').json()
    assert '/api/v1/auth/login' in document['paths']
    assert document['components']['schemas']['RunCreate']['additionalProperties'] is False


def test_password_whitespace_is_not_silently_removed(client, admin):
    password = '  synthetic-password-spaces  '
    assert client.post('/api/v1/users', headers=admin, json={
        'username': 'whitespace', 'display_name': '空格密码测试', 'password': password}).status_code == 201
    assert client.post('/api/v1/auth/login', json={'username': 'whitespace', 'password': password}).status_code == 200
    assert client.post('/api/v1/auth/login', json={'username': 'whitespace', 'password': password.strip()}).status_code == 401


def test_bad_secret_configuration_does_not_echo_value():
    secret = 'SYNTHETIC_INVALID_KEY'
    with pytest.raises(ValueError) as error:
        Settings(secret_key=secret)
    assert secret not in str(error.value)


def test_project_choices_paginate_without_repeating_first_page(client, admin, app):
    from workbench.models import Project
    with app.state.factory.begin() as db:
        for number in range(205):
            db.add(Project(name=f'synthetic-pagination-{number}', created_at=float(number)))
    first = client.get('/api/v1/projects?limit=200&offset=0', headers=admin).json()
    second = client.get('/api/v1/projects?limit=200&offset=200', headers=admin).json()
    assert first['total'] == second['total'] == 205
    assert len(first['items']) == 200 and len(second['items']) == 5
    assert not {item['id'] for item in first['items']} & {item['id'] for item in second['items']}
    assert all(item['role'] == 'project_admin' for item in second['items'])
