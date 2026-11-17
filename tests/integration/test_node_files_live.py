"""真实 HTTP、Agent 子进程和合成文件联调；不访问生产或真实通知通道。"""

from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid
import zipfile

from cryptography.fernet import Fernet
import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
import uvicorn

from workbench.config import Settings
from workbench.db import initialize
from workbench.main import create_app
from workbench.models import User
from workbench.security import hasher


ROOT = Path(__file__).resolve().parents[2]
SOURCE = '''import argparse, json, time
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--context-file', required=True)
context = json.loads(Path(parser.parse_args().context_file).read_text(encoding='utf-8'))
time.sleep(context['parameters'].get('delay', 0))
content = '|'.join(Path(p).read_text(encoding='utf-8') for p in context['parameters']['sources'])
output = Path(context['output_dir'])
(output / 'merged.txt').write_text(content, encoding='utf-8')
result = {'protocol_version': '1', 'run_id': context['run_id'], 'attempt_id': context['attempt_id'],
          'status': 'success', 'summary': '本机文件联调完成',
          'metrics': {'inputs': len(context['input_manifest'])},
          'artifacts': [{'name': 'merged.txt', 'required': True}]}
(output / 'result.json').write_text(json.dumps(result), encoding='utf-8')
print('synthetic files processed', flush=True)
'''


def checked(response: httpx.Response, status: int = 200) -> dict:
    assert response.status_code == status, (
        f'{response.request.method} {response.request.url.path}: '
        f'{response.status_code} != {status}; {response.text[:400]}'
    )
    return response.json()


def eventually(query, predicate, timeout: float = 20):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = query()
        if predicate(latest):
            return latest
        time.sleep(0.1)
    pytest.fail(f'等待真实系统状态超时：{latest}')


@contextmanager
def isolated_server(directory: Path):
    """默认临时 SQLite；显式测试指针可启用本机 PostgreSQL 独立 schema。"""
    database_url = 'sqlite:///' + str(directory / 'test.db')
    maintenance = None
    schema = None
    pointer = os.environ.get('WORKBENCH_TEST_DATABASE_FILE')
    if pointer:
        configured = json.loads(Path(pointer).read_text(encoding='utf-8'))['database_url']
        assert make_url(configured).host in {'127.0.0.1', 'localhost', '::1'}
        maintenance = create_engine(configured)
        schema = 'node_files_' + uuid.uuid4().hex
        with maintenance.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        database_url = make_url(configured).update_query_dict(
            {'options': '-csearch_path=' + schema}).render_as_string(hide_password=False)
    settings = Settings(env='test', database_url=database_url, data_dir=directory / 'storage',
                        secret_key=Fernet.generate_key().decode(), base_path='/automation')
    application = create_app(settings)
    initialize(application.state.engine, application.state.factory)
    password = secrets.token_urlsafe(24)
    with application.state.factory.begin() as session:
        session.add(User(username='node-files-test', display_name='合成验收',
                         password_hash=hasher.hash(password), is_admin=True))
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(application, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    base_url = f'http://127.0.0.1:{port}/automation'
    try:
        eventually(lambda: server.started, bool)
        with httpx.Client(base_url=base_url + '/api/v1/', timeout=10, trust_env=False) as client:
            login = checked(client.post('auth/login', json={
                'username': 'node-files-test', 'password': password}))
            client.headers['Authorization'] = 'Bearer ' + login['token']
            yield client, base_url, {'username': 'node-files-test', 'password': password}
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        application.state.engine.dispose()
        if maintenance is not None:
            with maintenance.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            maintenance.dispose()
        assert not thread.is_alive(), '隔离服务未正常退出'


@contextmanager
def running_agent(directory: Path, base_url: str, node: dict, inputs: Path,
                  python_runtime: str | None = None):
    assert urlsplit(base_url).hostname == '127.0.0.1'
    token_file = directory / 'node-token.local'
    token_file.write_text(node['token'], encoding='utf-8')
    config_file = directory / 'agent.json'
    config_file.write_text(json.dumps({
        'base_url': base_url, 'token_file': str(token_file),
        'state_dir': str(directory / 'state'), 'runtimes': {'python': python_runtime or sys.executable},
        'capabilities': ['python'], 'input_roots': [str(inputs)],
        'poll_seconds': 0.1, 'heartbeat_seconds': 0.2,
    }), encoding='utf-8')
    environment = os.environ.copy()
    profile = directory / 'synthetic-profile'
    profile.mkdir()
    environment.update(USERPROFILE=str(profile), HOME=str(profile))
    with (directory / 'agent.log').open('wb') as log:
        process = subprocess.Popen([sys.executable, '-m', 'agent', '--config', str(config_file)],
                                   cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


def test_real_agent_browse_parameters_local_save_and_archive(tmp_path):
    inputs, outputs = tmp_path / 'inputs', tmp_path / 'saved'
    inputs.mkdir()
    outputs.mkdir()
    files = [inputs / '甲.txt', inputs / '乙.txt']
    for path, content in zip(files, ['SYNTHETIC-A', 'SYNTHETIC-B'], strict=True):
        path.write_text(content, encoding='utf-8')
    with isolated_server(tmp_path) as (client, base_url, credentials):
        project = checked(client.post('projects', json={'name': '本机文件端到端合成项目'}), 201)
        node = checked(client.post('nodes', json={
            'project_id': project['id'], 'name': '隔离执行电脑', 'capabilities': ['python'],
            'slots': 1, 'input_roots': [str(inputs)]}), 201)
        with running_agent(tmp_path, base_url, node, inputs) as process:
            def node_detail():
                records = checked(client.get('nodes'))['items']
                return next(record for record in records if record['id'] == node['id'])

            eventually(node_detail, lambda record: record.get('online') and
                       'node-files-v1' in record.get('capabilities', []))
            file_config_route = f'nodes/{node["id"]}/file-configuration'
            configuration = checked(client.get(file_config_route, params={'project_id': project['id']}))
            checked(client.patch(f'nodes/{node["id"]}', json={'maintenance': True}))
            checked(client.put(file_config_route, json={
                'base_revision': configuration['revision'], 'output_roots': [str(outputs)],
                'grants': [{'project_id': project['id'], 'output_roots': [str(outputs)]}]}))
            eventually(lambda: checked(client.get(file_config_route, params={'project_id': project['id']})),
                       lambda result: result['apply_status'] == 'applied')
            checked(client.patch(f'nodes/{node["id"]}', json={'maintenance': False}))
            browse = checked(client.post(f'nodes/{node["id"]}/file-requests', json={
                'project_id': project['id'], 'purpose': 'input', 'path': str(inputs), 'limit': 1}), 202)
            listing = eventually(lambda: checked(client.get(
                f'nodes/{node["id"]}/file-requests/{browse["id"]}')),
                lambda result: result['state'] not in {'pending', 'leased'})
            assert listing['state'] == 'succeeded' and len(listing['entries']) == 1
            assert listing['truncated'] and listing['next_offset'] == 1
            next_page = checked(client.post(f'nodes/{node["id"]}/file-requests', json={
                'project_id': project['id'], 'purpose': 'input', 'path': str(inputs),
                'limit': 1, 'offset': listing['next_offset']}), 202)
            next_listing = eventually(lambda: checked(client.get(
                f'nodes/{node["id"]}/file-requests/{next_page["id"]}')),
                lambda result: result['state'] not in {'pending', 'leased'})
            assert next_listing['state'] == 'succeeded'
            assert {listing['entries'][0]['name'], next_listing['entries'][0]['name']} == {'甲.txt', '乙.txt'}
            program = checked(client.post('programs', json={
                'project_id': project['id'], 'name': '合成文件合并'}), 201)
            version = checked(client.post(f'programs/{program["id"]}/versions', json={
                'name': 'test-v1', 'command': ['python', 'main.py'], 'side_effects': False,
                'result_required': True, 'required_capabilities': ['python'],
                'parameter_schema': {'type': 'object', 'additionalProperties': False,
                    'properties': {'delay': {'type': 'number', 'minimum': 0, 'maximum': 3},
                        'sources': {'type': 'array', 'items': {'type': 'string'},
                        'title': '待合并文件', 'x-workbench-file': 'files'}}}}), 201)
            bundle = io.BytesIO()
            with zipfile.ZipFile(bundle, 'w') as archive:
                archive.writestr('main.py', SOURCE)
            checked(client.post(f'versions/{version["id"]}/bundle', content=bundle.getvalue(),
                                headers={'Content-Type': 'application/zip'}))
            checked(client.post(f'versions/{version["id"]}/publish'))
            task = checked(client.post('tasks', json={
                'project_id': project['id'], 'name': '文件映射与保存', 'version_id': version['id'],
                'node_id': node['id'], 'timeout_seconds': 20}), 201)
            options = {'inputs': {'sources': [str(path) for path in files]}, 'output_dir': str(outputs)}
            current_options = checked(client.get(f'tasks/{task["id"]}/file-options'))
            checked(client.put(f'tasks/{task["id"]}/file-options', json={
                **options, 'base_config_hash': current_options['base_config_hash']}))
            run = checked(client.post(f'tasks/{task["id"]}/runs', json={'event_id': 'file-live-1'}), 202)
            detail = eventually(lambda: checked(client.get(f'runs/{run["run_id"]}')),
                                lambda result: result['state'] not in {'queued', 'leased', 'running'}, 35)
            assert process.poll() is None, 'Agent意外退出'
            assert detail['state'] == 'succeeded', detail.get('error')
            assert detail['artifact_state'] == 'complete'
            assert detail['file_status']['save_state'] == 'succeeded'
            assert detail['parameters']['sources'] == options['inputs']['sources']
            saved = outputs / run['run_id'] / 'merged.txt'
            assert saved.read_text(encoding='utf-8') == 'SYNTHETIC-A|SYNTHETIC-B'
            artifact = next(artifact for artifact in detail['artifacts'] if artifact['name'] == 'merged.txt')
            downloaded = client.get(f'runs/{run["run_id"]}/artifacts/{artifact["id"]}')
            assert downloaded.status_code == 200 and downloaded.text == saved.read_text(encoding='utf-8')
            assert [path.read_text(encoding='utf-8') for path in files] == ['SYNTHETIC-A', 'SYNTHETIC-B']
            assert checked(client.get('channels', params={'project_id': project['id']}))['items'] == []
            # 先占用保存目录，验证保存失败不能掩盖脚本成功，也不能覆盖已有内容。
            collision_run = checked(client.post(f'tasks/{task["id"]}/runs', json={
                'event_id': 'file-live-collision', 'parameters': {'delay': 2}}), 202)
            occupied = outputs / collision_run['run_id']
            occupied.mkdir()
            marker = occupied / 'existing.txt'
            marker.write_text('KEEP-EXISTING', encoding='utf-8')
            failed_save = eventually(lambda: checked(client.get(f'runs/{collision_run["run_id"]}')),
                lambda result: result['state'] not in {'queued', 'leased', 'running'}, 35)
            assert failed_save['state'] == 'succeeded'
            assert failed_save['file_status']['save_state'] == 'failed'
            assert failed_save['artifact_state'] == 'complete'
            original_attempt = failed_save['attempt_id']
            retry_response = client.post(f'runs/{collision_run["run_id"]}/file-save/retry')
            assert retry_response.status_code in {200, 202}, retry_response.text
            retried = eventually(lambda: checked(client.get(f'runs/{collision_run["run_id"]}')),
                lambda result: result['file_status']['save_state'] == 'succeeded', 25)
            assert retried['attempt_id'] == original_attempt and retried['state'] == 'succeeded'
            assert marker.read_text(encoding='utf-8') == 'KEEP-EXISTING'
            retry_file = occupied / 'retry-1' / 'merged.txt'
            assert retry_file.read_text(encoding='utf-8') == 'SYNTHETIC-A|SYNTHETIC-B'
            if os.environ.get('WB_NODE_FILES_BROWSER') == '1':
                session_file = tmp_path / 'browser-session.local.json'
                session_file.write_text(json.dumps({**credentials, 'url': base_url,
                    'authorization': client.headers['Authorization'], 'node_id': node['id'],
                    'project_id': project['id'], 'program_id': program['id']}), encoding='utf-8')
                try:
                    browser_test = subprocess.run(['node', 'e2e/node-files.mjs', str(session_file)],
                        cwd=ROOT / 'frontend', capture_output=True, text=True, encoding='utf-8', timeout=100)
                    assert browser_test.returncode == 0, browser_test.stdout + browser_test.stderr
                finally:
                    session_file.unlink(missing_ok=True)


def test_real_agent_excel_selection_date_and_saved_archive(tmp_path):
    """真实 xlsx 经 HTTP 派发和 Agent 读取，验证日期、保存与下载内容一致。"""
    configured = os.environ.get('EXCEL_TEST_PYTHON')
    if not configured:
        pytest.skip('需配置 EXCEL_TEST_PYTHON 指向安装固定 Excel 依赖的独立 Python')
    python = str(Path(configured).absolute())
    inputs, outputs = tmp_path / 'excel-inputs', tmp_path / 'excel-saved'
    inputs.mkdir()
    outputs.mkdir()
    generated = subprocess.run([python, '-c', '''import sys
from pathlib import Path
from openpyxl import Workbook
import openpyxl
assert openpyxl.__version__ == '3.1.5'
for name, quantity in [('synthetic-a.xlsx', 3), ('synthetic-b.xlsx', 5)]:
    book=Workbook(); sheet=book.active; sheet.title='Synthetic'
    sheet.append(['ID','Quantity']); sheet.append([name,quantity])
    book.save(Path(sys.argv[1])/name); book.close()
''', str(inputs)], capture_output=True, timeout=15)
    assert generated.returncode == 0, generated.stderr
    files = sorted(inputs.glob('*.xlsx'))
    originals = [path.read_bytes() for path in files]
    example = ROOT / 'examples/excel-file-input'
    program_definition = json.loads((example / 'program.json').read_text(encoding='utf-8'))
    with isolated_server(tmp_path) as (client, base_url, _credentials):
        project = checked(client.post('projects', json={'name': 'Excel 文件日期合成验收'}), 201)
        node = checked(client.post('nodes', json={
            'project_id': project['id'], 'name': 'Excel 隔离执行电脑', 'capabilities': ['python'],
            'slots': 1, 'input_roots': [str(inputs)]}), 201)
        with running_agent(tmp_path, base_url, node, inputs, python_runtime=python) as process:
            eventually(lambda: checked(client.get('nodes'))['items'],
                       lambda records: any(record['id'] == node['id'] and record.get('online') and
                           'node-files-v1' in record.get('capabilities', []) for record in records))
            route = f'nodes/{node["id"]}/file-configuration'
            configuration = checked(client.get(route, params={'project_id': project['id']}))
            checked(client.patch(f'nodes/{node["id"]}', json={'maintenance': True}))
            checked(client.put(route, json={
                'base_revision': configuration['revision'], 'output_roots': [str(outputs)],
                'grants': [{'project_id': project['id'], 'output_roots': [str(outputs)]}]}))
            eventually(lambda: checked(client.get(route, params={'project_id': project['id']})),
                       lambda record: record['apply_status'] == 'applied')
            checked(client.patch(f'nodes/{node["id"]}', json={'maintenance': False}))
            program = checked(client.post('programs', json={
                'project_id': project['id'], 'name': program_definition['name']}), 201)
            version = checked(client.post(f'programs/{program["id"]}/versions', json={
                **{key: program_definition[key] for key in ('command', 'side_effects', 'result_required',
                                                            'required_capabilities', 'parameter_schema')},
                'name': 'synthetic-excel-v1'}), 201)
            bundle = io.BytesIO()
            with zipfile.ZipFile(bundle, 'w') as archive:
                for filename in ('main.py', 'requirements.txt', 'THIRD_PARTY_NOTICES.txt'):
                    archive.write(example / filename, filename)
            checked(client.post(f'versions/{version["id"]}/bundle', content=bundle.getvalue(),
                                headers={'Content-Type': 'application/zip'}))
            checked(client.post(f'versions/{version["id"]}/publish'))
            task = checked(client.post('tasks', json={
                'project_id': project['id'], 'name': '选择 Excel 与日期', 'version_id': version['id'],
                'node_id': node['id'], 'timeout_seconds': 30}), 201)
            options = checked(client.get(f'tasks/{task["id"]}/file-options'))
            checked(client.put(f'tasks/{task["id"]}/file-options', json={
                'inputs': {'sources': [str(path) for path in files]}, 'output_dir': str(outputs),
                'base_config_hash': options['base_config_hash']}))
            run = checked(client.post(f'tasks/{task["id"]}/runs', json={
                'event_id': 'synthetic-excel-date', 'parameters': {'report_date': '2026-11-17'}}), 202)
            detail = eventually(lambda: checked(client.get(f'runs/{run["run_id"]}')),
                lambda record: record['state'] not in {'queued', 'leased', 'running'}, 40)
            assert process.poll() is None
            assert detail['state'] == 'succeeded', detail.get('error')
            assert detail['artifact_state'] == 'complete'
            assert detail['file_status']['save_state'] == 'succeeded'
            assert detail['parameters']['report_date'] == '2026-11-17'
            saved = outputs / run['run_id'] / 'excel-summary.xlsx'
            artifact = next(record for record in detail['artifacts'] if record['name'] == 'excel-summary.xlsx')
            downloaded = client.get(f'runs/{run["run_id"]}/artifacts/{artifact["id"]}')
            assert downloaded.status_code == 200
            assert downloaded.content == saved.read_bytes()
            inspected = subprocess.run([python, '-c', '''import json, sys
from openpyxl import load_workbook
book=load_workbook(sys.argv[1],read_only=True,data_only=True)
try: print(json.dumps(list(book.active.values)))
finally: book.close()
''', str(saved)], capture_output=True, text=True, timeout=15)
            assert inspected.returncode == 0, inspected.stderr
            assert json.loads(inspected.stdout)[1:] == [
                ['synthetic-a.xlsx', 'Synthetic', 2, 2, 4, '2026-11-17'],
                ['synthetic-b.xlsx', 'Synthetic', 2, 2, 4, '2026-11-17']]
            assert [path.read_bytes() for path in files] == originals
            assert checked(client.get('channels', params={'project_id': project['id']}))['items'] == []
