import time
from sqlalchemy import select
from workbench.db import lock
from workbench.execution import alert
from workbench.models import Alert, Audit, Node, Run
from workbench.scheduling import scan_health


def test_node_recovery_does_not_resolve_unknown_run(client, admin, project, registered, app):
    node = client.post('/api/v1/nodes', headers=admin, json={'project_id': project['id'], 'name': '恢复测试'}).json()
    auth = {'Authorization': 'Bearer '+node['token']}
    heartbeat = {'capabilities': [], 'isolation': 'development', 'agent_version': '0.1.0'}
    client.post('/api/v1/agent/heartbeat', headers=auth, json=heartbeat)
    run_id = client.post('/api/v1/tasks/'+registered['task']['id']+'/runs', headers=admin,
                         json={'event_id': 'lost-node'}).json()['run_id']
    assert client.post('/api/v1/agent/claim', headers=auth, json={}).status_code == 200
    with app.state.factory.begin() as db:
        lock(db)
        db.get(Node, node['id']).last_seen = time.time()-300
        scan_health(db, app.state.settings)
    with app.state.factory() as db:
        assert db.get(Run, run_id).state == 'unknown'
        offline_id = db.scalar(select(Alert).where(Alert.kind == 'node_offline')).id
    assert client.post('/api/v1/alerts/'+offline_id+'/ack', headers=admin, json={'note': '已安排核查节点'}).status_code == 200
    client.post('/api/v1/agent/heartbeat', headers=auth, json=heartbeat)
    for _ in range(2):
        with app.state.factory.begin() as db:
            lock(db)
            scan_health(db, app.state.settings)
    with app.state.factory() as db:
        assert db.get(Alert, offline_id).status == 'recovered'
        assert db.get(Run, run_id).state == 'unknown'
        assert db.scalar(select(Alert).where(Alert.kind == 'unknown')).status == 'open'
        assert len(db.scalars(select(Alert).where(Alert.kind == 'recovery')).all()) == 1


def test_close_is_audited_idempotent_and_cannot_reopen(client, admin, project, app):
    with app.state.factory.begin() as db:
        record = alert(db, project['id'], 'synthetic_failure', '合成故障', 'close-test')
        identifier = record.id
    path = '/api/v1/alerts/'+identifier
    for _ in range(2):
        response = client.post(path+'/close', headers=admin, json={'note': '已核对测试故障原因'} )
        assert response.status_code == 200
        assert response.json()['status'] == 'closed'
    assert client.post(path+'/ack', headers=admin, json={'note': '不能重新确认关闭故障'}).status_code == 409
    with app.state.factory() as db:
        assert len(db.scalars(select(Audit).where(Audit.action == 'close_alert')).all()) == 1


def test_maintenance_suppresses_new_offline_alert(client, admin, project, app):
    node = client.post('/api/v1/nodes', headers=admin, json={'project_id': project['id'], 'name': '维护中'}).json()
    with app.state.factory.begin() as db:
        lock(db)
        record = db.get(Node, node['id'])
        record.last_seen, record.maintenance = time.time()-300, True
        scan_health(db, app.state.settings)
    assert client.get('/api/v1/alerts', headers=admin).json()['total'] == 0
