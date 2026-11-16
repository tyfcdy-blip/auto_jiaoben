import io
import zipfile


def test_windows_agent_package_requires_login_and_contains_only_expected_runtime(client, admin):
    assert client.get("/api/v1/agent-packages/windows").status_code == 401
    response = client.get("/api/v1/agent-packages/windows", headers=admin)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == 'attachment; filename="automation-workbench-windows-agent.zip"'
    assert len(response.content) < 2 * 1024 * 1024
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert {"安装并连接.cmd", "installer.py", "requirements-agent.txt", "使用说明.txt"} <= set(names)
        assert "agent/__main__.py" in names
        assert "contracts/runtime/claim.schema.json" in names
        assert {name for name in names if "/" not in name} == {
            "安装并连接.cmd", "installer.py", "requirements-agent.txt", "使用说明.txt"}
        command = archive.read("安装并连接.cmd")
        assert b"\r\n" in command and b"\n" not in command.replace(b"\r\n", b"")
        assert len(names) == len({name.casefold() for name in names})
        assert not any("__pycache__" in name or name.endswith((".pyc", ".json.local")) for name in names)
        combined = b"".join(archive.read(name) for name in names)
        assert b"node-token.txt" in combined
        assert b"SYNTHETIC_NODE_TOKEN" not in combined


def test_windows_agent_package_is_deterministic(client, admin):
    first = client.get("/api/v1/agent-packages/windows", headers=admin)
    second = client.get("/api/v1/agent-packages/windows", headers=admin)
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
