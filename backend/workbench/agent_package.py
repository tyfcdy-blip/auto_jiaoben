"""从受控白名单生成Windows执行电脑接入包。"""

from io import BytesIO
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "deploy" / "windows-agent"
TEMPLATE_FILES = ("installer.py", "requirements-agent.txt", "使用说明.txt", "安装并连接.cmd", "升级已绑定电脑.cmd")


def _write(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, content)


def windows_agent_package() -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in TEMPLATE_FILES:
            path = TEMPLATE / name
            content = path.read_bytes()
            if path.suffix.lower() == ".cmd":
                content = content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            _write(archive, path.name, content)
        for directory in (ROOT / "agent", ROOT / "contracts" / "runtime"):
            for path in sorted(directory.rglob("*.py" if directory.name == "agent" else "*.json")):
                relative = path.relative_to(ROOT).as_posix()
                _write(archive, relative, path.read_bytes())
    return output.getvalue()
