"""核对生产镜像的平台、OCI标签和前端子路径；不启动应用或访问网络。"""

import argparse
import json
import re
import subprocess
import sys


IMAGE_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}")
COMMIT = re.compile(r"[0-9a-f]{40}")
LOCAL_ASSET = re.compile(r"(?:src|href)=[\"']([^\"']+)[\"']")


def normalized_base_path(value: str) -> str:
    if value == "/":
        return value
    if (not value.startswith("/") or ".." in value or "//" in value
            or any(character.isspace() for character in value)
            or any(character in value for character in "?#\\")):
        raise ValueError("前端子路径必须是安全的绝对路径")
    return value.rstrip("/") + "/"


def verify_metadata(metadata: dict, version: str, commit: str) -> dict:
    labels = metadata.get("Config", {}).get("Labels") or {}
    if metadata.get("Os") != "linux" or metadata.get("Architecture") != "amd64":
        raise ValueError("发布镜像必须是linux/amd64")
    if labels.get("org.opencontainers.image.version") != version:
        raise ValueError("镜像版本标签与发布版本不一致")
    if labels.get("org.opencontainers.image.revision") != commit:
        raise ValueError("镜像提交标签与发布提交不一致")
    return {
        "image_id": metadata.get("Id"),
        "os": metadata.get("Os"),
        "architecture": metadata.get("Architecture"),
        "size_bytes": metadata.get("Size"),
    }


def verify_frontend_index(index_html: str, base_path: str) -> list[str]:
    prefix = normalized_base_path(base_path) + "assets/"
    local_assets = [path for path in LOCAL_ASSET.findall(index_html) if path.startswith("/")]
    if not local_assets:
        raise ValueError("镜像前端首页没有可核对的本地资源")
    invalid = [path for path in local_assets if not path.startswith(prefix)]
    if invalid:
        raise ValueError("镜像前端资源没有使用指定子路径")
    return local_assets


def run(command: list[str]) -> str:
    return subprocess.run(
        command, check=True, capture_output=True, text=True, encoding="utf-8", timeout=60
    ).stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--base-path", required=True)
    arguments = parser.parse_args()
    if not IMAGE_REFERENCE.fullmatch(arguments.image):
        parser.error("镜像引用格式无效")
    if not COMMIT.fullmatch(arguments.commit):
        parser.error("提交必须是40位小写十六进制")
    try:
        metadata = json.loads(run(["docker", "image", "inspect", arguments.image]))[0]
        result = verify_metadata(metadata, arguments.version, arguments.commit)
        index_html = run([
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--entrypoint", "cat", arguments.image, "/app/frontend/dist/index.html",
        ])
        result["frontend_assets"] = verify_frontend_index(index_html, arguments.base_path)
        result["base_path"] = normalized_base_path(arguments.base_path)
    except (ValueError, KeyError, IndexError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"镜像核对未通过：{type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
