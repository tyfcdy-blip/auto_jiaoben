from datetime import datetime, timezone
from sqlalchemy import select
from workbench.db import lock
from workbench.models import Run, Schedule, Task
from workbench.scheduling import scan_health, scan_schedules


def test_future_input_label_waits_then_event_deduplicates(client, admin, registered, project, app):
    with app.state.factory.begin() as db:
        db.get(Task, registered['task']['id']).requires_input = True
    schedule = client.post('/api/v1/schedules', headers=admin, json={
        'task_id': registered['task']['id'], 'name': '等待影刀月度输入', 'cron': '0 8 1 * *',
        'timezone': 'UTC', 'parameters': {'period': '${previous_month}'}, 'input_label': '${previous_month}',
        'misfire_policy': 'once', 'enabled': True})
    assert schedule.status_code == 201
    due = datetime(2026, 9, 1, 8, tzinfo=timezone.utc).timestamp()
    with app.state.factory.begin() as db:
        lock(db)
        db.get(Schedule, schedule.json()['id']).next_fire = due
        scan_schedules(db, app.state.settings, due+1)
    with app.state.factory() as db:
        run = db.scalar(select(Run))
        run_id = run.id
        assert run.state == 'waiting_input'
        assert run.expected_input_label == '2026-08'
        assert run.input_batch_id is None
    batch = client.post('/api/v1/batches', headers=admin, json={'project_id': project['id'], 'label': '2026-08'}).json()
    client.put(f'/api/v1/batches/{batch["id"]}/files/bill.csv', headers=admin, content=b'amount\n2')
    client.post(f'/api/v1/batches/{batch["id"]}/ready', headers=admin)
    with app.state.factory.begin() as db:
        lock(db)
        scan_health(db, app.state.settings, due+2)
    caller = client.post('/api/v1/callers', headers=admin, json={
        'project_id': project['id'], 'name': '影刀下载完成事件', 'task_ids': [registered['task']['id']]
    }).json()
    event_headers = {'Authorization': 'Bearer ' + caller['token']}
    response = client.post('/api/v1/tasks/'+registered['task']['id']+'/runs', headers=event_headers,
                           json={'event_id': 'download-finished', 'parameters': {'period': '2026-08'}, 'input_batch_id': batch['id']})
    assert response.status_code == 200
    assert response.json()['run_id'] == run_id
    assert response.json()['state'] == 'queued'
    with app.state.factory() as db:
        assert db.get(Run, run_id).input_batch_id == batch['id']
    # 不允许同标签悄悄覆盖；修订必须使用新标签，避免等待计划选错输入。
    assert client.post('/api/v1/batches', headers=admin, json={'project_id': project['id'], 'label': '2026-08'}).status_code == 409


def test_external_input_delivery_requires_explicit_project_grant(client, admin, registered, project):
    def caller(name, allowed):
        created = client.post('/api/v1/callers', headers=admin, json={'project_id': project['id'], 'name': name,
            'task_ids': [registered['task']['id']], 'allow_input_upload': allowed})
        assert created.status_code == 201
        return {'Authorization': 'Bearer '+created.json()['token']}
    denied = caller('仅触发', False)
    payload = {'project_id': project['id'], 'label': '外部合成批次'}
    assert client.post('/api/v1/batches', headers=denied, json=payload).status_code == 403
    permitted = caller('授权交付', True)
    batch = client.post('/api/v1/batches', headers=permitted, json=payload)
    assert batch.status_code == 201
    path = '/api/v1/batches/'+batch.json()['id']
    assert client.put(path+'/files/source.csv', headers=permitted, content=b'value\n1').status_code == 200
    assert client.post(path+'/ready', headers=permitted).status_code == 200
    assert client.get('/api/v1/batches', headers=permitted).json()['total'] == 1
    other = client.post('/api/v1/projects', headers=admin, json={'name': '禁止访问的另一个项目'}).json()
    assert client.post('/api/v1/batches', headers=permitted, json={'project_id': other['id'], 'label': '越权'}).status_code == 403
    assert client.get('/api/v1/batches?project_id='+other['id'], headers=permitted).status_code == 403


def test_input_batches_enforce_file_project_and_batch_count_quotas(
        client, admin, project, app):
    settings = app.state.settings
    settings.max_batch_files = 1
    settings.max_batch_bytes = 10
    settings.max_project_input_bytes = 10
    settings.max_project_open_batches = 2
    first = client.post('/api/v1/batches', headers=admin, json={
        'project_id': project['id'], 'label': 'quota-first',
    }).json()
    first_path = f'/api/v1/batches/{first["id"]}/files'
    assert client.put(first_path + '/one.bin', headers=admin, content=b'123456').status_code == 200
    too_many = client.put(first_path + '/two.bin', headers=admin, content=b'1')
    assert too_many.status_code == 413
    assert too_many.json()['code'] == 'batch_input_limit'
    second = client.post('/api/v1/batches', headers=admin, json={
        'project_id': project['id'], 'label': 'quota-second',
    }).json()
    project_full = client.put(f'/api/v1/batches/{second["id"]}/files/two.bin',
                              headers=admin, content=b'12345')
    assert project_full.status_code == 413
    assert project_full.json()['code'] == 'project_input_quota'
    too_many_batches = client.post('/api/v1/batches', headers=admin, json={
        'project_id': project['id'], 'label': 'quota-third',
    })
    assert too_many_batches.status_code == 413
    assert too_many_batches.json()['code'] == 'project_batch_limit'
    assert client.delete(f'/api/v1/batches/{first["id"]}', headers=admin).status_code == 409
    assert client.delete(f'/api/v1/batches/{second["id"]}', headers=admin).status_code == 204
    assert client.post('/api/v1/batches', headers=admin, json={
        'project_id': project['id'], 'label': 'quota-third',
    }).status_code == 201


def test_fixed_batch_and_label_are_mutually_exclusive(client, admin, registered, project):
    response = client.post('/api/v1/schedules', headers=admin, json={'task_id': registered['task']['id'], 'name': '歧义输入',
        'cron': '* * * * *', 'input_batch_id': 'anything', 'input_label': '${scheduled_date}'})
    assert response.status_code == 422
    assert response.json()['code'] == 'ambiguous_input'


def test_input_response_loss_can_be_reconciled_without_overwrite(client, admin, project):
    batch = client.post('/api/v1/batches', headers=admin, json={'project_id': project['id'], 'label': '重复传输'}).json()
    path = '/api/v1/batches/'+batch['id']
    first = client.put(path+'/files/bill.csv', headers=admin, content=b'amount\n8')
    assert first.status_code == 200
    assert client.put(path+'/files/bill.csv', headers=admin, content=b'amount\n8').json()['id'] == first.json()['id']
    assert client.post(path+'/ready', headers=admin).status_code == 200
    assert client.put(path+'/files/bill.csv', headers=admin, content=b'amount\n8').json()['id'] == first.json()['id']
    assert client.put(path+'/files/bill.csv', headers=admin, content=b'amount\n9').status_code == 409
    detail = client.get(path, headers=admin)
    assert detail.status_code == 200
    assert detail.json()['ready'] is True
    assert len(detail.json()['files']) == 1
    assert detail.json()['files'][0]['sha256'] == first.json()['sha256']
