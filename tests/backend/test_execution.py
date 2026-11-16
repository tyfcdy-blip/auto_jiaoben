import time
import pytest
from sqlalchemy import select
from workbench.db import lock
from workbench.models import Gate, Node, ResourceLock, Run, Task
from workbench.scheduling import scan_health


def setup_node(client, admin, project):
    node = client.post('/api/v1/nodes', headers=admin, json={"name": "执行节点", "project_id": project["id"]}).json()
    headers = {"Authorization": "Bearer "+node["token"]}
    assert client.post('/api/v1/agent/heartbeat', headers=headers, json={"capabilities": [], "isolation": "development", "agent_version": "0.1.0"}).status_code == 200
    return node, headers


def trigger(client, admin, registered, event="event1", **extra):
    return client.post('/api/v1/tasks/'+registered['task']['id']+'/runs', headers=admin,
                       json={"event_id": event, "parameters": {}, **extra})


def test_non_standard_json_number_is_rejected_as_invalid_parameters(client, admin, registered):
    response = client.post(
        '/api/v1/tasks/' + registered['task']['id'] + '/runs',
        headers={**admin, 'Content-Type': 'application/json'},
        content='{"event_id":"nan-event","parameters":{"period":NaN}}',
    )
    assert response.status_code == 422
    assert response.json()['code'] == 'invalid_parameters'


def test_event_retry_conflict_and_cross_identity_business_dedup(client, admin, registered, project):
    first = trigger(client, admin, registered)
    assert first.status_code == 202
    again = trigger(client, admin, registered)
    assert again.status_code == 200
    assert first.json()['run_id'] == again.json()['run_id']
    assert trigger(client, admin, registered, parameters={"period": "2026-08"}).status_code == 409


def test_no_input_different_events_are_distinct(client, admin, registered):
    first = trigger(client, admin, registered, "daily1").json()
    second = trigger(client, admin, registered, "daily2").json()
    assert first['run_id'] != second['run_id']


def test_each_manual_click_creates_a_new_run_even_with_business_key(client, admin, registered, app):
    with app.state.factory.begin() as db:
        db.get(Task, registered["task"]["id"]).business_key_fields = ["period"]
    path = f'/api/v1/tasks/{registered["task"]["id"]}/runs'
    first = client.post(path, headers=admin, json={"event_id": "manual-click-1",
                                                   "parameters": {"period": "2026-08"}})
    second = client.post(path, headers=admin, json={"event_id": "manual-click-2",
                                                    "parameters": {"period": "2026-08"}})
    retry = client.post(path, headers=admin, json={"event_id": "manual-click-2",
                                                   "parameters": {"period": "2026-08"}})
    assert first.status_code == second.status_code == 202
    assert first.json()["run_id"] != second.json()["run_id"]
    assert retry.status_code == 200
    assert retry.json()["run_id"] == second.json()["run_id"]


def test_real_claim_result_failure_log_dedup_and_secret_redaction(client, admin, project, registered):
    _, node = setup_node(client, admin, project)
    run = trigger(client, admin, registered).json()
    lease = client.post('/api/v1/agent/claim', headers=node, json={}).json()
    assert lease['run_id'] == run['run_id']
    attempt = lease['attempt_id']
    assert client.get(lease['bundle_url'], headers=node).status_code == 200
    assert client.post('/api/v1/agent/claim', headers=node, json={}).status_code == 204
    assert client.post(f'/api/v1/agent/runs/{run["run_id"]}/started', headers=node, json={"attempt_id": attempt, "pid": 123}).status_code == 200
    line = {"attempt_id": attempt, "entries": [{"sequence": 1, "stream": "stderr", "text": "password=do-not-echo failed"}]}
    for _ in range(2):
        assert client.post(f'/api/v1/agent/runs/{run["run_id"]}/logs', headers=node, json=line).status_code == 200
    logs = client.get(f'/api/v1/runs/{run["run_id"]}/logs', headers=admin).json()['entries']
    assert len(logs) == 1
    assert 'do-not-echo' not in logs[0]['text']
    completion = {"attempt_id": attempt, "exit_code": 7, "observation": "exited", "error": "合成失败"}
    for _ in range(2):
        result = client.post(f'/api/v1/agent/runs/{run["run_id"]}/complete', headers=node, json=completion)
        assert result.status_code == 200
        assert result.json()['state'] == 'failed'
    assert client.get('/api/v1/alerts', headers=admin).json()['total'] == 1


def test_input_waiting_freeze_and_manual_rerun(client, admin, project, registered, app):
    batch = client.post('/api/v1/batches', headers=admin, json={"project_id": project['id'], "label": "合成批次"}).json()
    assert client.put(f'/api/v1/batches/{batch["id"]}/files/test.csv', headers=admin, content=b'col\n1').status_code == 200
    run = trigger(client, admin, registered, input_batch_id=batch['id']).json()
    assert run['state'] == 'waiting_input'
    second = trigger(client, admin, registered, 'another-event', input_batch_id=batch['id'])
    assert second.status_code == 202
    assert second.json()['run_id'] != run['run_id']
    assert client.post(f'/api/v1/batches/{batch["id"]}/ready', headers=admin).status_code == 200
    assert client.put(f'/api/v1/batches/{batch["id"]}/files/more.csv', headers=admin, content=b'x').status_code == 409
    with app.state.factory.begin() as db:
        lock(db)
        scan_health(db, app.state.settings)
    assert client.get('/api/v1/runs/'+run['run_id'], headers=admin).json()['state'] == 'queued'


def test_lost_node_retains_lock_late_evidence_and_manual_reconcile(client, admin, registered, project, app):
    with app.state.factory.begin() as db:
        db.get(Task, registered['task']['id']).resource_keys = ['synthetic-table']
    node_record, node = setup_node(client, admin, project)
    run = trigger(client, admin, registered).json()
    lease = client.post('/api/v1/agent/claim', headers=node, json={}).json()
    with app.state.factory.begin() as db:
        lock(db)
        db.get(Node, node_record['id']).last_seen = time.time()-200
        scan_health(db, app.state.settings)
    payload = {"attempt_id": lease['attempt_id'], "exit_code": 0, "observation": "exited"}
    response = client.post(f'/api/v1/agent/runs/{run["run_id"]}/complete', headers=node, json=payload)
    assert response.json()['state'] == 'unknown'
    with app.state.factory() as db:
        assert db.scalar(select(ResourceLock).where(ResourceLock.run_id == run['run_id'])) is not None
        assert db.get(Run, run['run_id']).late_evidence['exit_code'] == 0
    response = client.post(f'/api/v1/runs/{run["run_id"]}/reconcile', headers=admin,
                           json={"outcome": "succeeded", "note": "在合成环境中核对进程退出及输出文件已完成"})
    assert response.status_code == 200
    with app.state.factory() as db:
        assert db.scalar(select(ResourceLock).where(ResourceLock.run_id == run['run_id'])) is None


def test_invalid_optional_result_is_not_success(client, admin, registered, project):
    _, node = setup_node(client, admin, project)
    run = trigger(client, admin, registered).json()
    lease = client.post('/api/v1/agent/claim', headers=node, json={}).json()
    result = client.post(f'/api/v1/agent/runs/{run["run_id"]}/complete', headers=node,
                         json={"attempt_id": lease['attempt_id'], "exit_code": 0, "observation": "exited", "error": "报告格式无效"})
    assert result.json()['state'] == 'failed'


def test_other_node_cannot_report_and_revoked_caller_never_starts(client, admin, registered, project):
    _, node = setup_node(client, admin, project)
    caller = client.post('/api/v1/callers', headers=admin, json={"name": "影刀测试", "project_id": project['id'], "task_ids": [registered['task']['id']]}).json()
    external = {"Authorization": "Bearer "+caller['token']}
    run = trigger(client, external, registered).json()
    client.patch('/api/v1/callers/'+caller['id'], headers=admin, json={"active": False})
    assert client.post('/api/v1/agent/claim', headers=node, json={}).status_code == 204
    assert client.get('/api/v1/runs/'+run['run_id'], headers=admin).json()['state'] == 'failed'


def test_cancel_queued_never_executes(client, admin, registered, project):
    _, node = setup_node(client, admin, project)
    run = trigger(client, admin, registered).json()
    assert client.post('/api/v1/runs/'+run['run_id']+'/cancel', headers=admin).json()['state'] == 'canceled'
    assert client.post('/api/v1/agent/claim', headers=node, json={}).status_code == 204


def test_queued_reason_reports_dispatch_gate_and_disabled_task(
        client, admin, registered, app):
    run = trigger(client, admin, registered, "dispatch-reason").json()
    with app.state.factory.begin() as db:
        db.get(Gate, 1).dispatch_enabled = False
    paused = client.get('/api/v1/runs/'+run['run_id'], headers=admin).json()
    assert paused['waiting_reason_code'] == 'dispatch_paused'
    with app.state.factory.begin() as db:
        db.get(Gate, 1).dispatch_enabled = True
        db.get(Task, registered['task']['id']).enabled = False
    disabled = client.get('/api/v1/runs/'+run['run_id'], headers=admin).json()
    assert disabled['waiting_reason_code'] == 'task_disabled'


def test_caller_run_detail_hides_internal_dispatch_information(client, admin, registered, project):
    caller = client.post('/api/v1/callers', headers=admin, json={
        "name": "受限上游", "project_id": project['id'], "task_ids": [registered['task']['id']]
    }).json()
    caller_headers = {"Authorization": "Bearer " + caller['token']}
    run = trigger(client, caller_headers, registered, "caller-private-detail").json()
    detail = client.get('/api/v1/runs/' + run['run_id'], headers=caller_headers).json()
    for key in ("parameters", "actor", "node_id", "attempt_id", "waiting_reason", "waiting_reason_code",
                "project_name", "late_evidence"):
        assert key not in detail


@pytest.mark.parametrize("revoke", ["cancel", "task", "caller"])
def test_start_handshake_rechecks_cancellation_and_authorization(client, admin, registered, project, revoke):
    _, node = setup_node(client, admin, project)
    caller = client.post('/api/v1/callers', headers=admin, json={"name": "上游", "project_id": project['id'], "task_ids": [registered['task']['id']]}).json()
    run = trigger(client, {"Authorization": "Bearer "+caller['token']}, registered).json()
    lease = client.post('/api/v1/agent/claim', headers=node, json={}).json()
    if revoke == "cancel":
        client.post('/api/v1/runs/'+run['run_id']+'/cancel', headers=admin)
    elif revoke == "task":
        client.patch('/api/v1/tasks/'+registered['task']['id'], headers=admin, json={"enabled": False})
    else:
        client.patch('/api/v1/callers/'+caller['id'], headers=admin, json={"active": False})
    response = client.post('/api/v1/agent/runs/'+run['run_id']+'/started', headers=node,
                           json={"attempt_id": lease['attempt_id'], "pid": 123})
    assert response.status_code == 409
    assert response.json()['code'] == 'start_revoked'


def test_conflicting_late_result_persists_without_overwriting_terminal(client, admin, registered, project, app):
    _, node = setup_node(client, admin, project)
    run = trigger(client, admin, registered).json()
    lease = client.post('/api/v1/agent/claim', headers=node, json={}).json()
    path = '/api/v1/agent/runs/'+run['run_id']+'/complete'
    payload = {"attempt_id": lease['attempt_id'], "exit_code": 0, "observation": "exited"}
    assert client.post(path, headers=node, json=payload).json()['state'] == 'succeeded'
    payload['exit_code'] = 5
    assert client.post(path, headers=node, json=payload).status_code == 409
    with app.state.factory() as db:
        record = db.get(Run, run['run_id'])
        assert record.state == 'succeeded'
        assert record.late_evidence['exit_code'] == 5
