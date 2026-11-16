import io
import zipfile
from sqlalchemy import func, select, text

from workbench.db import initialize
from workbench.models import NodeConfigState, NodeProjectGrant


def create_task_for_project(client, admin, project, name, *, node_id=None):
    program = client.post("/api/v1/programs", headers=admin,
                          json={"project_id": project["id"], "name": name + "程序"}).json()
    version = client.post(f'/api/v1/programs/{program["id"]}/versions', headers=admin, json={
        "name": "v1", "command": ["python", "main.py"], "result_required": False,
        "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    }).json()
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("main.py", "print('shared-node-test')")
    assert client.post(f'/api/v1/versions/{version["id"]}/bundle', headers=admin,
                       content=bundle.getvalue()).status_code == 200
    assert client.post(f'/api/v1/versions/{version["id"]}/publish', headers=admin).status_code == 200
    task_response = client.post("/api/v1/tasks", headers=admin, json={
        "project_id": project["id"], "name": name + "任务", "version_id": version["id"],
        "node_id": node_id,
    })
    return version, task_response


def create_project_task(client, admin, name, *, node_id=None):
    project = client.post("/api/v1/projects", headers=admin, json={"name": name}).json()
    version, task_response = create_task_for_project(client, admin, project, name, node_id=node_id)
    return project, version, task_response


def managed_node(client, admin, project, *, slots=1):
    node = client.post("/api/v1/nodes", headers=admin, json={
        "project_id": project["id"], "name": "共享执行电脑", "capabilities": ["python"],
        "slots": slots, "input_roots": ["D:/共享输入"],
    }).json()
    agent = {"Authorization": "Bearer " + node["token"]}
    heartbeat = {
        "capabilities": ["python", "node-config-v1", "directory-input-v1"],
        "isolation": "development", "agent_version": "0.1.0",
        "config_revision": node["config_revision"], "config_hash": node["config_hash"],
        "reported_input_roots": ["D:/共享输入"],
    }
    assert client.post("/api/v1/agent/heartbeat", headers=agent, json=heartbeat).status_code == 200
    return node, agent


def complete(client, agent, lease):
    response = client.post(f'/api/v1/agent/runs/{lease["run_id"]}/complete', headers=agent, json={
        "attempt_id": lease["attempt_id"], "exit_code": 0, "observation": "exited",
    })
    assert response.status_code == 200


def apply_latest_configuration(client, agent):
    latest = client.get("/api/v1/agent/configuration", headers=agent).json()
    acknowledgement = client.post("/api/v1/agent/configuration/ack", headers=agent, json={
        "revision": latest["revision"], "config_hash": latest["config_hash"],
        "status": "applied", "error": "",
    })
    assert acknowledgement.status_code == 200
    return latest


def test_one_managed_agent_serves_projects_with_global_slots_and_fair_turns(
        client, admin, project, registered):
    node, agent = managed_node(client, admin, project)
    second, _, task_response = create_project_task(client, admin, "第二项目", node_id=node["id"])
    assert task_response.status_code == 422
    assert task_response.json()["code"] == "invalid_node"

    grant = client.put(f'/api/v1/nodes/{node["id"]}/projects/{second["id"]}', headers=admin,
                       json={"base_revision": node["config_revision"], "enabled": True,
                             "input_roots": ["D:/共享输入/项目二"]})
    assert grant.status_code == 200
    apply_latest_configuration(client, agent)
    # 授权后为第二项目创建任务，验证同一个节点可被两个项目绑定。
    _, second_task = create_task_for_project(client, admin, second, "第二项目已授权", node_id=node["id"])
    assert second_task.status_code == 201

    first_runs = [client.post(f'/api/v1/tasks/{registered["task"]["id"]}/runs', headers=admin,
                              json={"event_id": f"shared-first-{index}", "parameters": {}}).json()
                  for index in range(2)]
    second_runs = [client.post(f'/api/v1/tasks/{second_task.json()["id"]}/runs', headers=admin,
                               json={"event_id": f"shared-second-{index}", "parameters": {}}).json()
                   for index in range(2)]
    first_lease = client.post("/api/v1/agent/claim", headers=agent).json()
    assert first_lease["run_id"] == first_runs[0]["run_id"]
    assert first_lease["project_id"] == project["id"]
    assert client.post("/api/v1/agent/claim", headers=agent).status_code == 204
    complete(client, agent, first_lease)
    second_lease = client.post("/api/v1/agent/claim", headers=agent).json()
    assert second_lease["run_id"] == second_runs[0]["run_id"]
    assert second_lease["project_id"] == second["id"]
    assert second_lease["allowed_input_roots"] == ["D:/共享输入/项目二"]
    complete(client, agent, second_lease)
    third_lease = client.post("/api/v1/agent/claim", headers=agent).json()
    assert third_lease["run_id"] == first_runs[1]["run_id"]
    complete(client, agent, third_lease)
    fourth_lease = client.post("/api/v1/agent/claim", headers=agent).json()
    assert fourth_lease["run_id"] == second_runs[1]["run_id"]

    listed = client.get("/api/v1/nodes", headers=admin,
                        params={"project_id": second["id"]}).json()["items"]
    assert [item["id"] for item in listed] == [node["id"]]


def test_atomic_configuration_requires_maintenance_revision_and_safe_roots(client, admin, project):
    node, _ = managed_node(client, admin, project)
    second = client.post("/api/v1/projects", headers=admin, json={"name": "授权项目"}).json()
    endpoint = f'/api/v1/nodes/{node["id"]}/configuration'
    base = {
        "base_revision": node["config_revision"], "name": "共享处理主机", "slots": 1,
        "input_roots": ["D:/共享输入", r"\\fileserver\finance"],
        "grants": [
            {"project_id": project["id"], "enabled": True, "input_roots": ["D:/共享输入/项目一"]},
            {"project_id": second["id"], "enabled": True,
             "input_roots": [r"\\fileserver\finance\项目二"]},
        ],
    }
    assert client.put(endpoint, headers=admin, json=base).status_code == 409
    assert client.patch(f'/api/v1/nodes/{node["id"]}', headers=admin,
                        json={"maintenance": True}).status_code == 200
    outside = {**base, "grants": [*base["grants"][:-1],
                                   {"project_id": second["id"], "enabled": True,
                                    "input_roots": [r"\\other\share"]}]}
    assert client.put(endpoint, headers=admin, json=outside).status_code == 422
    unchanged = client.get(endpoint, headers=admin).json()
    assert unchanged["node"]["name"] == "共享执行电脑"
    assert unchanged["desired_revision"] == node["config_revision"]

    unsafe_parallel = client.put(endpoint, headers=admin, json={**base, "slots": 2})
    assert unsafe_parallel.status_code == 422
    assert unsafe_parallel.json()["code"] == "shared_node_single_slot"

    saved = client.put(endpoint, headers=admin, json=base)
    assert saved.status_code == 200
    body = saved.json()
    assert body["node"]["name"] == "共享处理主机"
    assert body["node"]["slots"] == 1
    assert body["desired_revision"] == node["config_revision"] + 1
    assert body["config_status"] == "pending"
    assert {grant["project_id"] for grant in body["grants"] if grant["enabled"]} == {
        project["id"], second["id"],
    }
    assert client.put(endpoint, headers=admin, json=base).status_code == 409


def test_revoking_project_after_lease_blocks_download_and_start(client, admin, project):
    node, agent = managed_node(client, admin, project)
    second = client.post("/api/v1/projects", headers=admin, json={"name": "可撤权项目"}).json()
    granted = client.put(f'/api/v1/nodes/{node["id"]}/projects/{second["id"]}', headers=admin,
                         json={"base_revision": node["config_revision"],
                               "enabled": True, "input_roots": []})
    assert granted.status_code == 200
    apply_latest_configuration(client, agent)
    _, second_task = create_task_for_project(client, admin, second, "可撤权", node_id=node["id"])
    assert second_task.status_code == 201
    run = client.post(f'/api/v1/tasks/{second_task.json()["id"]}/runs', headers=admin,
                      json={"event_id": "revoke-after-lease", "parameters": {}}).json()
    lease = client.post("/api/v1/agent/claim", headers=agent).json()
    assert lease["run_id"] == run["run_id"]
    revoked = client.put(f'/api/v1/nodes/{node["id"]}/projects/{second["id"]}', headers=admin,
                         json={"base_revision": granted.json()["config_revision"],
                               "enabled": False, "input_roots": []})
    assert revoked.status_code == 200
    assert client.get(lease["bundle_url"], headers=agent).status_code == 409
    started = client.post(f'/api/v1/agent/runs/{run["run_id"]}/started', headers=agent,
                          json={"attempt_id": lease["attempt_id"], "pid": 123})
    assert started.status_code == 409
    assert started.json()["code"] == "start_revoked"


def test_initialize_adds_shared_node_tables_and_backfills_legacy_nodes(
        client, admin, project, app):
    node = client.post("/api/v1/nodes", headers=admin, json={
        "project_id": project["id"], "name": "旧版节点", "capabilities": [],
    }).json()
    patch_node = client.post("/api/v1/nodes", headers=admin, json={
        "project_id": project["id"], "name": "旧版目录节点", "capabilities": [],
    }).json()
    grant_node = client.post("/api/v1/nodes", headers=admin, json={
        "project_id": project["id"], "name": "旧版授权节点", "capabilities": [],
    }).json()
    with app.state.engine.begin() as connection:
        connection.execute(text("DROP TABLE node_config_state"))
        connection.execute(text("DROP TABLE node_project_grants"))
    initialize(app.state.engine, app.state.factory)
    initialize(app.state.engine, app.state.factory)
    with app.state.factory() as db:
        assert db.get(NodeConfigState, node["id"]) is not None
        assert db.scalar(select(func.count()).select_from(NodeProjectGrant).where(
            NodeProjectGrant.node_id == node["id"],
            NodeProjectGrant.project_id == project["id"],
        )) == 1

    assert client.patch(f'/api/v1/nodes/{node["id"]}', headers=admin,
                        json={"maintenance": True}).status_code == 200
    legacy = client.get(f'/api/v1/nodes/{node["id"]}/configuration', headers=admin).json()
    assert legacy["desired_revision"] == 0
    configuration = {
        "base_revision": 0, "name": "旧节点已纳管", "slots": 1,
        "input_roots": ["D:/旧节点输入"],
        "grants": [{"project_id": project["id"], "enabled": True,
                    "input_roots": ["D:/旧节点输入"]}],
    }
    assert client.put(f'/api/v1/nodes/{node["id"]}/configuration', headers=admin,
                      json={**configuration, "base_revision": -1}).status_code == 422
    maintained = client.put(f'/api/v1/nodes/{node["id"]}/configuration', headers=admin,
                            json=configuration)
    assert maintained.status_code == 200
    assert maintained.json()["desired_revision"] == 1
    assert client.put(f'/api/v1/nodes/{node["id"]}/configuration', headers=admin,
                      json=configuration).status_code == 409

    assert client.patch(f'/api/v1/nodes/{patch_node["id"]}', headers=admin,
                        json={"maintenance": True}).status_code == 200
    directory_patch = {"base_revision": 0, "input_roots": ["D:/旧版目录"]}
    patched = client.patch(f'/api/v1/nodes/{patch_node["id"]}', headers=admin,
                           json=directory_patch)
    assert patched.status_code == 200
    assert patched.json()["config_revision"] == 1
    assert client.patch(f'/api/v1/nodes/{patch_node["id"]}', headers=admin,
                        json=directory_patch).status_code == 409

    grant_endpoint = f'/api/v1/nodes/{grant_node["id"]}/projects/{project["id"]}'
    grant_update = {"base_revision": 0, "enabled": True, "input_roots": []}
    granted = client.put(grant_endpoint, headers=admin, json=grant_update)
    assert granted.status_code == 200
    assert granted.json()["config_revision"] == 1
    assert client.put(grant_endpoint, headers=admin, json=grant_update).status_code == 409


def test_shared_node_global_controls_require_platform_admin(client, admin, project):
    node, _ = managed_node(client, admin, project)
    second = client.post("/api/v1/projects", headers=admin, json={"name": "共享使用项目"}).json()
    granted = client.put(f'/api/v1/nodes/{node["id"]}/projects/{second["id"]}', headers=admin,
                         json={"base_revision": node["config_revision"],
                               "enabled": True, "input_roots": []})
    assert granted.status_code == 200
    stale = client.put(f'/api/v1/nodes/{node["id"]}/projects/{second["id"]}', headers=admin,
                       json={"base_revision": node["config_revision"],
                             "enabled": False, "input_roots": []})
    assert stale.status_code == 409

    user = client.post("/api/v1/users", headers=admin, json={
        "username": "owner-admin", "display_name": "所属项目管理员",
        "password": "synthetic-password-123",
    }).json()
    assert client.post(f'/api/v1/projects/{project["id"]}/members', headers=admin, json={
        "user_id": user["id"], "role": "project_admin",
    }).status_code == 200
    login = client.post("/api/v1/auth/login", json={
        "username": "owner-admin", "password": "synthetic-password-123",
    }).json()
    owner_admin = {"Authorization": "Bearer " + login["token"]}
    response = client.patch(f'/api/v1/nodes/{node["id"]}', headers=owner_admin,
                            json={"enabled": False})
    assert response.status_code == 403
    assert response.json()["code"] == "shared_node_admin_required"


def test_pending_configuration_explains_queued_run_and_node_capacity(
        client, admin, project):
    node = client.post("/api/v1/nodes", headers=admin, json={
        "project_id": project["id"], "name": "待升级电脑", "capabilities": ["python"],
        "slots": 1, "input_roots": [],
    }).json()
    agent = {"Authorization": "Bearer " + node["token"]}
    assert client.post("/api/v1/agent/heartbeat", headers=agent, json={
        "capabilities": ["python"], "isolation": "development", "agent_version": "0.1.0",
    }).status_code == 200
    second = client.post("/api/v1/projects", headers=admin,
                         json={"name": "等待配置项目"}).json()
    grant = client.put(f'/api/v1/nodes/{node["id"]}/projects/{second["id"]}', headers=admin,
                       json={"base_revision": node["config_revision"],
                             "enabled": True, "input_roots": []})
    assert grant.status_code == 200
    _, task_response = create_task_for_project(client, admin, second, "等待配置", node_id=node["id"])
    assert task_response.status_code == 201
    run = client.post(f'/api/v1/tasks/{task_response.json()["id"]}/runs', headers=admin,
                      json={"event_id": "pending-config-run", "parameters": {}}).json()

    detail = client.get(f'/api/v1/runs/{run["run_id"]}', headers=admin).json()
    assert detail["state"] == "queued"
    assert detail["waiting_reason_code"] == "config_pending"
    assert "版本过旧" in detail["waiting_reason"]
    listed_run = client.get("/api/v1/runs", headers=admin,
                            params={"project_id": second["id"]}).json()["items"][0]
    assert listed_run["waiting_reason_code"] == "config_pending"

    listed_node = client.get("/api/v1/nodes", headers=admin).json()["items"][0]
    assert listed_node["dispatch_status"] == "config_pending"
    assert listed_node["capacity_status"] == "idle"
    assert listed_node["active_runs"] == 0
    assert listed_node["available_slots"] == 1

    marker = "SENSITIVE_LOCAL_DETAIL_DO_NOT_EXPOSE"
    desired = client.get("/api/v1/agent/configuration", headers=agent).json()
    assert client.post("/api/v1/agent/configuration/ack", headers=agent, json={
        "revision": desired["revision"], "config_hash": desired["config_hash"],
        "status": "failed", "error": marker,
    }).status_code == 200
    viewer = client.post("/api/v1/users", headers=admin, json={
        "username": "dispatch-viewer", "display_name": "排队查看者",
        "password": "synthetic-password-123",
    }).json()
    assert client.post(f'/api/v1/projects/{second["id"]}/members', headers=admin, json={
        "user_id": viewer["id"], "role": "viewer",
    }).status_code == 200
    viewer_login = client.post("/api/v1/auth/login", json={
        "username": "dispatch-viewer", "password": "synthetic-password-123",
    }).json()
    viewer_headers = {"Authorization": "Bearer " + viewer_login["token"]}
    viewer_nodes = client.get("/api/v1/nodes", headers=viewer_headers,
                              params={"project_id": second["id"]}).json()
    viewer_run = client.get(f'/api/v1/runs/{run["run_id"]}', headers=viewer_headers).json()
    assert viewer_nodes["items"][0]["dispatch_status"] == "config_failed"
    assert viewer_run["waiting_reason_code"] == "config_failed"
    assert marker not in str(viewer_nodes)
    assert marker not in str(viewer_run)
    assert client.get(f'/api/v1/nodes/{node["id"]}/configuration',
                      headers=admin).json()["config_error"] == marker


def test_busy_capacity_and_queued_reason_match_active_slot(
        client, admin, project, registered):
    node, agent = managed_node(client, admin, project)
    first = client.post(f'/api/v1/tasks/{registered["task"]["id"]}/runs', headers=admin,
                        json={"event_id": "busy-slot-1", "parameters": {}}).json()
    second = client.post(f'/api/v1/tasks/{registered["task"]["id"]}/runs', headers=admin,
                         json={"event_id": "busy-slot-2", "parameters": {}}).json()
    lease = client.post("/api/v1/agent/claim", headers=agent).json()
    assert lease["run_id"] == first["run_id"]

    listed = client.get("/api/v1/nodes", headers=admin,
                        params={"project_id": project["id"]}).json()["items"][0]
    assert listed["capacity_status"] == "busy"
    assert listed["dispatch_status"] == "busy"
    assert listed["active_runs"] == 1
    assert listed["available_slots"] == 0
    waiting = client.get(f'/api/v1/runs/{second["run_id"]}', headers=admin).json()
    assert waiting["waiting_reason_code"] == "node_busy"
    complete(client, agent, lease)
