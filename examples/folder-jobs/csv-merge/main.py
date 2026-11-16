"""读取平台目录清单中的 CSV，按一致表头合并；仅使用 Python 标准库。"""

import argparse
import csv
import hashlib
import json
from pathlib import Path


def merge(context: dict, output: Path) -> dict:
    parameters = context["parameters"]
    root = Path(parameters["input_dir"])
    if not root.is_absolute() or not root.is_dir() or not parameters.get("batch_key"):
        raise ValueError("input_dir 必须是存在的绝对目录，batch_key 不能为空")
    root = root.resolve()
    entries = sorted(context["input_manifest"], key=lambda entry: entry["name"])
    sources = [entry for entry in entries if entry["name"].lower().endswith(".csv")]
    if not sources:
        raise ValueError("输入清单中没有 CSV 文件")
    header = None
    rows = 0
    temporary = output / "merged.csv.tmp"
    try:
        with temporary.open("x", encoding="utf-8-sig", newline="") as destination:
            writer = csv.writer(destination)
            for entry in sources:
                path = Path(entry["path"])
                if not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("输入清单中的文件超出指定目录")
                with path.open("rb") as source:
                    if hashlib.file_digest(source, "sha256").hexdigest() != entry["sha256"]:
                        raise ValueError("输入文件内容与清单校验值不一致")
                with path.open(encoding="utf-8-sig", newline="") as source:
                    reader = csv.reader(source, strict=True)
                    current = next(reader, None)
                    if not current or any(not field.strip() for field in current):
                        raise ValueError("CSV 缺少有效表头")
                    if header is None:
                        header = current
                        writer.writerow(header)
                    elif current != header:
                        raise ValueError("CSV 表头不一致")
                    for row in reader:
                        if len(row) != len(header):
                            raise ValueError("CSV 数据行列数与表头不一致")
                        writer.writerow(row)
                        rows += 1
                print(f"processed_csv={entry['name']} total_rows={rows}", flush=True)
        temporary.replace(output / "merged.csv")
    finally:
        temporary.unlink(missing_ok=True)
    return {"status": "success", "summary": f"已合并 {len(sources)} 个 CSV，共 {rows} 行数据",
            "metrics": {"files": len(sources), "rows": rows, "batch_key": parameters["batch_key"]},
            "artifacts": [{"name": "merged.csv", "required": True}]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-file", required=True)
    context = json.loads(Path(parser.parse_args().context_file).read_text(encoding="utf-8"))
    output = Path(context["output_dir"])
    if not output.is_absolute() or not output.is_dir():
        raise ValueError("平台输出目录必须已存在且为绝对路径")
    if output.resolve().is_relative_to(Path(context["parameters"]["input_dir"]).resolve()):
        raise ValueError("输出目录不能位于只读输入目录内")
    try:
        result = merge(context, output)
        code = 0
    except (OSError, ValueError, KeyError, TypeError, csv.Error) as error:
        result = {"status": "failed", "summary": f"CSV 合并失败：{error}", "artifacts": []}
        print(result["summary"], flush=True)
        code = 2
    result.update(protocol_version="1", run_id=context["run_id"], attempt_id=context["attempt_id"])
    temporary = output / "result.json.tmp"
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    temporary.replace(output / "result.json")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
