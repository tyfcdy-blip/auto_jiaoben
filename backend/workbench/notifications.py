"""平台告警与业务结果投递；网络请求在事务外，通知失败不重跑程序。"""
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from .db import lock
from .models import Alert, Channel, Delivery, ResultNotification
from .notification_policy import SEVERITY_LABEL, alert_severity
from .scheduling import watermark


def signed_url(url, secret, now):
    if not secret:
        return url
    timestamp = str(int(now * 1000))
    signature = base64.b64encode(hmac.new(secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256).digest()).decode()
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key not in {"timestamp", "sign"}]
    query.extend([("timestamp", timestamp), ("sign", signature)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _post_text(client, encrypted_config, settings, now, content):
    config = json.loads(Fernet(settings.secret_key.encode()).decrypt(encrypted_config.encode()))
    response = client.post(signed_url(config["url"], config["secret"], now),
                           json={"msgtype": "text", "text": {"content": content}})
    response.raise_for_status()
    return response.json()


@dataclass(frozen=True)
class TestMessageResult:
    sent: bool
    reason: str = ""
    status_code: int | None = None
    dingtalk_code: int | None = None


def send_test_message(encrypted_config, settings, test_id, client=None, now=None):
    """单次探测；不入告警队列、不重试，避免响应丢失时重复向群发送。"""
    now = time.time() if now is None else now
    content = ("自动化工作台连接测试\n"
               "这是一条由项目管理员手动发出的测试消息。\n"
               f"测试编号：{test_id}")
    owned_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(10), follow_redirects=False, trust_env=False)
    try:
        body = _post_text(client, encrypted_config, settings, now, content)
        if not isinstance(body, dict):
            return TestMessageResult(False, "invalid_response")
        if type(body.get("errcode")) is not int or body["errcode"] != 0:
            code = body.get("errcode")
            return TestMessageResult(False, "rejected", dingtalk_code=code if type(code) is int else None)
        return TestMessageResult(True)
    except httpx.HTTPStatusError as failure:
        return TestMessageResult(False, "http_error", status_code=failure.response.status_code)
    except httpx.RequestError:
        return TestMessageResult(False, "delivery_unknown")
    except (ValueError, KeyError, InvalidToken):
        return TestMessageResult(False, "invalid_config_or_response")
    finally:
        if owned_client:
            client.close()


def _refresh_alert_delivery_state(db, alert_id):
    states = list(db.scalars(select(Delivery.state).where(Delivery.alert_id == alert_id)))
    item = db.get(Alert, alert_id)
    item.delivery_state = ("failed" if "failed" in states else
                           "pending" if any(state in {"pending", "retry", "sending"} for state in states) else
                           "sent" if "sent" in states else "disabled")


def send_one(factory, settings, client=None, now=None):
    now = now or time.time()
    with factory.begin() as db:
        lock(db)
        alert_delivery = db.scalar(select(Delivery).where(
            Delivery.state.in_(["pending", "retry", "sending"]),
            Delivery.next_try <= now, Delivery.leased_until <= now,
        ).order_by(Delivery.created_at).limit(1))
        result_delivery = db.scalar(select(ResultNotification).where(
            ResultNotification.delivery_state.in_(["pending", "retry", "sending"]),
            ResultNotification.next_try <= now, ResultNotification.leased_until <= now,
        ).order_by(ResultNotification.created_at).limit(1))
        if alert_delivery is None and result_delivery is None:
            watermark(db, "notifier", now)
            return False
        is_result = result_delivery is not None and (alert_delivery is None or
                                                     result_delivery.created_at < alert_delivery.created_at)
        delivery = result_delivery if is_result else alert_delivery
        channel = db.get(Channel, delivery.channel_id)
        item = None if is_result else db.get(Alert, delivery.alert_id)
        if not channel.enabled:
            if is_result:
                delivery.delivery_state = "disabled"
            else:
                delivery.state = "disabled"
                db.flush()
                _refresh_alert_delivery_state(db, item.id)
            watermark(db, "notifier", now)
            return True
        # 恢复通知不能越过同一通道尚在发送或重试的原告警，否则群内会先看到恢复、后看到故障。
        if not is_result and item.kind == "recovery" and item.event_key.startswith("recovery:"):
            original_alert_id = item.event_key.removeprefix("recovery:")
            original = db.scalar(select(Delivery).where(
                Delivery.alert_id == original_alert_id,
                Delivery.channel_id == delivery.channel_id,
            ))
            if original and original.state in {"pending", "retry", "sending"}:
                delivery.next_try = max(delivery.next_try, original.next_try, original.leased_until, now + 5)
                watermark(db, "notifier", now)
                return True
        if delivery.attempts >= 5:
            if is_result:
                delivery.delivery_state = "failed"
            else:
                delivery.state = "failed"
                db.flush()
                _refresh_alert_delivery_state(db, item.id)
            delivery.last_error = "发送预算耗尽，最后一次送达结果可能未知"
            watermark(db, "notifier", now)
            return True
        delivery.attempts += 1
        if is_result:
            delivery.delivery_state = "sending"
        else:
            delivery.state = "sending"
        delivery.leased_until = now+30
        delivery_id, attempt = delivery.id, delivery.attempts
        encrypted = channel.encrypted_config
        content = (delivery.content if is_result else
                   f"自动化工作台告警 · {SEVERITY_LABEL[alert_severity(item.kind)]}\n"
                   f"事件：{item.id}\n类型：{item.kind}\n状态：{item.status}\n"
                   f"{item.message}\n运行：{item.run_id or '无'}")
    error = None
    owned_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(10), follow_redirects=False, trust_env=False)
    try:
        body = _post_text(client, encrypted, settings, now, content)
        if (not isinstance(body, dict) or type(body.get("errcode")) is not int
                or body["errcode"] != 0):
            code = body.get("errcode") if isinstance(body, dict) else None
            error = (f"钉钉拒绝消息（错误码 {code}）；请核查机器人安全设置、限流和群状态"
                     if type(code) is int else "通知接口返回格式无效；请核查连接配置")
    except (httpx.HTTPError, ValueError, KeyError, InvalidToken):
        error = "通知发送失败或送达未知；保留事件编号有限重试"
    finally:
        if owned_client:
            client.close()
    with factory.begin() as db:
        lock(db)
        delivery = db.get(ResultNotification if is_result else Delivery, delivery_id)
        state = delivery.delivery_state if is_result else delivery.state
        if delivery.attempts == attempt and state == "sending":
            next_state = ("failed" if attempt >= 5 else "retry") if error else "sent"
            if is_result:
                delivery.delivery_state = next_state
            else:
                delivery.state = next_state
            delivery.last_error, delivery.leased_until = error, 0
            delivery.next_try = now + min(300, 5 * 2**attempt)
            if not is_result:
                db.flush()
                _refresh_alert_delivery_state(db, delivery.alert_id)
        watermark(db, "notifier", now)
    return True
