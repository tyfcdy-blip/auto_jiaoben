"""向明确指定的空目录生成合成输入/独立程序包，拒绝覆盖已有文件。"""

import argparse
import csv
import json
from pathlib import Path
import uuid
import zipfile


JOBS = (
    ("csv-merge", "Python CSV 合并", "python", "main.py"),
    ("node-summary", "Node.js 目录统计", "node", "main.js"),
    ("fail", "Python 主动失败", "python", "main.py"),
    ("wait", "Python 等待与取消", "python", "main.py"),
)


def checked_empty(value: str) -> Path:
    directory = Path(value).absolute()
    if directory.resolve() != directory or directory.is_symlink():
        raise ValueError("目标目录不能经过链接或包含越界段")
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise ValueError("目标目录必须不存在或为空，拒绝覆盖任何文件")
    return directory


def generate_inputs(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "region-b").mkdir()
    for filename, rows in (
        ("region-a.csv", [["order_id", "amount"], ["SYNTHETIC-A", "10.25"], ["SYNTHETIC-B", "20.50"]]),
        ("region-b/region-b.csv", [["order_id", "amount"], ["SYNTHETIC-C", "8.75"]]),
    ):
        with (directory / filename).open("x", encoding="utf-8-sig", newline="") as handle:
            csv.writer(handle).writerows(rows)
    with (directory / "metadata.json").open("x", encoding="utf-8") as handle:
        json.dump({"synthetic": True, "description": "仅用于平台目录输入验证"}, handle, ensure_ascii=False)
    with (directory / "说明.txt").open("x", encoding="utf-8") as handle:
        handle.write("合成目录：2 个 CSV、1 个 JSON、1 个文本文件。\n")


def package_jobs(directory: Path, input_dir: Path | None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    examples = []
    for slug, name, runtime, entry in JOBS:
        with zipfile.ZipFile(directory / f"{slug}.zip", "x", compression=zipfile.ZIP_DEFLATED) as package:
            package.write(Path(__file__).parent / slug / entry, entry)
        properties = {"input_dir": {"type": "string", "minLength": 1, "maxLength": 2000},
                      "batch_key": {"type": "string", "minLength": 1, "maxLength": 180}}
        if slug == "wait":
            properties["wait_seconds"] = {"type": "number", "minimum": 1, "maximum": 600}
        parameters = {"batch_key": f"synthetic-{slug}-{uuid.uuid4().hex}"}
        if input_dir:
            parameters["input_dir"] = str(input_dir)
        if slug == "wait":
            parameters["wait_seconds"] = 30
        examples.append({"name": name, "zip": f"{slug}.zip", "command": [runtime, entry],
                         "side_effects": False, "result_required": True,
                         "required_capabilities": [runtime, "directory-input-v1"], "protocol_version": "1",
                         "parameter_schema": {"type": "object", "x-workbench-input": "directory",
                                              "properties": properties,
                                              "required": ["input_dir", "batch_key"],
                                              "additionalProperties": False},
                         "example_parameters": parameters,
                         "suggested_timeout_seconds": 3 if slug == "wait" else 60})
    with (directory / "programs.json").open("x", encoding="utf-8") as handle:
        json.dump(examples, handle, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", help="生成合成输入的空目录")
    parser.add_argument("--bundle-dir", help="存放四个 ZIP 和 programs.json 的空目录")
    options = parser.parse_args()
    if not options.input_dir and not options.bundle_dir:
        parser.error("至少指定 --input-dir 或 --bundle-dir")
    inputs = checked_empty(options.input_dir) if options.input_dir else None
    bundles = checked_empty(options.bundle_dir) if options.bundle_dir else None
    if inputs and bundles and (inputs.is_relative_to(bundles) or bundles.is_relative_to(inputs)):
        parser.error("输入目录和程序包目录必须互不包含")
    if inputs:
        generate_inputs(inputs)
        print(f"合成输入已生成：{inputs}")
    if bundles:
        package_jobs(bundles, inputs)
        print(f"四个独立程序包及接入参数已生成：{bundles}")


if __name__ == "__main__":
    main()
