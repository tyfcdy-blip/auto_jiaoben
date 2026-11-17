"""执行电脑演示共享实现；仅输出本次产物，不持有业务或机器人凭据。"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
from typing import Any
from urllib.parse import urlsplit
import zipfile


class ConfigurationError(ValueError):
    """仅包含本程序控制的安全提示，不引用网页地址或凭据。"""


def write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    temporary.replace(path)


def local_files(output: Path) -> dict[str, Any]:
    sources = output / "synthetic-source"
    copies = output / "synthetic-copy"
    sources.mkdir()
    copies.mkdir()
    entries = []
    with zipfile.ZipFile(output / "local-files.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for index in range(1, 4):
            name = f"sample-{index}.txt"
            source = sources / name
            with source.open("x", encoding="utf-8") as stream:
                stream.write(f"工作台合成本机操作测试，第 {index} 份文件。\n")
            target = copies / name
            shutil.copyfile(source, target)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            entries.append({"name": name, "sha256": digest, "bytes": target.stat().st_size})
            archive.write(target, name)
    write_json(output / "file-manifest.json", {"synthetic": True, "files": entries})
    return {"summary": "已生成并复制 3 份测试文件，完成 ZIP 打包", "metrics": {"files": 3},
            "artifacts": [{"name": name, "required": True}
                          for name in ("local-files.zip", "file-manifest.json")]}


def browser_options() -> dict[str, str]:
    # 执行器更换 USERPROFILE 且不传递 LOCALAPPDATA，优先定位系统安装的 Edge。
    for location in ("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                     "C:/Program Files/Microsoft/Edge/Application/msedge.exe"):
        if Path(location).is_file():
            return {"executable_path": location}
    return {"channel": "msedge"}


def page_url(parameters: dict[str, Any]) -> str:
    url = parameters.get("url", "")
    if not isinstance(url, str) or len(url) > 2000:
        raise ConfigurationError("url 必须是长度不超过 2000 的字符串")
    if not url:
        return Path(__file__).with_name("demo.html").resolve().as_uri()
    if url != "https://example.com/":
        raise ConfigurationError("此演示仅支持空地址（离线测试页）或 https://example.com/")
    return url


def request_allowed(method: str, request_url: str, initial_url: str) -> bool:
    """离线页面禁止联网；联网演示仅访问固定站点，阻止跨域跳转及子资源。"""
    if method not in {"GET", "HEAD"}:
        return False
    if initial_url != "https://example.com/":
        return request_url == initial_url
    try:
        parts = urlsplit(request_url)
        return (parts.scheme == "https" and parts.hostname == "example.com"
                and parts.port in {None, 443} and not parts.username and not parts.password)
    except ValueError:
        return False


def visit_page(parameters: dict[str, Any], output: Path, screenshot: bool,
               cancel_file: Path) -> dict[str, Any]:
    url = page_url(parameters)
    wait = parameters.get("wait_seconds", 1 if screenshot else 10)
    if isinstance(wait, bool) or not isinstance(wait, int) or not 1 <= wait <= 30:
        raise ConfigurationError("wait_seconds 必须是 1 至 30 的整数")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=screenshot, timeout=20000, **browser_options())
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 720},
                                          java_script_enabled=False, service_workers="block",
                                          accept_downloads=False)
            # 示例仅作页面阅读，禁止表单或脚本发起写请求，不加载其他本机文件。
            def read_only_route(route: Any) -> None:
                request = route.request
                if not request_allowed(request.method, request.url, url):
                    route.abort()
                else:
                    route.continue_()
            context.route("**/*", read_only_route)
            page = context.new_page()
            response = page.goto(url, wait_until="load", timeout=20000)
            if response is not None and response.status >= 400:
                raise ConfigurationError(f"网页返回 HTTP {response.status}")
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                if cancel_file.exists():
                    return {"status": "canceled", "summary": "网页任务已响应取消", "artifacts": []}
                page.wait_for_timeout(100)
            info = {"title": page.title()[:500], "source": "offline-demo" if not parameters.get("url")
                    else urlsplit(url).hostname, "viewport": {"width": 1280, "height": 720}}
            write_json(output / "page-info.json", info)
            artifacts = [{"name": "page-info.json", "required": True}]
            if screenshot:
                page.screenshot(path=str(output / "page.png"), full_page=False, timeout=10000)
                artifacts.append({"name": "page.png", "required": True})
            return {"summary": "网页截图已保存" if screenshot else "已打开网页并完成读取，测试窗口已关闭",
                    "notification_text": "执行电脑已完成网页截图，请进入本次运行详情下载 page.png。"
                    if screenshot else "执行电脑已完成打开网页测试。",
                    "metrics": {"screenshot": screenshot}, "artifacts": artifacts}
        finally:
            browser.close()


def main(job: str) -> int:
    if job not in {"local-files", "open-web", "web-screenshot"}:
        raise ConfigurationError("未定义的测试程序")
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-file", required=True, type=Path)
    context = json.loads(parser.parse_args().context_file.read_text(encoding="utf-8"))
    output = Path(context["output_dir"])
    if not output.is_absolute() or not output.is_dir() or output.is_symlink() or any(output.iterdir()):
        raise ValueError("输出目录必须是工作台为本次运行创建的空绝对目录")
    parameters = context["parameters"]
    try:
        if not isinstance(parameters, dict):
            raise ConfigurationError("参数必须为对象")
        allowed = set() if job == "local-files" else {"url", "wait_seconds"}
        if set(parameters) - allowed:
            raise ConfigurationError("存在未定义的业务参数")
        result = local_files(output) if job == "local-files" else visit_page(
            parameters, output, job == "web-screenshot", Path(context["cancel_file"]))
        result.setdefault("status", "success")
        code = 130 if result["status"] == "canceled" else 0
    except Exception as error:
        # 第三方浏览器异常可能包含完整 URL/查询字符串，只记录类型和固定提示。
        detail = str(error) if isinstance(error, ConfigurationError) else (
            f"{type(error).__name__}：请检查浏览器路径、Playwright 依赖、网络与桌面会话。")
        result = {"status": "failed", "summary": f"测试未完成：{detail}", "artifacts": []}
        code = 2
    result.update(protocol_version="1", run_id=context["run_id"], attempt_id=context["attempt_id"])
    write_json(output / "result.json", result)
    print(result["summary"], flush=True)
    return code
