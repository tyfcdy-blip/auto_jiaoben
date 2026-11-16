import time
import hashlib
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select
from workbench.models import (Artifact, Batch, BatchFile, FileRetention, LoopStatus, Run,
                              Schedule, Version)
from workbench.retention import cleanup_files
from workbench.storage import store_bytes
from workbench.security import Problem

DAY = 86400


def stored(app, payload):
    sha = store_bytes(app.state.settings, payload)
    return sha, app.state.settings.data_dir / "objects" / sha


def input_file(app, project_id, payload=b"old input", *, age_days=46, label="old-input"):
    now = time.time()
    sha, path = stored(app, payload)
    with app.state.factory.begin() as db:
        batch = Batch(project_id=project_id, label=label, ready=True, created_at=now-age_days*DAY)
        db.add(batch)
        db.flush()
        item = BatchFile(batch_id=batch.id, name="input.bin", sha256=sha, size=len(payload),
                         created_at=now-age_days*DAY)
        db.add(item)
        db.flush()
        return batch.id, item.id, sha, path


def terminal_artifact(app, registered, project_id, payload=b"old output", *, age_days=46,
                      state="succeeded"):
    now = time.time()
    sha, path = stored(app, payload)
    with app.state.factory.begin() as db:
        run = Run(project_id=project_id, task_id=registered["task"]["id"],
                  version_id=registered["version"]["id"], actor="test", source="manual",
                  parameters={}, business_key=hashlib.sha256(
                      f"retention:{sha}:{age_days}:{state}".encode()).hexdigest(),
                  state=state, finished_at=now-age_days*DAY, created_at=now-age_days*DAY)
        db.add(run)
        db.flush()
        item = Artifact(run_id=run.id, name="result.bin", sha256=sha, size=len(payload),
                        created_at=now-age_days*DAY)
        db.add(item)
        db.flush()
        return run.id, item.id, sha, path


def test_capacity_guard_and_existing_sha_dedup(app, monkeypatch):
    settings = app.state.settings
    settings.storage_max_bytes = 8
    settings.storage_warning_bytes = 4
    settings.storage_min_free_bytes = 0
    store_bytes(settings, b"1234")
    # 已存在对象不新增物理占用，即使硬上限随后降低也允许复用。
    settings.storage_max_bytes = 3
    assert store_bytes(settings, b"1234")
    with pytest.raises(Problem) as captured:
        store_bytes(settings, b"5678")
    assert captured.value.status == 507 and captured.value.code == "storage_capacity"

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr("workbench.storage.shutil.disk_usage", lambda _: usage(100, 90, 10))
    settings.storage_max_bytes = 100
    settings.storage_min_free_bytes = 8
    with pytest.raises(Problem) as captured:
        store_bytes(settings, b"abc")
    assert captured.value.status == 507 and captured.value.code == "storage_capacity"


def test_existing_object_content_mismatch_is_rejected(app):
    settings = app.state.settings
    sha = store_bytes(settings, b"expected")
    target = settings.data_dir / "objects" / sha
    target.write_bytes(b"tampered")
    with pytest.raises(Problem) as captured:
        store_bytes(settings, b"expected")
    assert captured.value.status == 507 and captured.value.code == "storage_corrupt"


def test_existing_object_symlink_is_rejected(app, tmp_path):
    settings = app.state.settings
    payload = b"symlink-target"
    sha = store_bytes(settings, payload)
    target = settings.data_dir / "objects" / sha
    outside = tmp_path / "outside.bin"
    outside.write_bytes(payload)
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("当前系统未授予合成测试目录创建符号链接的权限")
    with pytest.raises(Problem) as captured:
        store_bytes(settings, payload)
    assert captured.value.status == 507 and captured.value.code == "storage_corrupt"

def test_retention_configuration_rejects_invalid_thresholds():
    from workbench.config import Settings
    try:
        Settings(storage_warning_bytes=2048, storage_max_bytes=2048)
    except ValueError as exc:
        assert "预警阈值" in str(exc)
    else:
        raise AssertionError("无效阈值未被拒绝")


def test_old_unreferenced_input_expires_but_recent_input_stays(
        app, client, admin, project, registered):
    old_batch, old_id, _, old_path = input_file(app, project["id"], label="old")
    _, recent_id, _, recent_path = input_file(
        app, project["id"], payload=b"recent input", age_days=44, label="recent")
    result = cleanup_files(app.state.factory, app.state.settings, time.time())
    assert result["marked"] == 1 and result["deleted"] == 1
    with app.state.factory() as db:
        assert db.get(FileRetention, {"kind": "input", "record_id": old_id}).deletion_state == "deleted"
        assert db.get(FileRetention, {"kind": "input", "record_id": recent_id}) is None
        assert db.get(Batch, old_batch) is not None
    assert not old_path.exists() and recent_path.exists()
    detail = client.get(f"/api/v1/batches/{old_batch}", headers=admin)
    assert detail.status_code == 200
    assert detail.json()["retention_state"] == "expired"
    assert detail.json()["files"][0]["retention_state"] == "expired"
    trigger = client.post(f"/api/v1/tasks/{registered['task']['id']}/runs", headers=admin, json={
        "event_id": "expired-input", "input_batch_id": old_batch, "parameters": {}})
    assert trigger.status_code == 410 and trigger.json()["code"] == "file_expired"


def test_active_unknown_and_schedule_references_protect_old_input(app, project, registered):
    now = time.time()
    protected = []
    for index, state in enumerate(("queued", "running", "unknown")):
        batch_id, file_id, _, path = input_file(app, project["id"], label=f"protected-{index}")
        with app.state.factory.begin() as db:
            db.add(Run(project_id=project["id"], task_id=registered["task"]["id"],
                       version_id=registered["version"]["id"], actor="test", source="manual",
                       parameters={}, input_batch_id=batch_id, business_key=f"protected-{state}",
                       state=state, created_at=now-46*DAY))
        protected.append((file_id, path))
    batch_id, file_id, _, path = input_file(app, project["id"], label="scheduled")
    with app.state.factory.begin() as db:
        db.add(Schedule(task_id=registered["task"]["id"], project_id=project["id"],
                        name="固定旧批次", cron="0 8 * * *", timezone="Asia/Shanghai",
                        parameters={}, input_batch_id=batch_id, misfire_policy="once",
                        enabled=False, next_fire=now+DAY))
    protected.append((file_id, path))
    cleanup_files(app.state.factory, app.state.settings, now)
    with app.state.factory() as db:
        assert all(db.get(FileRetention, {"kind": "input", "record_id": item}) is None
                   for item, _ in protected)
    assert all(path.exists() for _, path in protected)


def test_artifact_requires_terminal_run_and_45_days(app, project, registered):
    _, old_id, _, old_path = terminal_artifact(app, registered, project["id"])
    _, recent_id, _, recent_path = terminal_artifact(
        app, registered, project["id"], payload=b"recent output", age_days=44)
    _, unknown_id, _, unknown_path = terminal_artifact(
        app, registered, project["id"], payload=b"unknown output", state="unknown")
    cleanup_files(app.state.factory, app.state.settings, time.time())
    with app.state.factory() as db:
        assert db.get(FileRetention, {"kind": "artifact", "record_id": old_id})
        assert db.get(FileRetention, {"kind": "artifact", "record_id": recent_id}) is None
        assert db.get(FileRetention, {"kind": "artifact", "record_id": unknown_id}) is None
    assert not old_path.exists() and recent_path.exists() and unknown_path.exists()


def test_shared_live_reference_and_program_bundle_prevent_physical_delete(
        app, project, registered):
    now = time.time()
    payload = b"shared-live-reference"
    _, artifact_id, sha, path = terminal_artifact(app, registered, project["id"], payload=payload)
    with app.state.factory.begin() as db:
        batch = Batch(project_id=project["id"], label="recent-shared", ready=True)
        db.add(batch)
        db.flush()
        db.add(BatchFile(batch_id=batch.id, name="shared.bin", sha256=sha, size=len(payload)))
    cleanup_files(app.state.factory, app.state.settings, now)
    with app.state.factory() as db:
        retained = db.get(FileRetention, {"kind": "artifact", "record_id": artifact_id})
        assert retained.deletion_state == "shared"
    assert path.exists()

    with app.state.factory() as db:
        bundle_sha = db.get(Version, registered["version"]["id"]).bundle_sha256
    bundle_path = app.state.settings.data_dir / "objects" / bundle_sha
    bundle_payload = bundle_path.read_bytes()
    _, program_artifact_id, _, _ = terminal_artifact(
        app, registered, project["id"], payload=bundle_payload, age_days=47)
    cleanup_files(app.state.factory, app.state.settings,
                  now+app.state.settings.cleanup_interval_seconds+1)
    with app.state.factory() as db:
        retained = db.get(FileRetention, {"kind": "artifact", "record_id": program_artifact_id})
        assert retained.deletion_state == "shared"
        assert db.scalar(select(Version.id).where(Version.bundle_sha256 == bundle_sha))
    assert bundle_path.exists()


def test_expired_artifact_metadata_remains_and_download_returns_410(
        app, client, admin, project, registered):
    run_id, artifact_id, _, _ = terminal_artifact(app, registered, project["id"])
    cleanup_files(app.state.factory, app.state.settings, time.time())
    detail = client.get(f"/api/v1/runs/{run_id}", headers=admin)
    assert detail.status_code == 200
    artifact = detail.json()["artifacts"][0]
    assert artifact["id"] == artifact_id
    assert artifact["retention_state"] == "expired" and artifact["expired_at"]
    download = client.get(f"/api/v1/runs/{run_id}/artifacts/{artifact_id}", headers=admin)
    assert download.status_code == 410 and download.json()["code"] == "file_expired"


def test_delete_failure_is_sanitized_and_retried(app, project, monkeypatch):
    now = time.time()
    _, file_id, _, path = input_file(app, project["id"], label="retry-delete")
    with monkeypatch.context() as scoped:
        scoped.setattr("workbench.retention.os.unlink",
                       lambda _: (_ for _ in ()).throw(PermissionError("private-path")))
        result = cleanup_files(app.state.factory, app.state.settings, now)
    assert result["marked"] == 1 and result["deleted"] == 0 and path.exists()
    with app.state.factory() as db:
        retained = db.get(FileRetention, {"kind": "input", "record_id": file_id})
        assert retained.deletion_state == "failed"
        assert retained.attempts == 1
        assert retained.last_error == "PermissionError" and "private-path" not in retained.last_error
        retry_at = retained.next_try
    result = cleanup_files(app.state.factory, app.state.settings, retry_at+1)
    assert result["deleted"] == 1 and not path.exists()


def test_expired_input_does_not_consume_active_project_quota(app, client, admin, project):
    settings = app.state.settings
    settings.max_project_input_bytes = 6
    settings.max_batch_bytes = 6
    settings.max_upload_bytes = 6
    _, old_id, _, _ = input_file(app, project["id"], payload=b"123456", label="quota-old")
    cleanup_files(app.state.factory, settings, time.time())
    with app.state.factory() as db:
        assert db.get(FileRetention, {"kind": "input", "record_id": old_id})
    batch = client.post("/api/v1/batches", headers=admin, json={
        "project_id": project["id"], "label": "quota-new"}).json()
    uploaded = client.put(f"/api/v1/batches/{batch['id']}/files/new.bin",
                          headers=admin, content=b"abcdef")
    assert uploaded.status_code == 200


def test_cleanup_interval_watermark_and_component_storage_status(app, client, monkeypatch):
    now = time.time()
    first = cleanup_files(app.state.factory, app.state.settings, now)
    second = cleanup_files(app.state.factory, app.state.settings, now+1)
    assert first["skipped"] is False and second["skipped"] is True
    with app.state.factory.begin() as db:
        for name in ("scheduler", "reconciler", "notifier"):
            db.add(LoopStatus(name=name, last_success=now))
    app.state.settings.monitor_token = "m" * 32
    response = client.get("/api/v1/health/components",
                          headers={"Authorization": "Bearer " + "m" * 32})
    assert response.status_code == 200
    assert response.json()["storage"]["healthy"] is True

    with app.state.factory.begin() as db:
        db.add(FileRetention(kind="input", record_id="persistent-failure",
                             project_id="synthetic", sha256="a" * 64, size=1,
                             expired_at=now, deletion_state="failed", attempts=3,
                             next_try=now+3600, last_error="PermissionError"))
    response = client.get("/api/v1/health/components",
                          headers={"Authorization": "Bearer " + "m" * 32})
    assert response.status_code == 503
    assert response.json()["code"] == "storage_cleanup_failed"
    with app.state.factory.begin() as db:
        db.get(FileRetention, {"kind": "input", "record_id": "persistent-failure"}).deletion_state = "deleted"

    monkeypatch.setattr("workbench.retention.storage_usage", lambda _: {
        "used_bytes": 61, "free_bytes": 29, "warning_bytes": 45,
        "max_bytes": 60, "min_free_bytes": 30, "warning": True,
    })
    response = client.get("/api/v1/health/components",
                          headers={"Authorization": "Bearer " + "m" * 32})
    assert response.status_code == 503 and response.json()["code"] == "storage_pressure"


def test_postgres_two_workers_do_not_repeat_cleanup(app, project):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("清理并发须在真实PostgreSQL行锁环境验证")
    now = time.time()
    _, file_id, _, path = input_file(app, project["id"], label="concurrent-cleanup")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: cleanup_files(app.state.factory, app.state.settings, now), range(2)))
    assert sum(result["marked"] for result in results) == 1
    assert sum(not result["skipped"] for result in results) == 1
    with app.state.factory() as db:
        assert db.get(FileRetention, {"kind": "input", "record_id": file_id}).deletion_state == "deleted"
    assert not path.exists()
