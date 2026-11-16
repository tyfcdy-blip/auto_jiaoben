from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import time
import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from workbench.db import lock
from workbench.execution import alert
from workbench.models import Alert, Channel, Delivery, Run, Schedule, Task, TriggerEvent
from workbench.notifications import send_one, signed_url
from workbench.scheduling import next_fire, resolve_dates, scan_schedules
from workbench.security import Problem


def test_date_resolution_uses_scheduled_time_and_rejects_code():
    timestamp = datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp()
    assert resolve_dates({"period": "${previous_month}", "date": "${scheduled_date}"}, timestamp, "Asia/Shanghai") == {"period": "2026-02", "date": "2026-03-01"}
    with pytest.raises(Problem):
        resolve_dates({"period": "${__import__('os')}"}, timestamp, "UTC")
    with pytest.raises(Problem):
        next_fire('* * * * * *', 'UTC', timestamp)
    with pytest.raises(Problem):
        next_fire('* * * * *', 'America/New_York', timestamp)


def test_schedule_scan_is_durable_and_idempotent(client, admin, registered, app):
    schedule = client.post('/api/v1/schedules', headers=admin, json={"task_id": registered['task']['id'], "name": "定时合成任务",
        "cron": '* * * * *', "timezone": 'UTC', "parameters": {"period": '${previous_month}'}, "misfire_policy": 'once', "enabled": True}).json()
    due = datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp()
    with app.state.factory.begin() as db:
        db.get(Schedule, schedule['id']).next_fire = due
    for _ in range(2):
        with app.state.factory.begin() as db:
            lock(db)
            scan_schedules(db, app.state.settings, due+120)
    with app.state.factory() as db:
        runs = db.scalars(select(Run)).all()
        assert len(runs) == 1
        assert runs[0].parameters['period'] == '2026-02'
        assert runs[0].source == 'schedule'
        assert db.get(Schedule, schedule['id']).next_fire > due+120
        assert len(db.scalars(select(TriggerEvent)).all()) == 1


def test_schedule_skip_and_dispatch_disabled(client, admin, registered, app):
    item = client.post('/api/v1/schedules', headers=admin, json={"task_id": registered['task']['id'], "name": "跳过漏触发", "cron": '* * * * *', "misfire_policy": 'skip', "enabled": True}).json()
    due = time.time()-3600
    with app.state.factory.begin() as db:
        db.get(Schedule, item['id']).next_fire = due
        app.state.settings.dispatch_enabled = False
        scan_schedules(db, app.state.settings)
        assert db.get(Schedule, item['id']).next_fire == due
        app.state.settings.dispatch_enabled = True
        scan_schedules(db, app.state.settings)
        skipped = db.get(Schedule, item['id'])
        assert skipped.next_fire > time.time()
        assert skipped.last_error == '错过计划时间，已按“跳过”策略顺延至下一次'
        missed = db.scalar(select(Alert).where(Alert.kind == 'schedule_misfire'))
        assert missed is not None
        assert missed.event_key.startswith(f"schedule-misfire:{item['id']}:")
        assert item['name'] in missed.message and item['id'] in missed.message
    assert client.get('/api/v1/runs', headers=admin).json()['total'] == 0


def test_schedule_success_clears_and_recovers_previous_misfire(client, admin, registered, app):
    item = client.post('/api/v1/schedules', headers=admin, json={
        "task_id": registered['task']['id'], "name": "漏触发后恢复", "cron": '* * * * *',
        "misfire_policy": 'skip', "enabled": True,
    }).json()
    now = time.time()
    with app.state.factory.begin() as db:
        schedule = db.get(Schedule, item['id'])
        schedule.next_fire = now - 120
        scan_schedules(db, app.state.settings, now)
        schedule.next_fire = now + 1
    with app.state.factory.begin() as db:
        scan_schedules(db, app.state.settings, now + 2)
    with app.state.factory() as db:
        schedule = db.get(Schedule, item['id'])
        missed = db.scalar(select(Alert).where(Alert.kind == 'schedule_misfire'))
        recovery = db.scalar(select(Alert).where(Alert.kind == 'recovery'))
        assert schedule.last_error is None
        assert missed.status == 'recovered'
        assert recovery is not None
        run = db.scalar(select(Run).where(Run.actor == f"schedule:{item['id']}"))
        assert run is not None and run.source == 'schedule'


def test_schedule_recovery_delivery_waits_for_misfire_delivery(client, admin, registered, project, app):
    config = {"url": 'https://oapi.dingtalk.com/robot/send?access_token=SYNTHETIC', "secret": ''}
    with app.state.factory.begin() as db:
        db.add(Channel(
            project_id=project['id'], name='计划告警通道', enabled=True,
            encrypted_config=Fernet(app.state.settings.secret_key.encode()).encrypt(
                json.dumps(config).encode()).decode(),
        ))
    item = client.post('/api/v1/schedules', headers=admin, json={
        "task_id": registered['task']['id'], "name": "每日库存同步", "cron": '* * * * *',
        "misfire_policy": 'skip', "enabled": True,
    }).json()
    now = time.time()
    with app.state.factory.begin() as db:
        schedule = db.get(Schedule, item['id'])
        schedule.next_fire = now - 120
        scan_schedules(db, app.state.settings, now)
        missed = db.scalar(select(Alert).where(Alert.kind == 'schedule_misfire'))
        missed_delivery = db.scalar(select(Delivery).where(Delivery.alert_id == missed.id))
        missed_delivery.state = 'retry'
        missed_delivery.next_try = now + 30
        schedule.next_fire = now + 1
    with app.state.factory.begin() as db:
        scan_schedules(db, app.state.settings, now + 2)

    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content)["text"]["content"])
        return httpx.Response(200, json={"errcode": 0})

    with httpx.Client(transport=httpx.MockTransport(respond)) as mock_client:
        assert send_one(app.state.factory, app.state.settings, mock_client, now + 2)
        assert payloads == []
        assert send_one(app.state.factory, app.state.settings, mock_client, now + 31)
        assert send_one(app.state.factory, app.state.settings, mock_client, now + 32)
    assert len(payloads) == 2
    assert 'schedule_misfire' in payloads[0]
    assert '每日库存同步' in payloads[0] and item['id'] in payloads[0]
    assert 'recovery' in payloads[1]
    assert '每日库存同步' in payloads[1] and item['id'] in payloads[1]


def test_queue_full_does_not_advance_schedule_cursor(client, admin, registered, app):
    app.state.settings.max_queue = 1
    client.post('/api/v1/tasks/'+registered['task']['id']+'/runs', headers=admin, json={"event_id": "fill"})
    item = client.post('/api/v1/schedules', headers=admin, json={"task_id": registered['task']['id'], "name": "等待容量", "cron": '* * * * *', "misfire_policy": 'once', "enabled": True}).json()
    due = time.time()-10
    with app.state.factory.begin() as db:
        db.get(Schedule, item['id']).next_fire = due
        scan_schedules(db, app.state.settings)
    with app.state.factory() as db:
        assert db.get(Schedule, item['id']).next_fire == due
        assert db.get(Schedule, item['id']).last_error


def test_dingtalk_encryption_retry_and_no_business_rerun(client, admin, registered, project, app):
    config = {"url": 'https://oapi.dingtalk.com/robot/send?access_token=SYNTHETIC', "secret": 'SYNTHETIC-ONLY'}
    with app.state.factory.begin() as db:
        channel = Channel(project_id=project['id'], name='模拟通知通道', enabled=True,
                          encrypted_config=Fernet(app.state.settings.secret_key.encode()).encrypt(json.dumps(config).encode()).decode())
        db.add(channel)
        db.flush()
        item = alert(db, project['id'], 'synthetic_failure', '合成错误', 'synthetic-event')
        identifier = item.id
    clock = time.time()+1
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"errcode": 45009}))
    with httpx.Client(transport=transport) as mock_client:
        assert send_one(app.state.factory, app.state.settings, mock_client, clock)
    with app.state.factory() as db:
        delivery = db.scalar(select(Delivery).where(Delivery.alert_id == identifier))
        assert delivery.state == 'retry'
        next_attempt = delivery.next_try
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"errcode": 0}))
    with httpx.Client(transport=transport) as mock_client:
        send_one(app.state.factory, app.state.settings, mock_client, next_attempt+1)
    with app.state.factory() as db:
        assert db.scalar(select(Delivery).where(Delivery.alert_id == identifier)).state == 'sent'
        assert db.scalars(select(Run)).all() == []
    response = client.get('/api/v1/channels', headers=admin)
    assert 'SYNTHETIC' not in response.text and 'encrypted_config' not in response.text
    assert 'sign=' in signed_url(config['url'], config['secret'], clock)


def test_channels_reject_ssrf_and_monitor_is_separate(client, admin, project, app):
    response = client.post('/api/v1/channels', headers=admin, json={"project_id": project['id'], "name": '非法', "webhook_url": 'http://127.0.0.1/secret'})
    assert response.status_code == 422
    assert client.post('/api/v1/channels', headers=admin, json={'project_id': project['id'], 'name': '非法端口',
        'webhook_url': 'https://oapi.dingtalk.com:invalid/robot/send'}).status_code == 422
    assert client.get('/api/v1/health/components', headers=admin).status_code == 401
    assert client.get('/api/v1/health/live').status_code == 200
    assert client.get('/api/v1/health/ready').status_code == 200


def test_postgres_concurrent_event_admission(client, admin, registered, app):
    if app.state.engine.dialect.name != 'postgresql':
        pytest.skip('并发门禁必须在真实PostgreSQL执行')
    path = '/api/v1/tasks/'+registered['task']['id']+'/runs'
    def submit(_):
        return client.post(path, headers=admin, json={"event_id": "concurrent-same-event"})
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(submit, range(12)))
    assert sum(response.status_code == 202 for response in responses) == 1
    assert all(response.status_code in {200, 202} for response in responses)
    assert len({response.json()['run_id'] for response in responses}) == 1


def test_postgres_concurrent_claims_respect_shared_resources(client, admin, registered, project, app):
    if app.state.engine.dialect.name != 'postgresql':
        pytest.skip('并发门禁必须在真实PostgreSQL执行')
    with app.state.factory.begin() as db:
        db.get(Task, registered['task']['id']).resource_keys = ['table-a', 'shared-number']
    headers = []
    for index in range(6):
        client.post('/api/v1/tasks/'+registered['task']['id']+'/runs', headers=admin, json={"event_id": f"resource-{index}"})
        node = client.post('/api/v1/nodes', headers=admin, json={"project_id": project['id'], "name": f"竞争节点-{index}"}).json()
        auth = {"Authorization": 'Bearer '+node['token']}
        client.post('/api/v1/agent/heartbeat', headers=auth, json={"capabilities": [], "isolation": 'development', "agent_version": '0.1.0'})
        headers.append(auth)
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda auth: client.post('/api/v1/agent/claim', headers=auth, json={}), headers))
    assert sum(response.status_code == 200 for response in responses) == 1
    assert sum(response.status_code == 204 for response in responses) == 5
