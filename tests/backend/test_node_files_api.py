import time
import pytest
from sqlalchemy import inspect
from workbench.db import initialize
from workbench.models import Node, NodeConfigState, NodeFileRequest, NodeProjectGrant, Task, Version, Run


@pytest.fixture
def files(client, app, admin, project, registered):
    node = client.post('/api/v1/nodes', headers=admin, json={
        'project_id': project['id'], 'name': '文件测试电脑', 'input_roots': ['C:/inputs']}).json()
    headers = {'Authorization': 'Bearer '+node['token']}
    with app.state.factory.begin() as db:
        computer = db.get(Node, node['id'])
        computer.last_seen = time.time()
        computer.capabilities = ['node-config-v1', 'node-files-v1']
        computer.agent_version = '0.1.0'
        computer.maintenance = True
        config = db.get(NodeConfigState, computer.id)
        config.applied_revision, config.applied_hash, config.apply_status = config.desired_revision, config.desired_hash, 'applied'
        task = db.get(Task, registered['task']['id'])
        task.node_id = computer.id
        version = db.get(Version, task.version_id)
        version.parameter_schema = {'type': 'object', 'properties': {
            'source': {'type': 'string', 'x-workbench-file': 'file'},
            'sources': {'type': 'array', 'items': {'type': 'string'}, 'x-workbench-file': 'files'}}, 'additionalProperties': False}
    response = client.put(f'/api/v1/nodes/{node["id"]}/file-configuration', headers=admin, json={
        'base_revision': 0, 'output_roots': ['C:/results'],
        'grants': [{'project_id': project['id'], 'output_roots': ['C:/results']} ]})
    assert response.status_code == 200, response.text
    config = client.get('/api/v1/agent/file-configuration', headers=headers).json()
    assert client.post('/api/v1/agent/file-configuration/ack', headers=headers, json={
        'revision': config['revision'], 'config_hash': config['config_hash'], 'status': 'applied'}).status_code == 200
    with app.state.factory.begin() as db:
        db.get(Node, node['id']).maintenance = False
    return {**registered, 'node': node, 'headers': headers, 'project': project}


def browse(client, admin, files, **values):
    return client.post(f'/api/v1/nodes/{files["node"]["id"]}/file-requests', headers=admin, json={
        'project_id': files['project']['id'], 'purpose': 'input', 'path': 'C:/inputs', **values})


def put_options(client, admin, task_id, options):
    endpoint = f'/api/v1/tasks/{task_id}/file-options'
    base_hash = client.get(endpoint, headers=admin).json()['base_config_hash']
    return client.put(endpoint, headers=admin, json={**options, 'base_config_hash': base_hash})


def test_browse_roundtrip_replay_path_bounds_and_expiry(client, app, admin, files):
    accepted = browse(client, admin, files)
    assert accepted.status_code == 202, accepted.text
    command = client.post('/api/v1/agent/file-requests/claim', headers=files['headers']).json()
    payload = {'config_revision': command['config_revision'], 'output_config_revision': command['output_config_revision'],
               'entries': [{'name': 'test.xlsx', 'path': 'C:/inputs/test.xlsx', 'kind': 'file', 'size': 3}]}
    url = f'/api/v1/agent/file-requests/{command["id"]}/complete'
    assert client.post(url, headers=files['headers'], json=payload).status_code == 200
    assert client.post(url, headers=files['headers'], json=payload).status_code == 200
    assert client.post(url, headers=files['headers'], json={**payload, 'error': 'different'}).status_code == 409
    assert browse(client, admin, files, path='C:/inputs/../private').status_code == 422
    assert browse(client, admin, files, path='C:/inputs-evil').status_code == 422
    with app.state.factory.begin() as db:
        db.get(NodeFileRequest, command['id']).expires_at = time.time()-1
    assert client.post(url, headers=files['headers'], json=payload).status_code == 410


def test_browse_identity_project_revocation_offline_legacy(client, app, admin, files):
    other = client.post('/api/v1/projects', headers=admin, json={'name': '其他隔离项目'}).json()
    assert browse(client, admin, files, project_id=other['id']).status_code == 404
    accepted = browse(client, admin, files).json()
    with app.state.factory.begin() as db:
        grant = db.query(NodeProjectGrant).filter_by(node_id=files['node']['id']).one()
        grant.input_roots = []
    assert client.get(f'/api/v1/nodes/{files["node"]["id"]}/file-requests/{accepted["id"]}', headers=admin).status_code == 403
    with app.state.factory.begin() as db:
        node = db.get(Node, files['node']['id'])
        node.last_seen = time.time()-1000
    assert browse(client, admin, files).json()['code'] == 'node_offline'
    with app.state.factory.begin() as db:
        node = db.get(Node, files['node']['id'])
        node.capabilities = []
    assert browse(client, admin, files).json()['code'] == 'file_agent_unsupported'


def test_file_options_snapshot_mapping_claim_save_retry(client, app, admin, files):
    task_id = files['task']['id']
    options = {'inputs': {'source': 'C:/inputs/a.xlsx'}, 'output_dir': 'C:/results'}
    response = put_options(client, admin, task_id, options)
    assert response.status_code == 200, response.text
    accepted = client.post(f'/api/v1/tasks/{task_id}/runs', headers=admin, json={'event_id': 'files-1'})
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()['run_id']
    claim = client.post('/api/v1/agent/claim', headers=files['headers'])
    assert claim.status_code == 200, claim.text
    assert claim.json()['parameters'] == options['inputs']
    assert claim.json()['node_files']['input_kinds'] == {'source': 'file'}
    attempt = claim.json()['attempt_id']
    payload = {'attempt_id': attempt, 'status': 'failed', 'error': 'permission denied'}
    endpoint = f'/api/v1/agent/runs/{run_id}/file-save'
    assert client.post(endpoint, headers=files['headers'], json=payload).status_code == 200
    with app.state.factory.begin() as db:
        db.get(Run, run_id).state = 'succeeded'
    retry = client.post(f'/api/v1/runs/{run_id}/file-save/retry', headers=admin)
    assert retry.status_code == 202, retry.text
    command = client.post('/api/v1/agent/file-save-retries/claim', headers=files['headers']).json()
    assert command['retry_number'] == 1
    assert client.post(endpoint, headers=files['headers'], json=payload).status_code == 409
    success = {'attempt_id': attempt, 'retry_number': 1, 'status': 'succeeded', 'files': [
        {'name': 'report.txt', 'path': f'C:/results/{run_id}/retry-1/report.txt'}]}
    assert client.post(endpoint, headers=files['headers'], json=success).status_code == 200
    detail = client.get(f'/api/v1/runs/{run_id}', headers=admin).json()
    assert detail['state'] == 'succeeded'
    assert detail['file_status']['save_state'] == 'succeeded'
    assert client.post(endpoint, headers=files['headers'], json=success).status_code == 200


def test_file_mapping_validation_and_atomic_task_configuration(client, app, admin, files):
    task_id = files['task']['id']
    assert put_options(client, admin, task_id, {'inputs': {'wrong': 'C:/inputs/a'}}).status_code == 422
    assert put_options(client, admin, task_id, {'inputs': {'source': ['C:/inputs/a']}}).status_code == 422
    assert put_options(client, admin, task_id, {'inputs': {'sources': ['C:/inputs/a', 'C:/secret/b']}}).status_code == 422
    assert put_options(client, admin, task_id, {'output_dir': 'C:/inputs'}).status_code == 422
    response = client.post('/api/v1/tasks', headers=admin, json={
        'project_id': files['project']['id'], 'name': 'atomic task', 'node_id': files['node']['id'],
        'version_id': files['version']['id'], 'file_options': {'inputs': {'source': 'C:/secret'}}})
    assert response.status_code == 422
    with app.state.factory.begin() as db:
        assert db.query(Task).filter_by(name='atomic task').count() == 0


def test_additive_migration_idempotent(app):
    initialize(app.state.engine, app.state.factory)
    initialize(app.state.engine, app.state.factory)
    assert {'node_file_config', 'node_file_requests', 'task_file_options', 'run_file_options'}.issubset(
        inspect(app.state.engine).get_table_names())


def test_browse_response_scope_pagination_and_limit(client, admin, files):
    assert browse(client, admin, files, limit=201).status_code == 422
    accepted = browse(client, admin, files, limit=1, offset=1).json()
    command = client.post('/api/v1/agent/file-requests/claim', headers=files['headers']).json()
    endpoint = f'/api/v1/agent/file-requests/{accepted["id"]}/complete'
    payload = {'config_revision': command['config_revision'], 'output_config_revision': command['output_config_revision'],
               'entries': [{'name': 'a.txt', 'path': 'C:/private/a.txt', 'kind': 'file'}]}
    assert client.post(endpoint, headers=files['headers'], json=payload).status_code == 422
    payload['entries'][0]['path'] = 'C:/inputs/a.txt'
    payload.update(truncated=True, next_offset=3)
    assert client.post(endpoint, headers=files['headers'], json=payload).status_code == 422
    payload['next_offset'] = 2
    assert client.post(endpoint, headers=files['headers'], json=payload).status_code == 200
    for _ in range(10):
        assert browse(client, admin, files).status_code == 202
    assert browse(client, admin, files).status_code == 429


def test_browse_wrong_agent_and_revision(client, app, admin, files):
    accepted = browse(client, admin, files).json()
    other = client.post('/api/v1/nodes', headers=admin, json={
        'project_id': files['project']['id'], 'name': '其他电脑'}).json()
    endpoint = f'/api/v1/agent/file-requests/{accepted["id"]}/complete'
    payload = {'config_revision': 1, 'output_config_revision': 1}
    assert client.post(endpoint, headers={'Authorization': 'Bearer '+other['token']}, json=payload).status_code == 404
    client.post('/api/v1/agent/file-requests/claim', headers=files['headers'])
    with app.state.factory.begin() as db:
        state = db.get(NodeConfigState, files['node']['id'])
        state.desired_revision += 1
    assert client.post(endpoint, headers=files['headers'], json=payload).status_code == 409


def test_task_options_do_not_change_accepted_runs_and_legacy_claim(client, app, admin, files):
    task_id = files['task']['id']
    accepted = client.post(f'/api/v1/tasks/{task_id}/runs', headers=admin, json={
        'event_id': 'snapshot', 'file_options': {'inputs': {'source': 'C:/inputs/first.xlsx'}}}).json()
    assert put_options(client, admin, task_id, {
        'inputs': {'source': 'C:/inputs/second.xlsx'}}).status_code == 409
    with app.state.factory.begin() as db:
        db.get(Node, files['node']['id']).capabilities = ['node-config-v1']
    assert client.post('/api/v1/agent/claim', headers=files['headers']).status_code == 204
    with app.state.factory.begin() as db:
        db.get(Node, files['node']['id']).capabilities = ['node-config-v1', 'node-files-v1']
    claim = client.post('/api/v1/agent/claim', headers=files['headers']).json()
    assert claim['run_id'] == accepted['run_id']
    assert claim['node_files']['inputs']['source'] == 'C:/inputs/first.xlsx'


def test_output_configuration_subset_and_stale_write(client, app, admin, files):
    endpoint = f'/api/v1/nodes/{files["node"]["id"]}/file-configuration'
    payload = {'base_revision': 1, 'output_roots': ['C:/results'],
               'grants': [{'project_id': files['project']['id'], 'output_roots': ['C:/secret']}]}
    assert client.put(endpoint, headers=admin, json=payload).status_code == 409
    with app.state.factory.begin() as db:
        db.get(Node, files['node']['id']).maintenance = True
    assert client.put(endpoint, headers=admin, json=payload).status_code == 422
    payload['base_revision'] = 0
    assert client.put(endpoint, headers=admin, json=payload).status_code == 409


def test_migration_check_detects_missing_tables_without_changes(app):
    from workbench.models import NodeFileRequest, NodeFileConfig, TaskFileOptions, RunFileOptions, Gate, User
    from workbench.node_files_migration import check
    assert check(app.state.engine, app.state.factory)
    with app.state.factory.begin() as db:
        db.get(Gate, 1).dispatch_enabled = False
        before = db.query(User).count()
    for model in [NodeFileRequest, RunFileOptions, TaskFileOptions, NodeFileConfig]:
        model.__table__.drop(app.state.engine)
    assert not check(app.state.engine, app.state.factory)
    assert 'node_file_requests' not in inspect(app.state.engine).get_table_names()
    initialize(app.state.engine, app.state.factory)
    initialize(app.state.engine, app.state.factory)
    assert check(app.state.engine, app.state.factory)
    with app.state.factory() as db:
        assert db.query(User).count() == before
        assert db.get(Gate, 1).dispatch_enabled is False
        assert db.get(Gate, 1).schema_version == 1


def test_file_options_hash_and_schedule_guard(client, app, admin, files):
    from workbench.models import Schedule
    task_id = files['task']['id']
    endpoint = f'/api/v1/tasks/{task_id}/file-options'
    old = client.get(endpoint, headers=admin).json()['base_config_hash']
    options = {'inputs': {'source': 'C:/inputs/a'}}
    assert put_options(client, admin, task_id, options).status_code == 200
    assert client.put(endpoint, headers=admin, json={**options, 'base_config_hash': old}).status_code == 409
    with app.state.factory.begin() as db:
        db.add(Schedule(project_id=files['project']['id'], task_id=task_id, name='active files schedule',
                        cron='0 0 * * *', timezone='Asia/Shanghai', parameters={}, enabled=True,
                        next_fire=time.time()+3600, misfire_policy='skip'))
    assert put_options(client, admin, task_id, {'inputs': {'source': 'C:/inputs/b'}}).status_code == 409


def test_boolean_schema_and_invalid_files_items():
    from workbench.storage import check_schema
    from workbench.security import Problem
    for child in [True, False]:
        check_schema({'type': 'object', 'properties': {'optional': child}, 'additionalProperties': False})
        with pytest.raises(Problem) as caught:
            check_schema({'type': 'object', 'properties': {'optional': {
                'type': 'array', 'items': child, 'x-workbench-file': 'files'}}, 'additionalProperties': False})
        assert caught.value.status == 422
    for annotation in [[], {}, 1, True]:
        with pytest.raises(Problem) as caught:
            check_schema({'type': 'object', 'properties': {'optional': {
                'type': 'string', 'x-workbench-file': annotation}}, 'additionalProperties': False})
        assert caught.value.status == 422


def test_run_clearing_file_selection_does_not_restore_defaults(client, app, admin, files):
    task_id = files['task']['id']
    with app.state.factory.begin() as db:
        db.get(Task, task_id).default_parameters = {'source': 'C:/inputs/old.xlsx'}
    assert put_options(client, admin, task_id, {'inputs': {'source': 'C:/inputs/old.xlsx'}}).status_code == 200
    accepted = client.post(f'/api/v1/tasks/{task_id}/runs', headers=admin, json={
        'event_id': 'clear-file', 'file_options': {'inputs': {}, 'output_dir': None}})
    assert accepted.status_code == 202
    detail = client.get(f'/api/v1/runs/{accepted.json()["run_id"]}', headers=admin).json()
    assert 'source' not in detail['parameters']
    assert detail['file_status'] is None


def test_file_parameters_from_external_shape_use_same_authorization(client, admin, files):
    endpoint = f'/api/v1/tasks/{files["task"]["id"]}/runs'
    caller = client.post('/api/v1/callers', headers=admin, json={
        'project_id': files['project']['id'], 'name': '文件事件测试', 'task_ids': [files['task']['id']]}).json()
    headers = {'Authorization': 'Bearer '+caller['token']}
    denied = client.post(endpoint, headers=headers, json={
        'event_id': 'external-invalid', 'parameters': {'source': 'C:/private/secret.xlsx'}})
    assert denied.status_code == 422
    accepted = client.post(endpoint, headers=headers, json={
        'event_id': 'external-valid', 'parameters': {'source': 'C:/inputs/report.xlsx'}})
    assert accepted.status_code == 202
    detail = client.get(f'/api/v1/runs/{accepted.json()["run_id"]}', headers=admin).json()
    assert detail['file_status']['inputs'] == {'source': 'C:/inputs/report.xlsx'}


def test_expired_browse_cleanup_is_bounded_and_local(client, app, admin, files):
    from workbench.models import Audit
    accepted = browse(client, admin, files).json()
    with app.state.factory.begin() as db:
        original = db.get(NodeFileRequest, accepted['id'])
        original.expires_at = time.time()-90000
        for _ in range(101):
            db.add(NodeFileRequest(node_id=original.node_id, project_id=original.project_id,
                                  actor=original.actor, purpose='input', path='C:/inputs',
                                  config_revision=1, expires_at=time.time()-90000, state='expired'))
        audit_count = db.query(Audit).count()
    current = browse(client, admin, files)
    assert current.status_code == 202
    with app.state.factory() as db:
        assert db.query(NodeFileRequest).filter(NodeFileRequest.expires_at < time.time()-86400).count() == 2
        assert db.query(NodeFileRequest).filter_by(id=current.json()['id']).count() == 1
        assert db.query(Audit).count() >= audit_count


def test_output_root_management_is_platform_admin_only(client, app, admin, files):
    from workbench.models import NodeFileConfig
    user = client.post('/api/v1/users', headers=admin, json={
        'username': 'file-project-admin', 'password': 'isolated-password-123',
        'display_name': '文件项目管理员', 'is_admin': False})
    assert user.status_code == 201, user.text
    assigned = client.post(f'/api/v1/projects/{files["project"]["id"]}/members', headers=admin, json={
        'user_id': user.json()['id'], 'role': 'project_admin'})
    assert assigned.status_code in {200, 201}, assigned.text
    logged_in = client.post('/api/v1/auth/login', json={
        'username': 'file-project-admin', 'password': 'isolated-password-123'})
    assert logged_in.status_code == 200
    project_admin = {'Authorization': 'Bearer '+logged_in.json()['token']}
    other = client.post('/api/v1/projects', headers=admin, json={'name': '隔离共享文件项目'}).json()
    with app.state.factory.begin() as db:
        db.add(NodeProjectGrant(node_id=files['node']['id'], project_id=other['id'], input_roots=['C:/other-inputs']))
        db.get(Node, files['node']['id']).maintenance = True
    endpoint = f'/api/v1/nodes/{files["node"]["id"]}/file-configuration'
    payload = {'base_revision': 1, 'output_roots': ['C:/results', 'D:/other-results'], 'grants': [
        {'project_id': files['project']['id'], 'output_roots': ['C:/results']},
        {'project_id': other['id'], 'output_roots': ['D:/other-results']}]}
    forbidden = client.put(endpoint, headers=project_admin, json={**payload, 'output_roots': ['C:/']})
    assert forbidden.status_code == 403, forbidden.text
    with app.state.factory() as db:
        assert db.get(NodeFileConfig, files['node']['id']).revision == 1
    updated = client.put(endpoint, headers=admin, json=payload)
    assert updated.status_code == 200, updated.text
    own = client.get(endpoint, headers=project_admin, params={'project_id': files['project']['id']})
    assert own.status_code == 200
    assert own.json()['output_roots'] == [] and own.json()['grants'] == []
    assert own.json()['allowed_output_roots'] == ['C:/results']
    assert 'D:/other-results' not in own.text and other['id'] not in own.text
    assert client.get(endpoint, headers=project_admin, params={'project_id': other['id']}).status_code == 404
    global_view = client.get(endpoint, headers=admin, params={'project_id': files['project']['id']}).json()
    assert global_view['output_roots'] == payload['output_roots']
    assert global_view['grants'] == payload['grants']


def test_clearing_task_file_options_removes_default_file_values(client, app, admin, files):
    task_id = files['task']['id']
    assert put_options(client, admin, task_id, {'inputs': {'source': 'C:/inputs/old.xlsx'}}).status_code == 200
    before = client.get(f'/api/v1/tasks/{task_id}', headers=admin).json()
    assert before['default_parameters']['source'] == 'C:/inputs/old.xlsx'
    cleared = put_options(client, admin, task_id, {'inputs': {}, 'output_dir': None})
    assert cleared.status_code == 200, cleared.text
    after = client.get(f'/api/v1/tasks/{task_id}', headers=admin).json()
    assert 'source' not in after['default_parameters']
    accepted = client.post(f'/api/v1/tasks/{task_id}/runs', headers=admin, json={'event_id': 'task-files-cleared'})
    assert accepted.status_code == 202, accepted.text
    detail = client.get(f'/api/v1/runs/{accepted.json()["run_id"]}', headers=admin).json()
    assert detail['parameters'] == {} and detail['file_status'] is None
    with app.state.factory.begin() as db:
        version = db.get(Version, files['version']['id'])
        version.parameter_schema = {**version.parameter_schema, 'required': ['source']}
    assert client.post(f'/api/v1/tasks/{task_id}/runs', headers=admin,
                       json={'event_id': 'required-file-cleared'}).status_code == 422
