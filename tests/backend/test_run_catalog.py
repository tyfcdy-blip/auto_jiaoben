import time

from workbench.models import Project, Run, Schedule, Task, TriggerEvent, Version


def test_global_run_catalog_is_authorized_and_filterable(client, admin, project, registered, app):
    with app.state.factory.begin() as db:
        second = Project(name="第二授权项目")
        hidden = Project(name="不可见项目")
        db.add_all([second, hidden])
        db.flush()
        second_task = Task(project_id=second.id, name="第二任务", version_id=registered["version"]["id"])
        hidden_task = Task(project_id=hidden.id, name="隐藏任务", version_id=registered["version"]["id"])
        db.add_all([second_task, hidden_task])
        db.flush()
        db.add_all([
            Run(project_id=project["id"], task_id=registered["task"]["id"],
                version_id=registered["version"]["id"], actor="user:synthetic", source="manual",
                parameters={}, business_key="catalog-manual", state="succeeded"),
            Run(project_id=second.id, task_id=second_task.id, version_id=registered["version"]["id"],
                actor="schedule:synthetic", source="schedule", parameters={},
                business_key="catalog-schedule", state="failed"),
            Run(project_id=hidden.id, task_id=hidden_task.id, version_id=registered["version"]["id"],
                actor="schedule:hidden-schedule", source="schedule", parameters={},
                business_key="catalog-hidden", state="queued"),
        ])
        second_id, second_task_id, hidden_task_id = second.id, second_task.id, hidden_task.id

    user = client.post("/api/v1/users", headers=admin, json={
        "username": "run-viewer", "display_name": "运行查看者", "password": "viewer-password-123"
    }).json()
    for project_id in (project["id"], second_id):
        assert client.post(f"/api/v1/projects/{project_id}/members", headers=admin,
                           json={"user_id": user["id"], "role": "viewer"}).status_code == 200
    token = client.post("/api/v1/auth/login", json={
        "username": "run-viewer", "password": "viewer-password-123"
    }).json()["token"]
    viewer = {"Authorization": "Bearer " + token}

    all_runs = client.get("/api/v1/runs", headers=viewer).json()
    assert all_runs["total"] == 2
    assert {item["project_name"] for item in all_runs["items"]} == {"隔离测试项目", "第二授权项目"}
    assert {item["source"] for item in all_runs["items"]} == {"manual", "schedule"}
    scheduled = client.get("/api/v1/runs", headers=viewer, params={
        "project_id": second_id, "task_id": second_task_id, "source": "schedule", "state": "failed"
    }).json()
    assert scheduled["total"] == 1 and scheduled["items"][0]["task_name"] == "第二任务"
    failed_group = client.get("/api/v1/runs", headers=viewer, params={
        "project_id": second_id, "state_group": "failed"
    }).json()
    assert failed_group["total"] == 1 and failed_group["items"][0]["state"] == "failed"
    assert client.get("/api/v1/runs", headers=viewer, params={
        "project_id": second_id, "state": "failed", "state_group": "failed"
    }).json()["total"] == 1
    assert client.get("/api/v1/runs", headers=viewer, params={
        "project_id": second_id, "state": "succeeded", "state_group": "failed"
    }).json()["total"] == 0
    assert client.get("/api/v1/runs", headers=viewer, params={"task_id": hidden_task_id}).json()["total"] == 0
    assert client.get("/api/v1/runs", headers=viewer,
                      params={"schedule_id": "hidden-schedule"}).json()["total"] == 0
    assert client.get("/api/v1/runs", headers=viewer, params={"source": "unknown-source"}).status_code == 422
    assert client.get("/api/v1/runs", headers=viewer, params={"state": "unknown-state"}).status_code == 422
    assert client.get("/api/v1/runs", headers=viewer,
                      params={"state_group": "unknown-group"}).status_code == 422


def test_schedule_list_can_be_scoped_to_one_task(client, admin, registered):
    first = client.post("/api/v1/schedules", headers=admin, json={
        "task_id": registered["task"]["id"], "name": "任务内计划", "cron": "0 8 * * *"
    })
    assert first.status_code == 201
    page = client.get("/api/v1/schedules", headers=admin,
                      params={"task_id": registered["task"]["id"]}).json()
    assert page["total"] == 1 and page["items"][0]["id"] == first.json()["id"]


def test_run_catalog_can_be_scoped_to_exact_schedule(client, admin, project, registered, app):
    first = client.post("/api/v1/schedules", headers=admin, json={
        "task_id": registered["task"]["id"], "name": "计划一", "cron": "0 8 * * *",
    }).json()
    second = client.post("/api/v1/schedules", headers=admin, json={
        "task_id": registered["task"]["id"], "name": "计划二", "cron": "0 9 * * *",
    }).json()
    with app.state.factory.begin() as db:
        for schedule, key in ((first, "first"), (second, "second")):
            run = Run(
                project_id=project["id"], task_id=registered["task"]["id"],
                version_id=registered["version"]["id"], actor="user:manual",
                source="manual", parameters={}, business_key=f"schedule-filter-{key}",
                state="succeeded",
            )
            db.add(run)
            db.flush()
            db.add(TriggerEvent(
                actor=f"schedule:{schedule['id']}", event_id=f"1:{key}",
                request_hash=(key * 64)[:64], run_id=run.id,
            ))
            if key == "first":
                db.add(TriggerEvent(
                    actor=f"schedule:{schedule['id']}", event_id="2:first",
                    request_hash="f" * 64, run_id=run.id,
                ))
        db.add(Run(
            project_id=project["id"], task_id=registered["task"]["id"],
            version_id=registered["version"]["id"], actor="user:manual", source="manual",
            parameters={}, business_key="schedule-filter-manual", state="succeeded",
        ))
    page = client.get("/api/v1/runs", headers=admin, params={"schedule_id": first["id"]}).json()
    assert page["total"] == 2
    assert page["items"][0]["source"] == "schedule"
    assert page["items"][0]["original_source"] == "manual"
    assert page["items"][0]["schedule_deduplicated"] is True
    assert {item["schedule_event_id"] for item in page["items"]} == {"1:first", "2:first"}
    assert client.get("/api/v1/runs", headers=admin, params={
        "schedule_id": first["id"], "state_group": "failed"
    }).json()["total"] == 0

    own_schedule = client.post("/api/v1/schedules", headers=admin, json={
        "task_id": registered["task"]["id"], "name": "固定批次计划", "cron": "0 10 * * *",
    }).json()
    with app.state.factory.begin() as db:
        run = Run(
            project_id=project["id"], task_id=registered["task"]["id"],
            version_id=registered["version"]["id"], actor=f"schedule:{own_schedule['id']}",
            source="schedule", parameters={}, business_key="same-schedule-run", state="succeeded",
        )
        db.add(run)
        db.flush()
        db.add_all([
            TriggerEvent(actor=f"schedule:{own_schedule['id']}", event_id="1:100",
                         request_hash="1" * 64, run_id=run.id, created_at=100),
            TriggerEvent(actor=f"schedule:{own_schedule['id']}", event_id="1:200",
                         request_hash="2" * 64, run_id=run.id, created_at=200),
        ])
    repeated = client.get("/api/v1/runs", headers=admin,
                          params={"schedule_id": own_schedule["id"]}).json()
    assert [(item["schedule_event_id"], item["schedule_deduplicated"])
            for item in repeated["items"]] == [("1:200", True), ("1:100", False)]


def test_schedule_toggle_requires_operator_and_recomputes_next_fire(client, admin, project, registered, app):
    schedule = client.post("/api/v1/schedules", headers=admin, json={
        "task_id": registered["task"]["id"], "name": "启停计划", "cron": "0 8 * * *"
    }).json()
    viewer = client.post("/api/v1/users", headers=admin, json={
        "username": "schedule-viewer", "display_name": "计划查看者", "password": "viewer-password-123"
    }).json()
    client.post(f"/api/v1/projects/{project['id']}/members", headers=admin,
                json={"user_id": viewer["id"], "role": "viewer"})
    token = client.post("/api/v1/auth/login", json={
        "username": "schedule-viewer", "password": "viewer-password-123"
    }).json()["token"]
    assert client.patch(f"/api/v1/schedules/{schedule['id']}",
                        headers={"Authorization": "Bearer " + token}, json={"enabled": True}).status_code == 403
    viewer_headers = {"Authorization": "Bearer " + token}
    assert client.put(f"/api/v1/schedules/{schedule['id']}", headers=viewer_headers, json={
        "base_revision": schedule["revision"], "name": "越权修改", "cron": "0 9 * * *",
        "timezone": "Asia/Shanghai", "parameters": {}, "input_batch_id": None,
        "input_label": None, "misfire_policy": "skip",
    }).status_code == 403
    assert client.delete(f"/api/v1/schedules/{schedule['id']}", headers=viewer_headers).status_code == 403
    with app.state.factory.begin() as db:
        db.get(Schedule, schedule["id"]).next_fire = 1
    enabled = client.patch(f"/api/v1/schedules/{schedule['id']}", headers=admin,
                           json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True and enabled.json()["revision"] == 2
    assert enabled.json()["next_fire"] > "2026-01-01"
    with app.state.factory() as db:
        assert db.get(Schedule, schedule["id"]).next_fire > time.time()


def test_schedule_enable_revalidates_parameters_against_current_task_version(
        client, admin, registered, app):
    schedule = client.post("/api/v1/schedules", headers=admin, json={
        "task_id": registered["task"]["id"], "name": "待复验计划", "cron": "0 8 * * *",
    }).json()
    with app.state.factory.begin() as db:
        version = db.get(Version, registered["version"]["id"])
        version.parameter_schema = {
            "type": "object",
            "properties": {"new_required": {"type": "string"}},
            "required": ["new_required"],
            "additionalProperties": False,
        }
    enabled = client.patch(f"/api/v1/schedules/{schedule['id']}", headers=admin,
                           json={"enabled": True})
    assert enabled.status_code == 422
    detail = client.get(f"/api/v1/schedules/{schedule['id']}", headers=admin).json()
    assert detail["enabled"] is False and detail["revision"] == schedule["revision"]
