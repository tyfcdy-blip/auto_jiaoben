"""无外部接口、无真实业务写入的接入示例，只输出当前运行的产物。"""

import argparse
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-file", required=True, type=Path)
    args = parser.parse_args()
    context = json.loads(args.context_file.read_text(encoding="utf-8"))
    output = Path(context["output_dir"])
    greeting = str(context["parameters"].get("greeting", "工作台接入验证"))
    print(f"已收到参数，输入文件数量：{len(context['input_manifest'])}", flush=True)
    (output / "summary.txt").write_text(greeting, encoding="utf-8")
    result = {"protocol_version": "1", "run_id": context["run_id"], "attempt_id": context["attempt_id"],
              "status": "success", "summary": "无副作用示例完成", "metrics": {"input_files": len(context["input_manifest"])},
              "artifacts": [{"name": "summary.txt", "required": True}]}
    temporary = output / "result.json.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(output / "result.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
