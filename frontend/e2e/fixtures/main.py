"""无外部副作用的浏览器联调程序，仅向本次输出目录写入结果。"""

import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--context-file", required=True)
arguments = parser.parse_args()
context = json.loads(Path(arguments.context_file).read_text(encoding="utf-8"))
if context["operation"] == "simulate_failure":
    raise RuntimeError("合成验收主动失败，不涉及外部调用")
output = Path(context["output_dir"])
output.mkdir(parents=True, exist_ok=True)
(output / "report.csv").write_text("label,value\nsynthetic,1\n", encoding="utf-8")
report = {
    "protocol_version": "1",
    "run_id": context["run_id"],
    "attempt_id": context["attempt_id"],
    "status": "success",
    "summary": "无副作用联调程序已完成",
    "artifacts": [{"name": "report.csv", "required": True}],
}
temporary = output / "result.tmp"
temporary.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
temporary.replace(output / "result.json")
print("浏览器联调程序完成", flush=True)
