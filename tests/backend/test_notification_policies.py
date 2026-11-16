import json
import os
import time
import uuid
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url

from workbench.config import Settings
from workbench.db import database, initialize
from workbench.execution import alert, recover_alert
from workbench.models import (Alert, Base, Channel, Delivery, Program, Project,
                              ResultNotification, Run, Task, Version)
from workbench.notifications import send_one


def create_channel(client, admin, project_id, name):
    response = client.post('/api/v1/channels', headers=admin, json={
        'project_id': project_id, 'name': name,
        'webhook_url': 'https://oapi.dingtalk.com/robot/send?access_token=SYNTHETIC',
        'signing_secret': None,
    })
    assert response.status_code == 201, response.text
    channel = response.json()
    assert client.patch('/api/v1/channels/'+channel['id'], headers=admin,
                        json={'enabled': True}).status_code == 200
    return channel


def test_result_protocol_accepts_optional_script_text_with_bounds():
    schema_path = Path(__file__).resolve().parents[2] / 'contracts/runtime/result.schema.json'
    validator = Draft202012Validator(json.loads(schema_path.read_text(encoding='utf-8')))
    result = {'protocol_version': '1', 'run_id': 'run', 'attempt_id': 'attempt',
              'status': 'success', 'summary': '内部摘要'}
    assert validator.is_valid(result)
    assert validator.is_valid({**result, 'notification_text': '给业务群的简短结论'})
    for invalid in ('', '   ', 'x'*2001, 123, {'text': '越界'}):
        assert not validator.is_valid({**result, 'notification_text': invalid})


def test_alert_severity_type_routing_recovery_and_legacy_channel(client, admin, project, app):
    channel = create_channel(client, admin, project['id'], '新规则连接')
    policy_url = '/api/v1/channels/'+channel['id']+'/policy'
    default = client.get(policy_url, headers=admin).json()
    assert default['revision'] == 1 and not default['legacy_all']
    assert 'schedule_delayed' not in default['enabled_kinds']
    assert client.put(policy_url, headers=admin, json={
        'base_revision': 1, 'enabled_kinds': ['failed'],
    }).status_code == 200
    assert client.put(policy_url, headers=admin, json={
        'base_revision': 1, 'enabled_kinds': ['unknown'],
    }).status_code == 409
    assert client.put(policy_url, headers=admin, json={
        'base_revision': 2, 'enabled_kinds': ['failed', 'failed'],
    }).status_code == 422

    with app.state.factory.begin() as db:
        legacy = Channel(project_id=project['id'], name='旧连接', enabled=True,
                         encrypted_config=Fernet(app.state.settings.secret_key.encode()).encrypt(
                             json.dumps({'url': 'https://oapi.dingtalk.com/robot/send?access_token=OLD',
                                         'secret': ''}).encode()).decode())
        db.add(legacy)
        db.flush()
        old_id = legacy.id
        delayed = alert(db, project['id'], 'schedule_delayed', '排队测试', 'policy-delay')
        failure = alert(db, project['id'], 'failed', '失败测试', 'policy-failed')
        unknown = alert(db, project['id'], 'unknown', '待核对测试', 'policy-unknown')
        recover_alert(db, unknown, '条件已恢复')
        delayed_id, failure_id, unknown_id = delayed.id, failure.id, unknown.id
    with app.state.factory() as db:
        deliveries = db.scalars(select(Delivery)).all()
        pairs = {(entry.alert_id, entry.channel_id) for entry in deliveries}
        assert (delayed_id, channel['id']) not in pairs
        assert (failure_id, channel['id']) in pairs
        assert (unknown_id, channel['id']) not in pairs
        recovery_id = db.scalar(select(Alert.id).where(Alert.kind == 'recovery'))
        assert (recovery_id, channel['id']) not in pairs
        assert (recovery_id, old_id) in pairs
    rows = client.get('/api/v1/alerts', headers=admin).json()['items']
    assert {row['kind']: row['severity'] for row in rows}['unknown'] == 'critical'
    assert {row['kind']: row['severity'] for row in rows}['failed'] == 'warning'
    assert {row['kind']: row['severity'] for row in rows}['recovery'] == 'info'
    detail = client.get('/api/v1/alert-deliveries', headers=admin).json()
    assert detail['total'] == len(deliveries)
    assert all('encrypted_config' not in row for row in detail['items'])
    assert client.get('/api/v1/channels/'+old_id+'/policy', headers=admin).json()['legacy_all']


def test_success_result_is_separate_redacted_idempotent_and_delivered(client, admin, project, registered, app):
    channel = create_channel(client, admin, project['id'], '结果群')
    task_id = registered['task']['id']
    config_url = '/api/v1/tasks/'+task_id+'/result-notification'
    assert client.get(config_url, headers=admin).json()['revision'] == 0
    updated = client.put(config_url, headers=admin, json={
        'base_revision': 0, 'channel_id': channel['id'], 'enabled': True,
    })
    assert updated.status_code == 200 and updated.json()['revision'] == 1
    assert client.put(config_url, headers=admin, json={
        'base_revision': 0, 'channel_id': channel['id'], 'enabled': False,
    }).status_code == 409
    rules = client.get('/api/v1/task-notification-rules', headers=admin).json()
    assert rules['total'] == 1
    assert rules['items'][0]['notification_enabled'] is True
    assert rules['items'][0]['channel_name'] == '结果群'
    assert 'encrypted_config' not in rules['items'][0]

    node = client.post('/api/v1/nodes', headers=admin,
                       json={'project_id': project['id'], 'name': '结果测试电脑'}).json()
    node_auth = {'Authorization': 'Bearer '+node['token']}
    assert client.post('/api/v1/agent/heartbeat', headers=node_auth, json={
        'capabilities': [], 'isolation': 'development', 'agent_version': '0.1.0',
    }).status_code == 200
    run_id = client.post('/api/v1/tasks/'+task_id+'/runs', headers=admin, json={
        'event_id': 'result-notification-one', 'parameters': {'period': '2026-09'},
    }).json()['run_id']
    lease = client.post('/api/v1/agent/claim', headers=node_auth, json={}).json()
    assert lease['run_id'] == run_id
    report = {'protocol_version': '1', 'run_id': run_id, 'attempt_id': lease['attempt_id'],
              'status': 'success', 'summary': '合并完成 3 个文件；token=do-not-send'}
    completion = {'attempt_id': lease['attempt_id'], 'exit_code': 0,
                  'observation': 'exited', 'result': report}
    endpoint = f'/api/v1/agent/runs/{run_id}/complete'
    for _ in range(2):
        response = client.post(endpoint, headers=node_auth, json=completion)
        assert response.status_code == 200 and response.json()['state'] == 'succeeded'
    notices = client.get('/api/v1/result-notifications', headers=admin).json()
    assert notices['total'] == 1
    notice = notices['items'][0]
    assert notice['run_id'] == run_id and notice['delivery_state'] == 'pending'
    assert notice['channel_name'] == '结果群'
    assert 'do-not-send' not in notice['content']
    assert '2026-09' not in notice['content']
    assert client.get('/api/v1/alerts', headers=admin).json()['total'] == 0

    sent = []
    def respond(request):
        sent.append(json.loads(request.content)['text']['content'])
        return httpx.Response(200, json={'errcode': 0})
    with httpx.Client(transport=httpx.MockTransport(respond)) as mock_client:
        assert send_one(app.state.factory, app.state.settings, mock_client, time.time()+2)
        assert not send_one(app.state.factory, app.state.settings, mock_client, time.time()+3)
    assert len(sent) == 1 and run_id in sent[0]
    assert 'do-not-send' not in sent[0]
    with app.state.factory() as db:
        assert db.scalar(select(ResultNotification).where(ResultNotification.run_id == run_id)).delivery_state == 'sent'
        assert db.get(Run, run_id).state == 'succeeded'

    failed_run_id = client.post('/api/v1/tasks/'+task_id+'/runs', headers=admin, json={
        'event_id': 'result-notification-failed', 'parameters': {'period': '2026-10'},
    }).json()['run_id']
    failed_lease = client.post('/api/v1/agent/claim', headers=node_auth, json={}).json()
    failed_report = {'protocol_version': '1', 'run_id': failed_run_id,
                     'attempt_id': failed_lease['attempt_id'], 'status': 'failed', 'summary': '业务处理失败'}
    failed_completion = client.post(f'/api/v1/agent/runs/{failed_run_id}/complete', headers=node_auth,
                                    json={'attempt_id': failed_lease['attempt_id'], 'exit_code': 1,
                                          'observation': 'exited', 'result': failed_report})
    assert failed_completion.status_code == 200 and failed_completion.json()['state'] == 'failed'
    assert client.get('/api/v1/result-notifications', headers=admin).json()['total'] == 1
    assert client.get('/api/v1/alerts', headers=admin).json()['total'] == 1


def test_result_channel_must_belong_to_task_project(client, admin, registered):
    other = client.post('/api/v1/projects', headers=admin, json={'name': '第二个通知项目'}).json()
    channel = create_channel(client, admin, other['id'], '其他项目连接')
    url = '/api/v1/tasks/'+registered['task']['id']+'/result-notification'
    response = client.put(url, headers=admin, json={
        'base_revision': 0, 'channel_id': channel['id'], 'enabled': True,
    })
    assert response.status_code == 422
    assert client.put(url, headers=admin, json={
        'base_revision': 0, 'channel_id': None, 'enabled': True,
    }).status_code == 422


def test_script_message_uses_platform_channel_template_and_rejects_invalid_text(
        client, admin, project, registered, app):
    app.state.settings.public_base_url = 'https://workbench.example.invalid'
    app.state.settings.base_path = '/automation'
    channel = create_channel(client, admin, project['id'], '脚本消息测试群')
    task_id = registered['task']['id']
    assert client.put(f'/api/v1/tasks/{task_id}/result-notification', headers=admin, json={
        'base_revision': 0, 'channel_id': channel['id'], 'enabled': True,
    }).status_code == 200
    node = client.post('/api/v1/nodes', headers=admin,
                       json={'project_id': project['id'], 'name': '消息测试电脑'}).json()
    node_auth = {'Authorization': 'Bearer '+node['token']}
    assert client.post('/api/v1/agent/heartbeat', headers=node_auth, json={
        'capabilities': [], 'isolation': 'development', 'agent_version': '0.1.0',
    }).status_code == 200

    def finish(event_id, message):
        run_id = client.post(f'/api/v1/tasks/{task_id}/runs', headers=admin, json={
            'event_id': event_id, 'parameters': {'period': '2026-09'},
        }).json()['run_id']
        lease = client.post('/api/v1/agent/claim', headers=node_auth, json={}).json()
        report = {'protocol_version': '1', 'run_id': run_id, 'attempt_id': lease['attempt_id'],
                  'status': 'success', 'summary': '内部摘要不应进入群',
                  'notification_text': message}
        body = {'attempt_id': lease['attempt_id'], 'exit_code': 0,
                'observation': 'exited', 'result': report}
        endpoint = f'/api/v1/agent/runs/{run_id}/complete'
        response = client.post(endpoint, headers=node_auth, json=body)
        assert client.post(endpoint, headers=node_auth, json=body).status_code == 200
        return run_id, response

    run_id, response = finish('script-message-valid', '已处理 3 份文件，token=hidden-value')
    assert response.status_code == 200 and response.json()['state'] == 'succeeded'
    entries = client.get('/api/v1/result-notifications', headers=admin).json()
    assert entries['total'] == 1
    content = entries['items'][0]['content']
    assert '已处理 3 份文件' in content and run_id in content
    assert f'https://workbench.example.invalid/automation/#/runs/{run_id}' in content
    assert '内部摘要' not in content and 'hidden-value' not in content
    sent = []
    def respond(request):
        sent.append(json.loads(request.content)['text']['content'])
        return httpx.Response(200, json={'errcode': 0})
    with httpx.Client(transport=httpx.MockTransport(respond)) as mock_client:
        assert send_one(app.state.factory, app.state.settings, mock_client, time.time()+2)
    assert sent == [content]

    invalid_id, invalid = finish('script-message-invalid', 123)
    assert invalid.status_code == 200 and invalid.json()['state'] == 'failed'
    assert client.get('/api/v1/result-notifications', headers=admin).json()['total'] == 1
    with app.state.factory() as db:
        assert db.get(Run, invalid_id).error == '结果协议或运行编号不匹配'


def test_result_delivery_retry_does_not_rerun_business(client, admin, project, registered, app):
    channel = create_channel(client, admin, project['id'], '失败重试测试群')
    task_id = registered['task']['id']
    assert client.put('/api/v1/tasks/'+task_id+'/result-notification', headers=admin, json={
        'base_revision': 0, 'channel_id': channel['id'], 'enabled': True,
    }).status_code == 200
    with app.state.factory.begin() as db:
        run = Run(project_id=project['id'], task_id=task_id, version_id=registered['version']['id'],
                  actor='synthetic', source='manual', parameters={}, business_key='synthetic-retry',
                  state='succeeded')
        db.add(run)
        db.flush()
        notice = ResultNotification(project_id=project['id'], task_id=task_id, run_id=run.id,
                                    channel_id=channel['id'], content='合成成功结果')
        db.add(notice)
        run_id = run.id
    now = time.time()+2
    with httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={'errcode': 45009}))) as mock_client:
        assert send_one(app.state.factory, app.state.settings, mock_client, now)
    with app.state.factory() as db:
        notice = db.scalar(select(ResultNotification).where(ResultNotification.run_id == run_id))
        assert notice.delivery_state == 'retry' and notice.attempts == 1
        assert '45009' in notice.last_error
        retry_at = notice.next_try
    with httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={'errcode': 0}))) as mock_client:
        assert send_one(app.state.factory, app.state.settings, mock_client, retry_at+1)
    with app.state.factory() as db:
        assert db.get(Run, run_id).state == 'succeeded'
        assert db.scalar(select(ResultNotification).where(ResultNotification.run_id == run_id)).delivery_state == 'sent'
        assert len(db.scalars(select(Run)).all()) == 1


def test_failure_summary_is_redacted_before_alert_and_group_message(client, admin, project, app):
    create_channel(client, admin, project['id'], '错误摘要测试群')
    with app.state.factory.begin() as db:
        event = alert(db, project['id'], 'failed', '处理失败 token=private-value', 'redact-error')
        alert_id = event.id
    with app.state.factory() as db:
        assert 'private-value' not in db.get(Alert, alert_id).message
    payloads = []
    def respond(request):
        payloads.append(json.loads(request.content)['text']['content'])
        return httpx.Response(200, json={'errcode': 0})
    with httpx.Client(transport=httpx.MockTransport(respond)) as mock_client:
        assert send_one(app.state.factory, app.state.settings, mock_client, time.time()+2)
    assert len(payloads) == 1 and 'private-value' not in payloads[0]


def test_new_notification_tables_initialize_without_altering_existing_tables(tmp_path):
    connection_file = os.environ.get('WORKBENCH_TEST_DATABASE_FILE')
    maintenance, schema = None, None
    database_url = 'sqlite:///'+str(tmp_path/'old.db')
    if connection_file:
        database_url = json.loads(Path(connection_file).read_text(encoding='utf-8'))['database_url']
        maintenance = create_engine(database_url)
        schema = 'test_notify_migration_'+uuid.uuid4().hex
        with maintenance.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        database_url = make_url(database_url).update_query_dict(
            {'options': '-csearch_path='+schema},
        ).render_as_string(hide_password=False)
    settings = Settings(env='test', database_url=database_url,
                        data_dir=tmp_path/'storage', secret_key=Fernet.generate_key().decode())
    engine, factory = database(settings)
    added = {'channel_alert_policies', 'task_result_notification_configs', 'result_notifications'}
    try:
        Base.metadata.create_all(engine, tables=[table for table in Base.metadata.sorted_tables
                                                  if table.name not in added])
        with factory.begin() as db:
            db.add(Project(name='既有项目'))
        initialize(engine, factory)
        assert added.issubset(set(inspect(engine).get_table_names()))
        with factory() as db:
            assert db.scalar(select(Project).where(Project.name == '既有项目')) is not None
        initialize(engine, factory)
    finally:
        engine.dispose()
        if maintenance:
            with maintenance.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            maintenance.dispose()


def test_notification_records_are_project_scoped_and_viewer_cannot_edit(
        client, admin, project, registered, app):
    own_channel = create_channel(client, admin, project['id'], '本项目群')
    other = client.post('/api/v1/projects', headers=admin, json={'name': '其他通知项目'}).json()
    other_channel = create_channel(client, admin, other['id'], '其他项目群')
    user = client.post('/api/v1/users', headers=admin, json={
        'username': 'notice-viewer', 'password': 'viewer-password-123',
        'display_name': '通知只读用户',
    }).json()
    assert client.post('/api/v1/projects/'+project['id']+'/members', headers=admin, json={
        'user_id': user['id'], 'role': 'viewer',
    }).status_code == 200
    login = client.post('/api/v1/auth/login', json={
        'username': 'notice-viewer', 'password': 'viewer-password-123',
    }).json()
    viewer = {'Authorization': 'Bearer '+login['token']}
    with app.state.factory.begin() as db:
        alert(db, project['id'], 'failed', '本项目异常', 'viewer-own')
        alert(db, other['id'], 'failed', '其他项目异常', 'viewer-other')
        other_program = Program(project_id=other['id'], name='其他项目程序')
        db.add(other_program)
        db.flush()
        other_version = Version(program_id=other_program.id, name='v1', command=['python', 'main.py'],
                                parameter_schema={'type': 'object'}, published=True)
        db.add(other_version)
        db.flush()
        other_task = Task(project_id=other['id'], version_id=other_version.id, name='其他项目任务')
        db.add(other_task)
        db.flush()
        for owner_id, task_id, version_id, channel_id, key in [
            (project['id'], registered['task']['id'], registered['version']['id'], own_channel['id'], 'own'),
            (other['id'], other_task.id, other_version.id, other_channel['id'], 'other'),
        ]:
            run = Run(project_id=owner_id, task_id=task_id, version_id=version_id,
                      actor='synthetic', source='manual', parameters={}, business_key=key, state='succeeded')
            db.add(run)
            db.flush()
            db.add(ResultNotification(project_id=owner_id, task_id=task_id, run_id=run.id,
                                      channel_id=channel_id, content='合成结果'))
    deliveries = client.get('/api/v1/alert-deliveries', headers=viewer).json()
    results = client.get('/api/v1/result-notifications', headers=viewer).json()
    assert deliveries['total'] == results['total'] == 1
    rules = client.get('/api/v1/task-notification-rules', headers=viewer).json()
    assert rules['total'] == 1 and rules['items'][0]['project_id'] == project['id']
    assert deliveries['items'][0]['project_id'] == project['id']
    assert results['items'][0]['project_id'] == project['id']
    assert client.get('/api/v1/result-notifications', headers=viewer,
                      params={'project_id': other['id']}).json()['total'] == 0
    assert client.get('/api/v1/channels/'+other_channel['id']+'/policy', headers=viewer).status_code == 404
    assert client.put('/api/v1/tasks/'+registered['task']['id']+'/result-notification', headers=viewer,
                      json={'base_revision': 0, 'channel_id': own_channel['id'], 'enabled': True}).status_code == 403
