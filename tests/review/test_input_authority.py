"""动态输入的独立反向审查：真实事务、真实权限、合成文件。"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from workbench.db import lock
from workbench.models import BatchFile, Caller, LoginSession, Node, Run, Schedule, User
from workbench.routes_catalog import _store_input
from workbench.scheduling import scan_health, scan_schedules
from workbench.security import Identity, Problem, digest, write_lock


def create_delivery_caller(client, admin, project, registered):
    response = client.post("/api/v1/callers", headers=admin, json={
        "project_id": project["id"], "name": "独立审查交付方", "task_ids": [registered["task"]["id"]],
        "allow_input_upload": True})
    assert response.status_code == 201
    return response.json()


def test_revoked_caller_cannot_finish_already_authenticated_upload(app, client, admin, project, registered):
    caller = create_delivery_caller(client, admin, project, registered)
    batch = client.post("/api/v1/batches", headers=admin, json={"project_id": project["id"], "label": "撤权窗口合成输入"}).json()
    # 模拟已完成Bearer校验、还在接收body的请求，保持真实SQLAlchemy身份快照。
    with app.state.factory.begin() as uploading:
        stale_identity = Identity("caller", uploading.get(Caller, caller["id"]))
        assert stale_identity.record.active is True
        response = client.patch(f"/api/v1/callers/{caller['id']}", headers=admin, json={"active": False})
        assert response.status_code == 200
        with pytest.raises(Problem) as rejected:
            _store_input(batch["id"], "source.csv", b"value\n1", app.state.settings, stale_identity, uploading)
        assert rejected.value.status in {401, 403}
    with app.state.factory() as observer:
        assert observer.scalar(select(BatchFile).where(BatchFile.batch_id == batch["id"])) is None


def test_upload_caller_cannot_modify_other_project_batch(client, admin, project, registered):
    caller = create_delivery_caller(client, admin, project, registered)
    headers = {"Authorization": "Bearer " + caller["token"]}
    other = client.post("/api/v1/projects", headers=admin, json={"name": "独立审查另一个项目"}).json()
    batch = client.post("/api/v1/batches", headers=admin, json={"project_id": other["id"], "label": "禁止跨项目修改"}).json()
    assert client.put(f"/api/v1/batches/{batch['id']}/files/source.csv", headers=headers, content=b"value\n1").status_code == 403
    assert client.post(f"/api/v1/batches/{batch['id']}/ready", headers=headers).status_code == 403


@pytest.mark.parametrize("first_source", ["schedule", "event"])
def test_event_and_schedule_same_input_label_are_order_independent(app, client, admin, project, registered, first_source):
    caller = create_delivery_caller(client, admin, project, registered)
    event_headers = {"Authorization": "Bearer " + caller["token"]}
    due = datetime(2026, 9, 1, 8, tzinfo=timezone.utc).timestamp()
    schedule = client.post("/api/v1/schedules", headers=admin, json={
        "task_id": registered["task"]["id"], "name": "独立审查标签合并", "cron": "0 8 1 * *", "timezone": "UTC",
        "parameters": {"period": "${previous_month}"}, "input_label": "${previous_month}",
        "enabled": True, "misfire_policy": "once"}).json()
    def scan():
        with app.state.factory.begin() as db:
            lock(db)
            db.get(Schedule, schedule["id"]).next_fire = due
            scan_schedules(db, app.state.settings, due + 1)

    if first_source == "schedule":
        scan()
    batch = client.post("/api/v1/batches", headers=admin, json={"project_id": project["id"], "label": "2026-08"}).json()
    assert client.put(f"/api/v1/batches/{batch['id']}/files/source.csv", headers=admin, content=b"value\n1").status_code == 200
    assert client.post(f"/api/v1/batches/{batch['id']}/ready", headers=admin).status_code == 200
    event = {"event_id": "independent-event", "input_batch_id": batch["id"], "parameters": {"period": "2026-08"}}
    route = f"/api/v1/tasks/{registered['task']['id']}/runs"
    accepted = client.post(route, headers=event_headers, json=event)
    assert accepted.status_code == (200 if first_source == "schedule" else 202)
    if first_source == "event":
        scan()
    with app.state.factory.begin() as db:
        lock(db)
        scan_health(db, app.state.settings, due + 2)
    with app.state.factory() as db:
        runs = db.scalars(select(Run).where(Run.task_id == registered["task"]["id"])).all()
        assert len(runs) == 1
        assert runs[0].input_batch_id == batch["id"] and runs[0].state == "queued"
    duplicate = client.post(route, headers=event_headers, json=event)
    assert duplicate.status_code == 200 and duplicate.json()["run_id"] == accepted.json()["run_id"]
    conflict = client.post(route, headers=event_headers, json={**event, "parameters": {"period": "changed"}})
    assert conflict.status_code == 409


def test_revoked_trigger_caller_cannot_start_already_leased_run(client, admin, project, registered):
    caller = create_delivery_caller(client, admin, project, registered)
    headers = {"Authorization": "Bearer " + caller["token"]}
    run = client.post(f"/api/v1/tasks/{registered['task']['id']}/runs", headers=headers,
                      json={"event_id": "revoke-before-start"}).json()
    node = client.post("/api/v1/nodes", headers=admin, json={"project_id": project["id"], "name": "独立启动复核节点"}).json()
    node_headers = {"Authorization": "Bearer " + node["token"]}
    assert client.post("/api/v1/agent/heartbeat", headers=node_headers,
                       json={"capabilities": [], "isolation": "development", "agent_version": "0.1.0"}).status_code == 200
    lease = client.post("/api/v1/agent/claim", headers=node_headers, json={}).json()
    assert lease["run_id"] == run["run_id"]
    assert client.patch(f"/api/v1/callers/{caller['id']}", headers=admin, json={"active": False}).status_code == 200
    response = client.post(f"/api/v1/agent/runs/{run['run_id']}/started", headers=node_headers,
                           json={"attempt_id": lease["attempt_id"], "pid": 12345})
    assert response.status_code == 409 and response.json()["code"] == "start_revoked"


def test_write_lock_rejects_authenticated_user_after_logout(app, client, admin):
    with app.state.factory.begin() as writing:
        login = writing.scalar(select(LoginSession).where(LoginSession.token_hash == digest(admin["Authorization"][7:])))
        stale = Identity("user", writing.get(User, login.user_id), login.id)
        assert client.post("/api/v1/auth/logout", headers=admin).status_code == 200
        with pytest.raises(Problem) as rejected:
            write_lock(writing, stale)
        assert rejected.value.status == 401


def test_write_lock_rejects_authenticated_node_after_disable(app, client, admin, project):
    response = client.post("/api/v1/nodes", headers=admin, json={"project_id": project["id"], "name": "节点撤权复核"})
    assert response.status_code == 201
    node_id = response.json()["id"]
    with app.state.factory.begin() as writing:
        stale = Identity("node", writing.get(Node, node_id))
        assert stale.record.enabled is True
        assert client.patch(f"/api/v1/nodes/{node_id}", headers=admin, json={"enabled": False}).status_code == 200
        with pytest.raises(Problem) as rejected:
            write_lock(writing, stale)
        assert rejected.value.status == 401
