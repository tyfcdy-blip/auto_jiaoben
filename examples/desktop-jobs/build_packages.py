"""创建三个独立程序包与可粘贴的界面参数定义，不覆盖已有交付目录。"""
import argparse
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent
JOBS = {"local-files": "本机文件生成复制与打包", "open-web": "打开网页并读取标题",
        "web-screenshot": "网页截图与工作台通知"}


def build(output: Path) -> list[dict]:
    if output.exists():
        raise ValueError("交付目录已存在，拒绝覆盖")
    output.mkdir(parents=True)
    programs = []
    for slug, name in JOBS.items():
        browser = slug != "local-files"
        properties = {"url": {"type": "string", "enum": ["", "https://example.com/"],
                              "title": "测试页（留空离线；联网仅 example.com）"},
                      "wait_seconds": {"type": "integer", "minimum": 1, "maximum": 30,
                                       "title": "页面停留秒数"}} if browser else {}
        schema = {"type": "object", "properties": properties, "additionalProperties": False}
        defaults = {"url": "", "wait_seconds": 10 if slug == "open-web" else 1} if browser else {}
        for suffix, value in (("parameter-schema", schema), ("default-parameters", defaults)):
            (output / f"{slug}.{suffix}.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        with zipfile.ZipFile(output / f"{slug}.zip", "x", zipfile.ZIP_DEFLATED) as archive:
            archive.write(ROOT / slug / "main.py", "main.py")
            for filename in ("job_runtime.py", "demo.html", "requirements.txt", "THIRD_PARTY_NOTICES.txt"):
                archive.write(ROOT / filename, filename)
        programs.append({"name": name, "zip": f"{slug}.zip", "command": ["python", "main.py"],
                         "side_effects": False, "result_required": True, "parameter_schema": schema,
                         "example_parameters": defaults, "suggested_timeout_seconds": 90})
    (output / "programs.json").write_text(json.dumps(programs, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copyfile(ROOT / "README.txt", output / "使用说明.txt")
    shutil.copyfile(ROOT / "requirements.txt", output / "requirements.txt")
    return programs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output.resolve())
