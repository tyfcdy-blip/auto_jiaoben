import hashlib
import json
import time

from sqlalchemy import select

from .models import NodeConfigState, NodeProjectGrant


CONFIG_CAPABILITY = "node-config-v1"


def configuration_payload(revision: int, input_roots: list[str]) -> dict:
    return {"revision": revision, "input_roots": input_roots}


def configuration_hash(revision: int, input_roots: list[str]) -> str:
    encoded = json.dumps(configuration_payload(revision, input_roots), ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def grant_for(db, node_id: str, project_id: str, *, enabled: bool = True):
    query = select(NodeProjectGrant).where(NodeProjectGrant.node_id == node_id,
                                           NodeProjectGrant.project_id == project_id)
    if enabled:
        query = query.where(NodeProjectGrant.enabled.is_(True))
    return db.scalar(query)


def active_grants(db, node_id: str) -> list[NodeProjectGrant]:
    return db.scalars(select(NodeProjectGrant).where(
        NodeProjectGrant.node_id == node_id,
        NodeProjectGrant.enabled.is_(True),
    ).order_by(NodeProjectGrant.last_claimed_at.asc().nullsfirst(),
               NodeProjectGrant.created_at, NodeProjectGrant.id)).all()


def config_state(db, node_id: str) -> NodeConfigState:
    state = db.get(NodeConfigState, node_id)
    if state is None:
        state = NodeConfigState(node_id=node_id)
        db.add(state)
        db.flush()
    return state


def publish_configuration(state: NodeConfigState, input_roots: list[str]) -> None:
    state.desired_revision = (state.desired_revision or 0) + 1
    state.desired_input_roots = list(input_roots)
    state.desired_hash = configuration_hash(state.desired_revision, state.desired_input_roots)
    state.apply_status = "pending"
    state.apply_error = ""


def report_configuration(state: NodeConfigState, *, revision: int | None, config_hash: str | None,
                         input_roots: list[str] | None, root_status: list[dict]) -> None:
    state.reported_at = time.time()
    if input_roots is not None:
        state.reported_input_roots = list(input_roots)
    state.root_status = root_status
    if (revision is not None and config_hash and revision == state.desired_revision
            and config_hash == state.desired_hash):
        state.applied_revision = revision
        state.applied_hash = config_hash
        state.apply_status = "applied"
        state.apply_error = ""


def configuration_ready(state: NodeConfigState) -> bool:
    return (state.desired_revision > 0 and state.applied_revision == state.desired_revision
            and state.applied_hash == state.desired_hash and state.apply_status == "applied")
