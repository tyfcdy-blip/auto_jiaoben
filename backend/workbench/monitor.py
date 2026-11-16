"""独立故障域监控：本地状态与独立通知通道，不访问平台数据库。"""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import sys
import time
from typing import Iterator
from urllib.parse import urlsplit
import uuid

import httpx

from .notifications import signed_url


@dataclass(frozen=True)
class MonitorConfig:
    target_base_url: str
    monitor_token: str
    state_file: Path
    webhook_url: str = ""
    signing_secret: str = ""
    interval_seconds: float = 30
    timeout_seconds: float = 5
    notification_budget: int = 3

    @property
    def target_key(self) -> str:
        return hashlib.sha256(self.target_base_url.encode()).hexdigest()


def load_config(path: Path) -> MonitorConfig:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    allowed = {"target_base_url", "monitor_token_file", "state_file", "webhook_file",
               "signing_secret_file", "interval_seconds", "timeout_seconds", "notification_budget"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError("配置包含未知字段")

    def local_file(key: str) -> Path:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"缺少文件路径：{key}")
        return (path.parent / value).resolve()

    def secret(key: str) -> str:
        value = local_file(key).read_text(encoding="utf-8-sig").strip()
        if not value or len(value) > 8192 or "\n" in value or "\r" in value:
            raise ValueError(f"凭据文件格式错误：{key}")
        return value

    target = raw.get("target_base_url")
    if not isinstance(target, str):
        raise ValueError("需要目标服务地址")
    target = target.rstrip("/")
    parsed = urlsplit(target)
    try:
        loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = False
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.scheme not in {"http", "https"} or (parsed.scheme == "http" and not loopback)):
        raise ValueError("远端目标必须使用 HTTPS；HTTP 仅允许本机回环地址")
    parsed.port  # 立即拒绝非法端口，避免在运行时输出含凭据的 URL。
    webhook = secret("webhook_file") if raw.get("webhook_file") else ""
    signing = secret("signing_secret_file") if raw.get("signing_secret_file") else ""
    if signing and not webhook:
        raise ValueError("签名密钥必须配合 Webhook")
    if webhook:
        parts = urlsplit(webhook)
        if (parts.scheme != "https" or parts.hostname != "oapi.dingtalk.com"
                or parts.port not in {None, 443} or parts.username or parts.password
                or parts.fragment or parts.path != "/robot/send"):
            raise ValueError("只支持钉钉群机器人 HTTPS Webhook")
    interval = raw.get("interval_seconds", 30)
    timeout = raw.get("timeout_seconds", 5)
    budget = raw.get("notification_budget", 3)
    if (type(interval) not in {int, float} or not 1 <= interval <= 86400
            or type(timeout) not in {int, float} or not 0.1 <= timeout <= 60
            or type(budget) is not int or not 1 <= budget <= 10):
        raise ValueError("间隔、超时或发送预算超出范围")
    state_file = local_file("state_file")
    protected = {path.resolve(), *(local_file(key) for key in (
        "monitor_token_file", "webhook_file", "signing_secret_file") if raw.get(key))}
    if state_file in protected or Path(str(state_file) + ".lock") in protected:
        raise ValueError("状态文件不得覆盖配置或凭据")
    return MonitorConfig(target, secret("monitor_token_file"), state_file, webhook, signing,
                         interval, timeout, budget)


@contextmanager
def state_lock(path: Path) -> Iterator[None]:
    """系统级文件锁随进程退出释放，避免重叠计划任务重复发送。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(path) + ".lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def save_state(path: Path, state: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_state(config: MonitorConfig) -> dict:
    if not config.state_file.exists():
        return {"version": 1, "target_key": config.target_key, "healthy": None, "event": None}
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or state.get("version") != 1
            or state.get("target_key") != config.target_key or type(state.get("healthy")) is not bool):
        raise ValueError("状态文件损坏或属于其他目标，拒绝重置去重记录")
    event = state.get("event")
    if event is not None and (not isinstance(event, dict) or event.get("kind") not in {"fault", "recovery"}
                             or type(event.get("attempts")) is not int or event["attempts"] < 0
                             or type(event.get("delivered")) is not bool
                             or type(event.get("next_attempt_at")) not in {int, float}
                             or not isinstance(event.get("id"), str)
                             or not isinstance(event.get("reason"), str)):
        raise ValueError("通知状态损坏，拒绝重置发送预算")
    return state


def check_health(config: MonitorConfig, client: httpx.Client) -> tuple[bool, str]:
    failures = []
    for endpoint, expected in (("ready", "ready"), ("components", "healthy")):
        try:
            response = client.get(f"{config.target_base_url}/api/v1/health/{endpoint}",
                                  headers={"Authorization": f"Bearer {config.monitor_token}"},
                                  timeout=config.timeout_seconds, follow_redirects=False)
            if response.status_code != 200:
                failures.append(f"{endpoint}:HTTP_{response.status_code}")
            else:
                body = response.json()
                if not isinstance(body, dict) or body.get("status") != expected:
                    failures.append(f"{endpoint}:invalid_response")
        except httpx.TimeoutException:
            failures.append(f"{endpoint}:timeout")
        except httpx.HTTPError:
            failures.append(f"{endpoint}:unreachable")
        except ValueError:
            failures.append(f"{endpoint}:invalid_response")
    return not failures, ";".join(failures) or "healthy"


def deliver(config: MonitorConfig, event: dict, client: httpx.Client, now: float) -> bool:
    """外部消息失败只写 stderr，不向同一通道创建二次告警。"""
    message = {"event_id": event["id"], "kind": event["kind"], "reason": event["reason"]}
    if not config.webhook_url:
        print(json.dumps(message, ensure_ascii=False), flush=True)
        return True
    content = (f"自动化工作台独立监控\n事件：{event['id']}\n"
               f"状态：{'故障' if event['kind'] == 'fault' else '恢复'}\n检查：{event['reason']}")
    try:
        response = client.post(signed_url(config.webhook_url, config.signing_secret, now),
                               json={"msgtype": "text", "text": {"content": content}},
                               timeout=config.timeout_seconds, follow_redirects=False)
        body = response.json() if response.status_code == 200 else None
        if isinstance(body, dict) and type(body.get("errcode")) is int and body["errcode"] == 0:
            return True
    except (httpx.HTTPError, ValueError):
        pass
    print(f"monitor_notification_failed event={event['id']} attempt={event['attempts']} "
          "送达失败或未知；不会递归告警", file=sys.stderr, flush=True)
    return False


def run_once(config: MonitorConfig, client: httpx.Client, now: float | None = None) -> int:
    now = time.time() if now is None else now
    with state_lock(config.state_file):
        state = load_state(config)
        healthy, reason = check_health(config, client)
        previous = state["healthy"]
        if previous != healthy and (not healthy or previous is False):
            # 恢复时丢弃尚未送达的旧故障消息，避免恢复以后继续发送过时告警。
            state["event"] = {"id": uuid.uuid4().hex, "kind": "recovery" if healthy else "fault",
                              "reason": reason, "attempts": 0, "delivered": False, "next_attempt_at": now}
        state.update(healthy=healthy, checked_at=now, reason=reason)
        save_state(config.state_file, state)
        event = state["event"]
        if (event and not event["delivered"] and event["attempts"] < config.notification_budget
                and now >= event["next_attempt_at"]):
            # 先落盘扣预算再发送；崩溃或超时可能已送达，重试保留同一事件编号。
            event["attempts"] += 1
            event["next_attempt_at"] = now + config.interval_seconds
            save_state(config.state_file, state)
            event["delivered"] = deliver(config, event, client, now)
            save_state(config.state_file, state)
        if (event and not event["delivered"] and event["attempts"] >= config.notification_budget
                and not event.get("budget_reported")):
            # 上一进程可能在扣完最后一次预算后崩溃，重启也必须报告送达未知。
            print(f"monitor_notification_exhausted event={event['id']}", file=sys.stderr, flush=True)
            event["budget_reported"] = True
            save_state(config.state_file, state)
        return 0 if healthy else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="独立工作台健康监控；不访问平台数据库")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--once", action="store_true", help="单轮检查，供操作系统计划任务使用")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config.resolve())
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            while True:
                result = run_once(config, client)
                if args.once:
                    return result
                time.sleep(config.interval_seconds)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, TypeError, KeyError):
        # 不打印异常原文，避免 URL、凭据或本机路径进入第三方采集日志。
        print("monitor_local_error 配置、状态或文件锁异常；请检查本机配置与权限", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
