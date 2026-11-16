import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from workbench import routes_operations
from workbench.models import Audit, Channel, Delivery, ResultNotification
from workbench.notifications import send_test_message


def registered_channel(client, admin, project_id, secret="SYNTHETIC-SECRET"):
    response = client.post('/api/v1/channels', headers=admin, json={
        'project_id': project_id, 'name': '隔离测试群',
        'webhook_url': 'https://oapi.dingtalk.com/robot/send?access_token=SYNTHETIC-TOKEN',
        'signing_secret': secret,
    })
    assert response.status_code == 201
    return response.json()


def mock_probe(monkeypatch, handler):
    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(routes_operations, 'send_test_message',
                        lambda encrypted, settings, test_id:
                        send_test_message(encrypted, settings, test_id, client=mock_client, now=1700000000))
    return mock_client


def test_disabled_channel_sends_one_signed_message_and_records_audit(
        client, admin, project, app, monkeypatch):
    channel = registered_channel(client, admin, project['id'])
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'errcode': 0})

    with mock_probe(monkeypatch, handler):
        response = client.post(f"/api/v1/channels/{channel['id']}/test-message", headers=admin)
    assert response.status_code == 200, response.text
    assert response.json()['sent'] is True
    test_id = response.json()['test_id']
    assert len(requests) == 1
    request = requests[0]
    assert request.method == 'POST'
    assert request.url.host == 'oapi.dingtalk.com'
    assert request.url.path == '/robot/send'
    query = parse_qs(urlsplit(str(request.url)).query)
    assert query['access_token'] == ['SYNTHETIC-TOKEN']
    assert query['timestamp'] == ['1700000000000']
    expected = hmac.new(b'SYNTHETIC-SECRET', b'1700000000000\nSYNTHETIC-SECRET',
                        hashlib.sha256).digest()
    import base64
    assert query['sign'] == [base64.b64encode(expected).decode()]
    body = json.loads(request.content)
    assert body['msgtype'] == 'text'
    assert '自动化工作台连接测试' in body['text']['content']
    assert test_id in body['text']['content']
    assert 'SYNTHETIC-SECRET' not in json.dumps(body)
    with app.state.factory() as db:
        assert db.get(Channel, channel['id']).enabled is False
        assert db.scalar(select(Delivery.id)) is None
        assert db.scalar(select(ResultNotification.id)) is None
        audits = list(db.scalars(select(Audit).where(Audit.resource_id == channel['id']).order_by(Audit.created_at)))
    probe_audits = [entry for entry in audits if entry.action.startswith('test_channel_')]
    assert {entry.action for entry in probe_audits} == {'test_channel_requested', 'test_channel_result'}
    assert all(test_id in entry.note for entry in probe_audits)
    assert 'SYNTHETIC-TOKEN' not in ''.join(entry.note for entry in probe_audits)
    assert 'SYNTHETIC-SECRET' not in ''.join(entry.note for entry in probe_audits)


@pytest.mark.parametrize('response_type, expected_text', [
    ('rejected', '错误码 310000'),
    ('invalid_code', '钉钉拒绝测试消息'),
    ('invalid_json', '送达情况未知'),
    ('http_error', 'HTTP 429'),
    ('timeout', '送达情况未知'),
])
def test_probe_failures_are_immediate_sanitized_and_audited(
        client, admin, project, app, monkeypatch, response_type, expected_text):
    channel = registered_channel(client, admin, project['id'])

    def handler(_request):
        if response_type == 'rejected':
            return httpx.Response(200, json={'errcode': 310000,
                                             'errmsg': 'access_token=SYNTHETIC-TOKEN private-value'})
        if response_type == 'invalid_code':
            return httpx.Response(200, json={'errcode': False})
        if response_type == 'invalid_json':
            return httpx.Response(200, text='private-value')
        if response_type == 'http_error':
            return httpx.Response(429, text='private-value')
        raise httpx.ReadTimeout('private-value')

    with mock_probe(monkeypatch, handler):
        response = client.post(f"/api/v1/channels/{channel['id']}/test-message", headers=admin)
    assert response.status_code == 502
    assert expected_text in response.json()['message']
    assert response.json()['code'] == 'channel_test_failed'
    assert 'private-value' not in response.text
    assert 'SYNTHETIC-TOKEN' not in response.text
    with app.state.factory() as db:
        audits = list(db.scalars(select(Audit).where(Audit.resource_id == channel['id'],
                                                      Audit.action == 'test_channel_result')))
    assert len(audits) == 1
    assert 'private-value' not in audits[0].note
    assert 'SYNTHETIC-TOKEN' not in audits[0].note


def test_probe_requires_project_admin_and_existing_channel(client, admin, project, monkeypatch):
    channel = registered_channel(client, admin, project['id'])
    monkeypatch.setattr(routes_operations, 'send_test_message',
                        lambda *_: pytest.fail('无权限请求不应外发'))
    path = f"/api/v1/channels/{channel['id']}/test-message"
    assert client.post(path).status_code == 401
    person = client.post('/api/v1/users', headers=admin, json={
        'username': 'probe-viewer', 'password': 'viewer-password-123', 'display_name': '只读人员',
    }).json()
    assert client.post(f"/api/v1/projects/{project['id']}/members", headers=admin,
                       json={'user_id': person['id'], 'role': 'viewer'}).status_code == 200
    viewer_token = client.post('/api/v1/auth/login', json={
        'username': 'probe-viewer', 'password': 'viewer-password-123',
    }).json()['token']
    viewer = {'Authorization': 'Bearer '+viewer_token}
    assert client.post(path, headers=viewer).status_code == 403
    other = client.post('/api/v1/projects', headers=admin, json={'name': '其他隔离项目'}).json()
    other_channel = registered_channel(client, admin, other['id'])
    assert client.post(f"/api/v1/channels/{other_channel['id']}/test-message",
                       headers=viewer).status_code == 404
    assert client.post('/api/v1/channels/missing/test-message', headers=admin).status_code == 404
