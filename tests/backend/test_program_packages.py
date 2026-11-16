import io
import zipfile
from sqlalchemy import select
from workbench.bundle_scan import scan_bundle
from workbench.models import Audit


def package(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def create_draft(client, admin, project, name="package-test"):
    program = client.post("/api/v1/programs", headers=admin, json={
        "project_id": project["id"], "name": name,
    }).json()
    version = client.post(f'/api/v1/programs/{program["id"]}/versions', headers=admin, json={
        "name": "v1", "command": ["python", "main.py"],
        "parameter_schema": {"type": "object", "additionalProperties": False},
    }).json()
    return program, version


def test_preflight_and_upload_reject_python_syntax_error(client, admin, project):
    payload = package({"main.py": "def broken(:\n    pass\n"})
    preflight = client.post(
        f'/api/v1/program-bundles/scan?project_id={project["id"]}&runtime=python&entry=main.py',
        headers=admin, content=payload,
    )
    assert preflight.status_code == 200
    assert preflight.json()["decision"] == "blocked"
    assert preflight.json()["findings"][0]["code"] == "python_syntax_error"
    _, version = create_draft(client, admin, project)
    stored = client.post(f'/api/v1/versions/{version["id"]}/bundle', headers=admin, content=payload)
    assert stored.status_code == 422
    assert stored.json()["code"] == "bundle_scan_blocked"


def test_high_risk_package_requires_explicit_review(client, admin, project):
    payload = package({"main.py": "import os\nos.system('echo synthetic')\n"})
    scan = client.post(
        f'/api/v1/program-bundles/scan?project_id={project["id"]}&runtime=python&entry=main.py',
        headers=admin, content=payload,
    ).json()
    assert scan["decision"] == "review"
    assert any(item["code"] == "shell_command" for item in scan["findings"])
    _, version = create_draft(client, admin, project, "review-test")
    assert client.post(f'/api/v1/versions/{version["id"]}/bundle', headers=admin,
                       content=payload).status_code == 409
    accepted = client.post(f'/api/v1/versions/{version["id"]}/bundle?accept_review=true',
                           headers=admin, content=payload)
    assert accepted.status_code == 200
    assert accepted.json()["bundle_scan"]["decision"] == "review"
    assert client.post(f'/api/v1/versions/{version["id"]}/publish', headers=admin).status_code == 409
    published = client.post(f'/api/v1/versions/{version["id"]}/publish?accept_review=true', headers=admin)
    assert published.status_code == 200


def test_program_package_can_be_exported_without_write_permission(client, admin, project):
    payload = package({"main.py": "print('export-verification')\n"})
    program, version = create_draft(client, admin, project, "export-test")
    assert client.post(f'/api/v1/versions/{version["id"]}/bundle', headers=admin,
                       content=payload).status_code == 200
    user = client.post("/api/v1/users", headers=admin, json={
        "username": "package-viewer", "display_name": "程序查看者",
        "password": "viewer-password-123",
    }).json()
    assert client.post(f'/api/v1/projects/{project["id"]}/members', headers=admin, json={
        "user_id": user["id"], "role": "viewer",
    }).status_code == 200
    token = client.post("/api/v1/auth/login", json={
        "username": "package-viewer", "password": "viewer-password-123",
    }).json()["token"]
    downloaded = client.get(f'/api/v1/versions/{version["id"]}/bundle',
                            headers={"Authorization": f"Bearer {token}"})
    assert downloaded.status_code == 200
    assert downloaded.content == payload
    assert downloaded.headers["content-type"] == "application/zip"
    assert version["id"] in downloaded.headers["content-disposition"]
    assert program["id"]


def test_package_export_is_audited_and_rejects_machine_or_unrelated_user(
        client, admin, project, app):
    payload = package({"main.py": "print('protected')\n"})
    _, version = create_draft(client, admin, project, "protected-export")
    assert client.post(f'/api/v1/versions/{version["id"]}/bundle', headers=admin,
                       content=payload).status_code == 200
    node = client.post('/api/v1/nodes', headers=admin, json={
        'name': 'package-node', 'project_id': project['id'],
    }).json()
    assert client.get(f'/api/v1/versions/{version["id"]}/bundle', headers={
        'Authorization': 'Bearer ' + node['token'],
    }).status_code == 403
    client.post('/api/v1/users', headers=admin, json={
        'username': 'unrelated-package-user', 'display_name': '无关用户',
        'password': 'unrelated-password-123',
    })
    token = client.post('/api/v1/auth/login', json={
        'username': 'unrelated-package-user', 'password': 'unrelated-password-123',
    }).json()['token']
    assert client.get(f'/api/v1/versions/{version["id"]}/bundle', headers={
        'Authorization': 'Bearer ' + token,
    }).status_code == 404
    assert client.get(f'/api/v1/versions/{version["id"]}/bundle', headers=admin).status_code == 200
    with app.state.factory() as db:
        assert db.scalar(select(Audit).where(
            Audit.action == 'download_bundle', Audit.resource_id == version['id'],
        )) is not None


def test_missing_bundle_cannot_be_exported(client, admin, project):
    _, version = create_draft(client, admin, project, "missing-export-test")
    response = client.get(f'/api/v1/versions/{version["id"]}/bundle', headers=admin)
    assert response.status_code == 404
    assert response.json()["code"] == "missing_bundle"


def test_saved_bundle_is_idempotent_immutable_and_project_quota_is_enforced(
        client, admin, project, app):
    payload = package({'main.py': "print('stable')\n"})
    _, version = create_draft(client, admin, project, 'immutable-package')
    path = f'/api/v1/versions/{version["id"]}/bundle'
    assert client.post(path, headers=admin, content=payload).status_code == 200
    assert client.post(path, headers=admin, content=payload).status_code == 200
    replaced = client.post(path, headers=admin, content=package({'main.py': "print('changed')\n"}))
    assert replaced.status_code == 409
    assert replaced.json()['code'] == 'immutable_bundle'
    app.state.settings.max_project_program_bytes = len(payload)
    _, another = create_draft(client, admin, project, 'quota-package')
    quota = client.post(f'/api/v1/versions/{another["id"]}/bundle', headers=admin,
                        content=package({'main.py': "print('another distinct package')\n"}))
    assert quota.status_code == 413
    assert quota.json()['code'] == 'project_program_quota'


def test_scanner_covers_invalid_json_secrets_destructive_commands_and_opaque_files():
    blocked = scan_bundle(package({
        'main.py': "password = 'synthetic-secret-123'\n",
        'config.json': '{invalid',
        'cleanup.sh': 'rm -rf /\n',
        'helper.dll': b'not-a-real-binary',
    }), 'main.py')
    assert blocked['decision'] == 'blocked'
    codes = {finding['code'] for finding in blocked['findings']}
    assert {'embedded_secret', 'json_syntax_error', 'destructive_root_command',
            'opaque_executable'} <= codes
    clean = scan_bundle(package({
        'main.py': "from pathlib import Path\nprint(Path('input').name)\n",
        'worker.js': "console.log('synthetic')\n",
        'settings.json': '{"enabled": true}',
    }), 'main.py')
    assert clean['decision'] == 'pass'
    assert clean['coverage'] == 'full'


def test_scanner_decision_cannot_be_hidden_after_finding_display_limit():
    noisy = '\n'.join(f'eval("{number}")' for number in range(100))
    report = scan_bundle(package({
        'a.py': noisy,
        'main.py': "print('entry')\n",
        'z.sh': 'rm -rf /\n',
    }), 'main.py', 'python')
    assert len(report['findings']) == 100
    assert report['summary']['errors'] == 1
    assert report['decision'] == 'blocked'


def test_python_runtime_scans_entry_even_when_extension_is_unusual():
    report = scan_bundle(package({'main.txt': 'def broken(:\n  pass\n'}),
                         'main.txt', 'python')
    assert report['decision'] == 'blocked'
    assert report['findings'][0]['code'] == 'python_syntax_error'
    unsupported = scan_bundle(package({'main.custom': 'opaque source'}),
                              'main.custom', 'custom-runtime')
    assert unsupported['decision'] == 'review'
    assert unsupported['coverage'] == 'partial'


def test_scanner_recognizes_python_import_aliases_and_marks_node_syntax_unverified():
    report = scan_bundle(package({'main.py': (
        'from os import system as shell\n'
        'from subprocess import run\n'
        'import requests as r\n'
        'from pathlib import Path\n'
        'shell("echo synthetic")\n'
        'run("echo synthetic", shell=True)\n'
        'r.get("https://example.invalid", verify=False)\n'
        'Path("synthetic").unlink()\n'
    )}), 'main.py', 'python')
    codes = {finding['code'] for finding in report['findings']}
    assert {'shell_command', 'subprocess_shell', 'tls_verification_disabled',
            'destructive_filesystem'} <= codes
    node = scan_bundle(package({'main.js': 'function broken( {'}), 'main.js', 'node')
    assert node['decision'] == 'review'
    assert any(item['code'] == 'entry_syntax_not_verified' for item in node['findings'])
