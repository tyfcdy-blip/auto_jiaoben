import io
import zipfile
import json
import os
import uuid
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet
from workbench.config import Settings
from workbench.db import initialize
from workbench.main import create_app
from workbench.models import User
from workbench.security import hasher


@pytest.fixture
def app(tmp_path):
    database_url = "sqlite:///"+str(tmp_path/"test.db")
    connection_file = os.environ.get("WORKBENCH_TEST_DATABASE_FILE")
    schema, maintenance = None, None
    if connection_file:
        database_url = json.loads(Path(connection_file).read_text(encoding="utf-8"))["database_url"]
        maintenance = create_engine(database_url)
        schema = "test_"+uuid.uuid4().hex
        with maintenance.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        database_url = make_url(database_url).update_query_dict({"options": "-csearch_path="+schema}).render_as_string(hide_password=False)
    settings = Settings(env="test", database_url=database_url,
                        data_dir=tmp_path/"storage", secret_key=Fernet.generate_key().decode())
    application = create_app(settings)
    initialize(application.state.engine, application.state.factory)
    with application.state.factory.begin() as db:
        db.add(User(username="test-admin", display_name="测试管理员", password_hash=hasher.hash("test-password-123"), is_admin=True))
    yield application
    application.state.engine.dispose()
    if maintenance:
        with maintenance.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        maintenance.dispose()


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def admin(client):
    response = client.post("/api/v1/auth/login", json={"username": "test-admin", "password": "test-password-123"})
    assert response.status_code == 200
    return {"Authorization": "Bearer "+response.json()["token"]}


@pytest.fixture
def project(client, admin):
    response = client.post("/api/v1/projects", headers=admin, json={"name": "隔离测试项目"})
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def registered(client, admin, project):
    program = client.post("/api/v1/programs", headers=admin, json={"project_id": project["id"], "name": "合成程序"}).json()
    version = client.post(f'/api/v1/programs/{program["id"]}/versions', headers=admin,
                          json={"name": "test-v1", "command": ["python", "main.py"], "result_required": False,
                                "parameter_schema": {"type": "object", "properties": {"period": {"type": "string"}}, "additionalProperties": False}}).json()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("main.py", "print('synthetic test')")
    response = client.post(f'/api/v1/versions/{version["id"]}/bundle', headers=admin, content=buffer.getvalue())
    assert response.status_code == 200
    assert client.post(f'/api/v1/versions/{version["id"]}/publish', headers=admin).status_code == 200
    task = client.post("/api/v1/tasks", headers=admin, json={"project_id": project["id"], "name": "测试任务", "version_id": version["id"]}).json()
    return {"program": program, "version": version, "task": task}
