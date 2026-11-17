from contextlib import contextmanager
from sqlalchemy import create_engine, event, select, inspect
from sqlalchemy.orm import sessionmaker
from .models import Base, Gate, Node, NodeConfigState, NodeProjectGrant


def database(settings):
    if settings.database_url.startswith("sqlite"):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    sqlite = settings.database_url.startswith("sqlite")
    engine_options = {
        "pool_pre_ping": True,
        "connect_args": {"check_same_thread": False} if sqlite else {},
    }
    if not sqlite:
        engine_options.update(
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
        )
    engine = create_engine(settings.database_url, **engine_options)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def sqlite_setup(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
    return engine, sessionmaker(engine, expire_on_commit=False)


def initialize(engine, factory):
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name in existing:
            actual = {column["name"] for column in inspector.get_columns(table.name)}
            if set(table.columns.keys()) - actual:
                raise RuntimeError("既有数据库结构不兼容；禁止自动覆盖，请使用经验证的迁移或独立测试库")
    Base.metadata.create_all(engine)
    # create_all不会为已存在的表补索引；保留清理查询的索引须显式、幂等创建。
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            if index.name.startswith(("ix_retention_", "ix_run_file_options_")):
                index.create(engine, checkfirst=True)
    index_inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        expected = {index.name: [column.name for column in index.columns]
                    for index in table.indexes if index.name.startswith(("ix_retention_", "ix_run_file_options_"))}
        if not expected:
            continue
        actual = {index["name"]: index["column_names"] for index in index_inspector.get_indexes(table.name)}
        if any(actual.get(name) != columns for name, columns in expected.items()):
            raise RuntimeError("既有数据库保留策略索引不兼容；禁止使用同名错误索引")
    with factory.begin() as session:
        if session.get(Gate, 1) is None:
            session.add(Gate(id=1, schema_version=1, dispatch_enabled=True))
        for node in session.scalars(select(Node)).all():
            grant = session.scalar(select(NodeProjectGrant).where(
                NodeProjectGrant.node_id == node.id,
                NodeProjectGrant.project_id == node.project_id,
            ))
            if grant is None:
                session.add(NodeProjectGrant(node_id=node.id, project_id=node.project_id,
                                             input_roots=[], updated_by="migration"))
            if session.get(NodeConfigState, node.id) is None:
                session.add(NodeConfigState(node_id=node.id))


def lock(session):
    """一期控制写入串行化，事务持锁至提交；不靠进程内互斥保证多实例一致性。"""
    gate = session.scalar(select(Gate).where(Gate.id == 1).with_for_update())
    if gate is None or gate.schema_version != 1:
        raise RuntimeError("数据库尚未初始化或版本不兼容")
    return gate


@contextmanager
def transaction(factory):
    with factory.begin() as session:
        yield session
