"""把程序和固定纯Python依赖打包成可直接上传工作台的ZIP。"""

import argparse
import json
from pathlib import Path
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parent
DEPENDENCIES = ("openpyxl", "et_xmlfile")


def dependency_locations(python: Path) -> dict:
    script = (
        "import importlib.metadata as m,json,pathlib; "
        "names=('openpyxl','et_xmlfile'); "
        "result={}; "
        "[(result.__setitem__(n,{'version':m.version(n),'root':str(pathlib.Path(m.distribution(n).locate_file(''))),"
        "'files':[str(p) for p in m.distribution(n).files]})) for n in names]; "
        "print(json.dumps(result))"
    )
    completed = subprocess.run([str(python), "-I", "-c", script], capture_output=True, text=True,
                               encoding="utf-8", timeout=30, check=False)
    if completed.returncode:
        raise RuntimeError("依赖Python中缺少固定版本openpyxl/et_xmlfile")
    return json.loads(completed.stdout)


def add_file(package: zipfile.ZipFile, source: Path, name: str) -> None:
    if source.is_symlink() or not source.is_file():
        raise RuntimeError(f"拒绝打包链接或非普通文件：{name}")
    info = zipfile.ZipInfo(name, date_time=(2026, 9, 17, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    package.writestr(info, source.read_bytes())


def build(output: Path, python: Path) -> None:
    if output.exists():
        raise FileExistsError("输出ZIP已存在，拒绝覆盖")
    locations = dependency_locations(python)
    expected = {"openpyxl": "3.1.5", "et_xmlfile": "2.0.0"}
    for name, version in expected.items():
        if locations[name]["version"] != version:
            raise RuntimeError(f"{name}版本必须固定为{version}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x") as package:
        add_file(package, ROOT / "main.py", "main.py")
        add_file(package, ROOT / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md")
        for dependency in DEPENDENCIES:
            metadata = locations[dependency]
            root = Path(metadata["root"])
            for relative in sorted(metadata["files"]):
                path = Path(relative)
                if ("__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}
                        or not path.parts or path.parts[0] not in {dependency, f"{dependency}-{metadata['version']}.dist-info"}):
                    continue
                add_file(package, root / path, path.as_posix())
    with zipfile.ZipFile(output) as package:
        names = package.namelist()
        total = sum(info.file_size for info in package.infolist())
        if names.count("main.py") != 1 or not any(name.startswith("openpyxl/") for name in names):
            raise RuntimeError("程序包缺少入口或依赖")
        if len(names) > 1000 or total > 100 * 1024 * 1024:
            raise RuntimeError("程序包超出执行器解压限制")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dependency-python", type=Path, required=True)
    options = parser.parse_args()
    build(options.output.resolve(), options.dependency_python.resolve())
    print(f"程序包已生成：{options.output.resolve()}")


if __name__ == "__main__":
    main()
