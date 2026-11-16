import time
from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy import select, delete, func
from . import schemas as S
from .db import lock
from .models import User, LoginSession, LoginAttempt, Project, Member
from .security import (Problem, administrator, audit, authorize, digest, get_db, hasher,
                       human, project_role, require, token_pair, verify_password, write_lock)
from .views import listing, public

router = APIRouter()


@router.post("/auth/login")
def login(payload: S.Login, request: Request):
    settings = request.app.state.settings
    denied = False
    with request.app.state.factory.begin() as db:
        lock(db)
        key = digest((request.client.host if request.client else "local") + ":" + payload.username)
        attempts = db.get(LoginAttempt, key)
        if attempts and attempts.blocked_until > time.time():
            raise Problem(429, "login_throttled", "登录失败次数过多，请稍后再试")
        user = db.scalar(select(User).where(User.username == payload.username))
        if not user or not verify_password(user.password_hash, payload.password) or not user.active:
            if attempts is None:
                attempts = LoginAttempt(id=key, failures=0, blocked_until=0)
                db.add(attempts)
            attempts.failures += 1
            if attempts.failures >= 5:
                attempts.blocked_until = time.time() + 300
                attempts.failures = 0
            denied = True
        else:
            if attempts:
                db.delete(attempts)
            token, hashed = token_pair()
            db.add(LoginSession(token_hash=hashed, user_id=user.id, expires_at=time.time()+settings.session_seconds))
            audit(db, "user:"+user.id, "login", user.id)
            response = {"token": token, "user": public(user)}
    if denied:
        raise Problem(401, "invalid_login", "账号或密码错误")
    return response


@router.get("/auth/me")
def me(who=Depends(human)):
    return public(who.record)


@router.post("/auth/logout")
def logout(request: Request, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    db.execute(delete(LoginSession).where(LoginSession.token_hash == digest(request.headers["authorization"][7:])))
    return {"ok": True}


@router.get("/users")
def users(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
          who=Depends(administrator), db=Depends(get_db, scope="function")):
    return listing(db, select(User).order_by(User.created_at.desc()), limit, offset)


@router.post("/users", status_code=201)
def create_user(payload: S.UserCreate, who=Depends(administrator), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    if db.scalar(select(User.id).where(User.username == payload.username)):
        raise Problem(409, "duplicate_user", "账号已存在")
    user = User(username=payload.username, display_name=payload.display_name,
                password_hash=hasher.hash(payload.password), is_admin=payload.is_admin)
    db.add(user)
    db.flush()
    audit(db, who, "create_user", user.id)
    return public(user)


@router.patch("/users/{user_id}")
def change_user(user_id: str, payload: S.Active, who=Depends(administrator), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    user = require(db, User, user_id)
    if not payload.active and user.is_admin:
        admins = db.scalar(select(func.count()).select_from(User).where(User.is_admin.is_(True), User.active.is_(True)))
        if admins <= 1 or user.id == who.record.id:
            raise Problem(409, "last_administrator", "不能停用当前账号或最后一个管理员")
    user.active = payload.active
    if not user.active:
        db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    audit(db, who, "update_user", user.id)
    return public(user)


@router.get("/projects")
def projects(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
             who=Depends(human), db=Depends(get_db, scope="function")):
    query = select(Project).order_by(Project.created_at)
    if not who.record.is_admin:
        query = query.where(Project.id.in_(select(Member.project_id).where(Member.user_id == who.record.id)))
    return listing(db, query, limit, offset, transform=lambda project: {**public(project), "role": project_role(db, who, project.id)})


@router.post("/projects", status_code=201)
def create_project(payload: S.Name, who=Depends(administrator), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    if db.scalar(select(Project.id).where(Project.name == payload.name)):
        raise Problem(409, "duplicate_project", "项目名已存在")
    project = Project(name=payload.name)
    db.add(project)
    db.flush()
    audit(db, who, "create_project", project.id, project.id)
    return {**public(project), "role": "project_admin"}


@router.get("/projects/{project_id}/members")
def members(project_id: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
            who=Depends(human), db=Depends(get_db, scope="function")):
    authorize(db, who, project_id, "project_admin")
    return listing(db, select(Member).where(Member.project_id == project_id).order_by(Member.created_at, Member.id), limit, offset,
                   transform=lambda member: {**public(member), "username": db.get(User, member.user_id).username})


@router.post("/projects/{project_id}/members")
def add_member(project_id: str, payload: S.MemberCreate, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    require(db, Project, project_id)
    authorize(db, who, project_id, "project_admin")
    require(db, User, payload.user_id)
    member = db.scalar(select(Member).where(Member.user_id == payload.user_id, Member.project_id == project_id))
    if member:
        member.role = payload.role
    else:
        member = Member(project_id=project_id, **payload.model_dump())
        db.add(member)
    db.flush()
    audit(db, who, "set_member", member.id, project_id)
    return public(member)
