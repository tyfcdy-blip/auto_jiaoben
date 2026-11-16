def task_payload(project, registered, **changes):
    values = {
        "project_id": project["id"],
        "name": "可维护任务",
        "version_id": registered["version"]["id"],
        "default_parameters": {"period": "2026-09"},
        "node_id": None,
        "timeout_seconds": 300,
        "resource_keys": [],
        "business_key_fields": ["period"],
        "requires_input": False,
        "operation": "run",
    }
    values.update(changes)
    return values


def update_payload(task):
    return {**{key: task[key] for key in (
        "name", "version_id", "default_parameters", "node_id", "timeout_seconds",
        "resource_keys", "business_key_fields", "requires_input", "operation",
    )}, "base_config_hash": task["config_hash"]}


def test_task_can_be_read_updated_and_deleted_when_unused(client, admin, project, registered):
    created = client.post("/api/v1/tasks", headers=admin,
                          json=task_payload(project, registered)).json()
    search = client.get("/api/v1/tasks", headers=admin,
                        params={"project_id": project["id"], "q": "可维护"}).json()
    assert search["total"] == 1 and search["items"][0]["id"] == created["id"]
    payload = update_payload(created)
    payload.update(name="已修正任务", timeout_seconds=600)
    updated = client.put(f"/api/v1/tasks/{created['id']}", headers=admin, json=payload)
    assert updated.status_code == 200
    assert updated.json()["name"] == "已修正任务"
    assert updated.json()["timeout_seconds"] == 600
    detail = client.get(f"/api/v1/tasks/{created['id']}", headers=admin).json()
    assert detail["name"] == "已修正任务"
    assert detail["program_id"] == registered["program"]["id"]
    assert client.delete(f"/api/v1/tasks/{created['id']}", headers=admin).status_code == 204
    assert client.get(f"/api/v1/tasks/{created['id']}", headers=admin).status_code == 404


def test_task_delete_preserves_dependencies_and_execution_edit_requires_paused_plan(
        client, admin, project, registered):
    created = client.post("/api/v1/tasks", headers=admin,
                          json=task_payload(project, registered, name="有计划任务")).json()
    schedule = client.post("/api/v1/schedules", headers=admin, json={
        "task_id": created["id"], "name": "活动计划", "cron": "0 8 * * *", "enabled": True,
        "parameters": {"period": "2026-09"},
    }).json()
    changed = update_payload(created)
    changed["timeout_seconds"] = 601
    response = client.put(f"/api/v1/tasks/{created['id']}", headers=admin, json=changed)
    assert response.status_code == 409 and response.json()["code"] == "active_schedules"
    assert client.delete(f"/api/v1/tasks/{created['id']}", headers=admin).status_code == 409
    assert client.delete(f"/api/v1/schedules/{schedule['id']}", headers=admin).status_code == 204
    assert client.delete(f"/api/v1/tasks/{created['id']}", headers=admin).status_code == 204


def test_task_with_run_history_cannot_be_deleted(client, admin, project, registered):
    response = client.post(
        f"/api/v1/tasks/{registered['task']['id']}/runs", headers=admin,
        json={"event_id": "task-delete-history", "parameters": {}},
    )
    assert response.status_code == 202
    deleted = client.delete(f"/api/v1/tasks/{registered['task']['id']}", headers=admin)
    assert deleted.status_code == 409 and deleted.json()["code"] == "task_has_history"


def test_active_run_blocks_execution_edit_and_caller_reference_blocks_delete(
        client, admin, project, registered):
    active_task = client.get(f"/api/v1/tasks/{registered['task']['id']}", headers=admin).json()
    accepted = client.post(
        f"/api/v1/tasks/{active_task['id']}/runs", headers=admin,
        json={"event_id": "active-edit-guard", "parameters": {}},
    )
    assert accepted.status_code == 202
    update = update_payload(active_task)
    update["timeout_seconds"] += 1
    blocked = client.put(f"/api/v1/tasks/{active_task['id']}", headers=admin, json=update)
    assert blocked.status_code == 409 and blocked.json()["code"] == "task_in_use"

    referenced = client.post("/api/v1/tasks", headers=admin,
                             json=task_payload(project, registered, name="被事件引用任务")).json()
    caller = client.post("/api/v1/callers", headers=admin, json={
        "project_id": project["id"], "name": "影刀调用方", "task_ids": [referenced["id"]],
    })
    assert caller.status_code == 201
    deleted = client.delete(f"/api/v1/tasks/{referenced['id']}", headers=admin)
    assert deleted.status_code == 409 and deleted.json()["code"] == "task_has_callers"


def test_schedule_can_be_read_updated_and_deleted(client, admin, registered):
    created = client.post("/api/v1/schedules", headers=admin, json={
        "task_id": registered["task"]["id"], "name": "待修正计划", "cron": "0 8 * * *",
    }).json()
    search = client.get("/api/v1/schedules", headers=admin,
                        params={"project_id": registered["task"]["project_id"], "q": "待修正"}).json()
    assert search["total"] == 1 and search["items"][0]["id"] == created["id"]
    detail = client.get(f"/api/v1/schedules/{created['id']}", headers=admin)
    assert detail.status_code == 200 and detail.json()["name"] == "待修正计划"
    updated = client.put(f"/api/v1/schedules/{created['id']}", headers=admin, json={
        "base_revision": created["revision"],
        "name": "已修正计划", "cron": "30 9 * * *", "timezone": "Asia/Shanghai",
        "parameters": {"period": "${scheduled_date}"}, "input_batch_id": None,
        "input_label": None, "misfire_policy": "once",
    })
    assert updated.status_code == 200
    assert updated.json()["name"] == "已修正计划"
    assert updated.json()["cron"] == "30 9 * * *"
    assert updated.json()["revision"] == 2
    assert updated.json()["misfire_policy"] == "once"
    assert client.delete(f"/api/v1/schedules/{created['id']}", headers=admin).status_code == 204
    assert client.get(f"/api/v1/schedules/{created['id']}", headers=admin).status_code == 404


def test_stale_task_and_schedule_edits_are_rejected(client, admin, project, registered):
    task = client.post("/api/v1/tasks", headers=admin,
                       json=task_payload(project, registered, name="并发任务")).json()
    first_task_update = update_payload(task)
    first_task_update["name"] = "先保存的任务"
    assert client.put(f"/api/v1/tasks/{task['id']}", headers=admin,
                      json=first_task_update).status_code == 200
    stale_task = client.put(f"/api/v1/tasks/{task['id']}", headers=admin,
                            json={**first_task_update, "name": "覆盖任务"})
    assert stale_task.status_code == 409 and stale_task.json()["code"] == "stale_config"

    schedule = client.post("/api/v1/schedules", headers=admin, json={
        "task_id": registered["task"]["id"], "name": "并发计划", "cron": "0 8 * * *",
    }).json()
    update = {
        "base_revision": schedule["revision"], "name": "先保存的计划", "cron": "30 9 * * *",
        "timezone": "Asia/Shanghai", "parameters": {}, "input_batch_id": None,
        "input_label": None, "misfire_policy": "skip",
    }
    assert client.put(f"/api/v1/schedules/{schedule['id']}", headers=admin,
                      json=update).status_code == 200
    stale_schedule = client.put(f"/api/v1/schedules/{schedule['id']}", headers=admin,
                                json={**update, "name": "覆盖计划"})
    assert stale_schedule.status_code == 409 and stale_schedule.json()["code"] == "stale_config"


def test_deleted_schedule_keeps_trigger_history_queryable(client, admin, project, registered, app):
    from workbench.models import Run, TriggerEvent

    schedule = client.post("/api/v1/schedules", headers=admin, json={
        "task_id": registered["task"]["id"], "name": "保留历史计划", "cron": "0 8 * * *",
    }).json()
    with app.state.factory.begin() as db:
        run = Run(project_id=project["id"], task_id=registered["task"]["id"],
                  version_id=registered["version"]["id"], actor=f"schedule:{schedule['id']}",
                  source="schedule", parameters={}, business_key="deleted-schedule-history",
                  state="succeeded")
        db.add(run)
        db.flush()
        db.add(TriggerEvent(actor=f"schedule:{schedule['id']}", event_id="1:deleted",
                            request_hash="d" * 64, run_id=run.id))
    assert client.delete(f"/api/v1/schedules/{schedule['id']}", headers=admin).status_code == 204
    history = client.get("/api/v1/runs", headers=admin,
                         params={"schedule_id": schedule["id"]}).json()
    assert history["total"] == 1 and history["items"][0]["schedule_event_id"] == "1:deleted"
