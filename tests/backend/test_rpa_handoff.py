"""合成影刀调用方的受理、结果和产物交付链路。"""
import hashlib
import pytest
from workbench.config import Settings


def caller(client, admin, project, task_id, name):
    created = client.post('/api/v1/callers', headers=admin, json={
        'name': name, 'project_id': project['id'], 'task_ids': [task_id],
    })
    assert created.status_code == 201
    return created.json(), {'Authorization': 'Bearer ' + created.json()['token']}


def test_rpa_trigger_result_and_file_handoff(client, admin, project, registered):
    task_id = registered['task']['id']
    first, first_auth = caller(client, admin, project, task_id, '合成影刀-A')
    _, other_auth = caller(client, admin, project, task_id, '合成影刀-B')
    endpoint = f'/api/v1/tasks/{task_id}/runs'
    payload = {'event_id': 'synthetic-delivery-001', 'parameters': {'period': '2026-08'}}

    accepted = client.post(endpoint, headers=first_auth, json=payload)
    repeated = client.post(endpoint, headers=first_auth, json=payload)
    assert accepted.status_code == 202
    assert repeated.status_code == 200
    run_id = accepted.json()['run_id']
    assert repeated.json()['run_id'] == run_id
    assert accepted.json()['accepted'] is True

    node = client.post('/api/v1/nodes', headers=admin, json={
        'name': '合成执行电脑', 'project_id': project['id'],
    }).json()
    node_auth = {'Authorization': 'Bearer ' + node['token']}
    assert client.post('/api/v1/agent/heartbeat', headers=node_auth, json={
        'capabilities': [], 'isolation': 'development', 'agent_version': '0.1.0',
    }).status_code == 200
    claim = client.post('/api/v1/agent/claim', headers=node_auth, json={}).json()
    assert claim['run_id'] == run_id
    assert claim['parameters'] == {'period': '2026-08'}
    attempt_id = claim['attempt_id']
    assert client.post(f'/api/v1/agent/runs/{run_id}/started', headers=node_auth,
                       json={'attempt_id': attempt_id, 'pid': 123}).status_code == 200

    content = b'synthetic result bytes\n'
    uploaded = client.post(f'/api/v1/agent/runs/{run_id}/artifacts/result.txt',
                           params={'attempt_id': attempt_id}, headers=node_auth,
                           content=content)
    assert uploaded.status_code == 200
    report = {
        'protocol_version': '1', 'run_id': run_id, 'attempt_id': attempt_id,
        'status': 'success', 'summary': '合成处理完成',
        'metrics': {'file_count': 1}, 'artifacts': [{'name': 'result.txt', 'required': True}],
    }
    complete = client.post(f'/api/v1/agent/runs/{run_id}/complete', headers=node_auth,
                           json={'attempt_id': attempt_id, 'exit_code': 0,
                                 'observation': 'exited', 'artifact_state': 'complete',
                                 'result': report})
    assert complete.status_code == 200
    assert complete.json()['state'] == 'succeeded'

    detail = client.get(f'/api/v1/runs/{run_id}', headers=first_auth)
    assert detail.status_code == 200
    assert detail.json()['state'] == 'succeeded'
    assert detail.json()['result'] == report
    artifact = detail.json()['artifacts'][0]
    assert artifact['name'] == 'result.txt'
    assert artifact['sha256'] == hashlib.sha256(content).hexdigest()
    assert artifact['size'] == len(content)
    path = f'/api/v1/runs/{run_id}/artifacts/{artifact["id"]}'
    downloaded = client.get(path, headers=first_auth)
    assert downloaded.status_code == 200
    assert downloaded.content == content

    assert client.get(f'/api/v1/runs/{run_id}', headers=other_auth).status_code == 404
    assert client.get(path, headers=other_auth).status_code == 404
    assert client.get(path, headers=node_auth).status_code == 403
    assert client.get(f'/api/v1/runs/other-run/artifacts/{artifact["id"]}',
                      headers=first_auth).status_code == 404
    assert client.patch(f'/api/v1/callers/{first["id"]}', headers=admin,
                        json={'active': False}).status_code == 200
    assert client.get(path, headers=first_auth).status_code == 401


@pytest.mark.parametrize('url', [
    'http://workbench.example.invalid',
    'https://workbench.example.invalid/path',
    'https://user:pass@workbench.example.invalid',
    'https://workbench.example.invalid/?x=1',
    'https://workbench.example.invalid/#/runs',
    'https://workbench.example.invalid:invalid',
])
def test_public_base_url_rejects_non_origin_or_unsafe_address(url):
    with pytest.raises(ValueError, match='WB_PUBLIC_BASE_URL'):
        Settings(env='development', public_base_url=url)


def test_public_base_url_accepts_https_origin_and_local_test_only():
    assert Settings(env='development', public_base_url='https://workbench.example.invalid/').public_base_url == (
        'https://workbench.example.invalid'
    )
    assert Settings(env='test', public_base_url='http://127.0.0.1:18080').public_base_url == (
        'http://127.0.0.1:18080'
    )
