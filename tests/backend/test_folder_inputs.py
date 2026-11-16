import io
import zipfile
import pytest
from workbench.input_sources import roots_are_subset, validate_directory_parameters
from workbench.security import Problem

SCHEMA = {"type": "object", "additionalProperties": False, "x-workbench-input": "directory",
          "required": ["input_dir", "batch_key"],
          "properties": {"input_dir": {"type": "string"}, "batch_key": {"type": "string"}}}


@pytest.fixture
def directory_setup(client, admin, project):
    program = client.post('/api/v1/programs', headers=admin, json={"name": "目录合成程序", "project_id": project['id']}).json()
    response = client.post(f'/api/v1/programs/{program["id"]}/versions', headers=admin,
                           json={"name": "directory-v1", "command": ["python", "main.py"], "parameter_schema": SCHEMA})
    assert response.status_code == 201
    version = response.json()
    assert 'directory-input-v1' in version['required_capabilities']
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('main.py', "print('directory fixture')")
    assert client.post(f'/api/v1/versions/{version["id"]}/bundle', headers=admin, content=archive.getvalue()).status_code == 200
    assert client.post(f'/api/v1/versions/{version["id"]}/publish', headers=admin).status_code == 200
    node = client.post('/api/v1/nodes', headers=admin, json={"name": "目录测试电脑", "project_id": project['id'],
                      "capabilities": ['python', 'directory-input-v1'],
                      "input_roots": ['D:/输入']}).json()
    task_body = {"name": "目录任务", "project_id": project['id'], "version_id": version['id'], "node_id": node['id']}
    return version, node, task_body


def create_task(client, admin, setup):
    response = client.post('/api/v1/tasks', headers=admin, json=setup[2])
    assert response.status_code == 201
    assert response.json()['input_mode'] == 'directory'
    assert response.json()['business_key_fields'] == ['batch_key']
    return response.json()


@pytest.mark.parametrize('directory', ['relative/path', 'C:relative', '\\relative', 'D:/a/../b', '//?/C:/data',
                                     '//./pipe/name', '/folder/./file', '/dir:stream', '', ' /data', '/data\n'])
def test_invalid_directory_syntax(directory):
    with pytest.raises(Problem):
        validate_directory_parameters({'input_dir': directory, 'batch_key': 'batch-1'})


@pytest.mark.parametrize('directory', ['D:\\数据\\2026-09', '/srv/rpa-input', '\\\\server\\share\\数据'])
def test_absolute_directory_syntax_cross_os(directory):
    validate_directory_parameters({'input_dir': directory, 'batch_key': 'batch-1'})


def test_project_roots_use_real_path_boundaries_and_windows_case_folding():
    assert roots_are_subset(['d:/DATA/project'], ['D:/data'])
    assert not roots_are_subset(['D:/Data2'], ['D:/Data'])
    assert roots_are_subset([r'\\SERVER\Share\project'], [r'\\server\share'])
    assert not roots_are_subset([r'\\server\share2'], [r'\\server\share'])


@pytest.mark.parametrize('key', ['', ' ', 'a\nb', 'x' * 181])
def test_invalid_directory_batch_key(key):
    with pytest.raises(Problem):
        validate_directory_parameters({'input_dir': '/srv/input', 'batch_key': key})


def test_directory_task_node_and_upload_source_constraints(client, admin, directory_setup, project):
    _, _, body = directory_setup
    assert client.post('/api/v1/tasks', headers=admin, json={**body, 'node_id': None}).status_code == 422
    assert client.post('/api/v1/tasks', headers=admin, json={**body, 'requires_input': True}).status_code == 422
    ordinary_node = client.post('/api/v1/nodes', headers=admin, json={'project_id': project['id'], 'name': '旧电脑'}).json()
    assert client.post('/api/v1/tasks', headers=admin, json={**body, 'node_id': ordinary_node['id']}).status_code == 422
    task = create_task(client, admin, directory_setup)
    assert client.get(f'/api/v1/tasks/{task["id"]}', headers=admin).json()['input_mode'] == 'directory'
    assert client.get('/api/v1/tasks', params={'project_id': project['id']}, headers=admin).json()['items'][0]['input_mode'] == 'directory'


def test_directory_manual_reruns_and_old_agent(client, admin, directory_setup):
    task = create_task(client, admin, directory_setup)
    route = f'/api/v1/tasks/{task["id"]}/runs'
    payload = {'event_id': 'folder-event-1', 'parameters': {'input_dir': 'D:/输入/batch1', 'batch_key': 'b1'}}
    assert client.post(route, headers=admin, json={**payload, 'parameters': {}}).status_code == 422
    assert client.post(route, headers=admin, json={**payload, 'input_batch_id': 'other'}).status_code == 422
    outside = client.post(route, headers=admin, json={
        'event_id': 'folder-outside-project',
        'parameters': {'input_dir': 'D:/其他项目/batch1', 'batch_key': 'outside'},
    })
    assert outside.status_code == 422
    assert outside.json()['code'] == 'directory_outside_project_grant'
    accepted = client.post(route, headers=admin, json=payload)
    assert accepted.status_code == 202
    run_id = accepted.json()['run_id']
    repeated = client.post(route, headers=admin, json=payload)
    assert repeated.status_code == 200 and repeated.json()['run_id'] == run_id
    new_event = client.post(route, headers=admin, json={**payload, 'event_id': 'new-event-same-batch'})
    assert new_event.status_code == 202 and new_event.json()['run_id'] != run_id
    conflicting = {**payload, 'event_id': 'changed-directory',
                   'parameters': {**payload['parameters'], 'input_dir': 'D:/输入/different'}}
    changed = client.post(route, headers=admin, json=conflicting)
    assert changed.status_code == 202 and changed.json()['run_id'] not in {run_id, new_event.json()['run_id']}
    node_headers = {'Authorization': 'Bearer ' + directory_setup[1]['token']}
    heartbeat = {'agent_version': '0.1.0', 'isolation': 'development', 'capabilities': ['python']}
    assert client.post('/api/v1/agent/heartbeat', headers=node_headers, json=heartbeat).status_code == 200
    assert client.post('/api/v1/agent/claim', headers=node_headers).status_code == 204
    client.post('/api/v1/agent/heartbeat', headers=node_headers, json={
        **heartbeat, 'capabilities': ['python', 'directory-input-v1', 'node-config-v1'],
        'config_revision': directory_setup[1]['config_revision'],
        'config_hash': directory_setup[1]['config_hash'],
        'reported_input_roots': ['D:/输入'],
    })
    claim = client.post('/api/v1/agent/claim', headers=node_headers)
    assert claim.status_code == 200
    assert claim.json()['input_directory'] == payload['parameters']['input_dir']
    assert claim.json()['input_files'] == []


def test_directory_grant_shrink_blocks_already_leased_start(client, admin, directory_setup):
    task = create_task(client, admin, directory_setup)
    node = directory_setup[1]
    agent = {'Authorization': 'Bearer ' + node['token']}
    assert client.post('/api/v1/agent/heartbeat', headers=agent, json={
        'agent_version': '0.1.0', 'isolation': 'development',
        'capabilities': ['python', 'directory-input-v1', 'node-config-v1'],
        'config_revision': node['config_revision'], 'config_hash': node['config_hash'],
        'reported_input_roots': ['D:/输入'],
    }).status_code == 200
    run = client.post(f'/api/v1/tasks/{task["id"]}/runs', headers=admin, json={
        'event_id': 'shrink-after-lease',
        'parameters': {'input_dir': 'D:/输入/批次一', 'batch_key': 'shrink-one'},
    }).json()
    lease = client.post('/api/v1/agent/claim', headers=agent).json()
    assert lease['run_id'] == run['run_id']
    changed = client.put(f'/api/v1/nodes/{node["id"]}/projects/{directory_setup[1]["project_id"]}',
                         headers=admin, json={
                             'base_revision': node['config_revision'], 'enabled': True,
                             'input_roots': ['D:/输入/其他批次'],
                         })
    assert changed.status_code == 200
    assert client.get(lease['bundle_url'], headers=agent).status_code == 409
    started = client.post(f'/api/v1/agent/runs/{run["run_id"]}/started', headers=agent,
                          json={'attempt_id': lease['attempt_id'], 'pid': 123})
    assert started.status_code == 409
    assert started.json()['code'] == 'start_revoked'


def test_directory_schedule_resolves_batch_and_rejects_platform_inputs(client, admin, directory_setup):
    task = create_task(client, admin, directory_setup)
    payload = {'task_id': task['id'], 'name': '目录定时', 'cron': '0 8 * * *',
               'parameters': {'input_dir': 'D:/输入/每日', 'batch_key': '${scheduled_date}'}}
    assert client.post('/api/v1/schedules', headers=admin, json={**payload, 'input_label': '${scheduled_date}'}).status_code == 422
    assert client.post('/api/v1/schedules', headers=admin, json={**payload, 'input_batch_id': 'other'}).status_code == 422
    response = client.post('/api/v1/schedules', headers=admin, json=payload)
    assert response.status_code == 201
    assert response.json()['parameters']['batch_key'] == '${scheduled_date}'
    assert response.json()['enabled'] is False


def test_directory_version_declaration_rejected_without_required_fields(client, admin, registered):
    program = registered['program']
    for declaration in ({**SCHEMA, 'required': []}, {**SCHEMA, 'x-workbench-input': 'remote'},
                        {**SCHEMA, 'properties': {**SCHEMA['properties'], 'input_dir': {'type': 'number'}}}):
        response = client.post(f'/api/v1/programs/{program["id"]}/versions', headers=admin,
                               json={'name': 'bad-directory', 'command': ['python', 'main.py'], 'parameter_schema': declaration})
        assert response.status_code == 422
    assert client.get(f'/api/v1/tasks/{registered["task"]["id"]}', headers=admin).json()['input_mode'] == 'none'
