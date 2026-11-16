import hashlib
import secrets
import time
from dataclasses import dataclass
from fastapi import Depends, Request
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from sqlalchemy import select
from .models import Audit, Caller, LoginSession, Member, Node, User

hasher = PasswordHasher()


class Problem(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def token_pair():
    token = secrets.token_urlsafe(40)
    return token, digest(token)


def verify_password(encoded, password):
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def get_db(request: Request):
    with request.app.state.factory.begin() as session:
        yield session


@dataclass
class Identity:
    kind: str
    record: User | Node | Caller
    session_id: str | None = None

    @property
    def actor(self):
        return f"{self.kind}:{self.record.id}"


def identity(request: Request, db=Depends(get_db, scope="function")):
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer ") or len(header) > 300:
        raise Problem(401, "unauthorized", "请登录或提供有效令牌")
    hashed = digest(header[7:])
    login = db.scalar(select(LoginSession).where(LoginSession.token_hash == hashed,
                                                LoginSession.expires_at > time.time()))
    if login:
        user = db.get(User, login.user_id)
        if user and user.active:
            return Identity("user", user, login.id)
    node = db.scalar(select(Node).where(Node.token_hash == hashed, Node.enabled.is_(True)))
    if node:
        return Identity("node", node)
    caller = db.scalar(select(Caller).where(Caller.token_hash == hashed, Caller.active.is_(True)))
    if caller:
        return Identity("caller", caller)
    raise Problem(401, "unauthorized", "身份无效或已停用")


def human(who=Depends(identity)):
    if who.kind != "user":
        raise Problem(403, "forbidden", "需要人员账号")
    return who


def administrator(who=Depends(human)):
    if not who.record.is_admin:
        raise Problem(403, "forbidden", "需要平台管理员权限")
    return who


def node_identity(who=Depends(identity)):
    if who.kind != "node":
        raise Problem(403, "forbidden", "需要执行节点身份")
    return who


def write_lock(db, who):
    """锁等待/接收文件期间可能撤权；在实际写入的串行边界重读身份。"""
    from .db import lock
    gate = lock(db)
    db.refresh(who.record)
    enabled = who.record.enabled if who.kind == "node" else who.record.active
    if not enabled:
        raise Problem(401, "unauthorized", "身份已停用，写入已阻断")
    if who.kind == "user" and who.session_id:
        login = db.scalar(select(LoginSession).where(LoginSession.id == who.session_id,
                                                    LoginSession.expires_at > time.time()))
        if not login:
            raise Problem(401, "unauthorized", "会话已失效，写入已阻断")
    return gate


ROLE_LEVEL = {"viewer": 0, "operator": 1, "developer": 2, "project_admin": 3}


def project_role(db, who, project_id):
    if who.kind != "user":
        raise Problem(403, "forbidden", "需要人员账号")
    if who.record.is_admin:
        return "project_admin"
    member = db.scalar(select(Member).where(Member.user_id == who.record.id, Member.project_id == project_id))
    if not member:
        raise Problem(404, "not_found", "资源不存在或无权访问")
    return member.role


def authorize(db, who, project_id, level="viewer"):
    role = project_role(db, who, project_id)
    if ROLE_LEVEL[role] < ROLE_LEVEL[level]:
        raise Problem(403, "forbidden", "当前角色没有该操作权限")


def audit(db, who, action, resource_id, project_id=None, note=""):
    db.add(Audit(actor=who.actor if isinstance(who, Identity) else who, action=action,
                 resource_id=resource_id, project_id=project_id, note=note[:2000]))


def require(db, model, identifier):
    record = db.get(model, identifier)
    if record is None:
        raise Problem(404, "not_found", "资源不存在或无权访问")
    return record
