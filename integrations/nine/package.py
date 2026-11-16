"""从显式nine源目录复制两份已审核脚本；绝不扫描或复制配置、Excel及输出。"""

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


HERE = Path(__file__).resolve().parent
SOURCE_FILES = ("pdd_billing_check.py", "pdd_billing_warehouse_load.py")
ADAPTER_FILES = ("adapter.py", "legacy_launcher.py", "parameters.schema.json", "requirements.txt")


def build(source: Path, destination: Path) -> None:
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination.is_relative_to(source):
        raise ValueError("输出必须在原项目目录之外")
    payloads = {}
    for name in SOURCE_FILES:
        path = source / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise ValueError("原程序文件缺失、为链接或超过限制")
        payload = path.read_bytes()
        compile(payload, name, "exec")
        payloads[name] = payload
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in payloads.items():
            archive.writestr("legacy/" + name, payload)
        for name in ADAPTER_FILES:
            archive.write(HERE / name, name)
        archive.writestr("source-manifest.json", json.dumps({
            "format": 1, "sha256": {name: hashlib.sha256(content).hexdigest() for name, content in payloads.items()},
            "operations": ["check", "preflight"],
            "source_notice": "用户提供的内部程序；保留原内容，不包含数据或凭据；未确认可公开再分发许可",
        }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.output)
    print(json.dumps({"package": str(args.output.resolve()), "operations": ["check", "preflight"]}))


if __name__ == "__main__":
    main()
