# 钉钉通知工作包验证记录（2026-09-19）

## 任务闭环

执行模式：全过程开发；等级：L3（消息外发、权限和既有数据库新增表）。基准：仓库 `main` 尚无首个提交，`git status --short` 全部项目文件均为未跟踪，无法用 Git 计算本次差异。未提交或推送。经用户授权，已更新本机 `127.0.0.1:18080` Docker 测试实例。

| 需求 / 工作包 | 实现 | 用例 | 实际状态 |
| --- | --- | --- | --- |
| 告警类型、等级与按连接选择 | `notification_policy.py`、`execution.alert()`、策略 API 与告警页面 | DN-01/02/03 | 通过：新连接默认规则、旧连接兼容、修订冲突、恢复沿原连接 |
| 成功结果独立通知 | 任务通知配置、`complete()` 同事务入队、`ResultNotification` 与页面 | DN-04/05/06 | 通过：同项目权限、成功一次、失败不发结果、重复完成不重复、摘要凭据遮蔽 |
| 可靠投递与查询 | `send_one()`、两类逐条记录 API/页面 | DN-07 | 通过：模拟成功/失败重试、状态可查，业务运行不重跑 |
| 既有库增表兼容 | 仅新增三张表；`initialize()` 创建缺失表 | DN-09 | SQLite 与 PostgreSQL 隔离库通过；当前 Docker 数据库升级后新表 3 张，原有记录计数保持不变 |
| UI 端到端配置 | 通知页面、任务结果配置、独立浏览器场景 | DN-08 | 隔离 PostgreSQL 服务 + Chrome 通过；连接始终停用，无外发 |

## 测试证据

| 环境及命令 | 退出码 | 结果 |
| --- | ---: | --- |
| 本机 `.venv`：`python -m pytest tests/backend -q` | 0 | 99 通过、7 跳过（需要 PostgreSQL） |
| 隔离 PostgreSQL 17：`WORKBENCH_TEST_DATABASE_FILE=<本机私有文件> python -m pytest tests/backend -q` | 0 | 108 通过 |
| 最终改动后隔离 PostgreSQL 17：同变量下 `python -m pytest tests/backend/test_notification_policies.py tests/backend/test_alert_lifecycle.py tests/backend/test_scheduling_notifications.py -q` | 0 | 20 通过 |
| `ruff check backend tests/backend/test_notification_policies.py` | 0 | 通过 |
| `npm run test`（frontend） | 0 | 147 通过 |
| `npm run build`（frontend） | 0 | TypeScript 与 Vite 构建通过 |
| `npm run format:check` 及 `prettier --check e2e/notifications.mjs package.json` | 0 | 通过 |
| 隔离 PostgreSQL + Chrome：`npm run test:e2e:notifications` | 0 | UI 新增无加签连接、选告警类型、设置任务结果通知；连接未启用 |
| 全仓 `python -m pytest tests -q` | 1 | 302 通过、7 失败、36 跳过；6 项执行器 CLI 测试被当前用户正在运行的全局执行器锁阻挡，1 项安装器测试单独复跑通过。与本通知模块无共同代码路径，**不能记为全仓通过** |
| 旧版 UI 全量 `npm run test:e2e` | 1 | 前 10 场景通过，执行器阶段遇同一全局运行锁；通知专项 E2E 已另行通过 |
| `docker build -t automation-workbench:notification-test -f Dockerfile .` | 1 | Docker Hub 匿名令牌端点连接失败，未进入应用编译阶段；当前运行容器未受影响 |
| `docker build --build-arg NODE_IMAGE=public.ecr.aws/docker/library/node:22-bookworm-slim --build-arg PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim-bookworm -t automation-workbench:notification-test -f Dockerfile .` | 0 | 使用相同 Node/Python 主版本的备用官方镜像来源完成镜像构建；镜像 ID `sha256:aeb9603a…` |
| `docker compose --env-file deploy/.env.example -f deploy/compose.yaml config --quiet` | 0 | 本地 Compose 配置解析通过 |
| `docker run --rm --entrypoint python automation-workbench:notification-test -c "..."` | 0 | 容器中成功导入通知模块，确认三张新表已进入镜像模型注册 |
| `docker compose ... stop api worker`；`python deploy/operations.py backup --project automation-workbench-dev --env-file deploy/.env.example --directory <备份目录> --note <停机说明>` | 0 | 停机前活动任务 0、五分钟内计划 0、启用连接 0；备份数据库 129,794 字节和平台文件 337,920 字节，SHA-256 与 manifest 一致，tar 安全检查通过 |
| `docker compose ... up -d --no-build --force-recreate init-db api worker` | 0 | 本机测试实例已升级；旧镜像保留为 `automation-workbench:rollback-pre-notifications-20260919`，数据库容器未重建；`init-db` 退出码 0，API/worker 运行，API 健康 |
| `GET /api/v1/health/ready`、`GET /`、`GET /api/openapi.json`、前端 JS 资源 | 0 | 均返回 200；OpenAPI 含连接策略、任务结果配置、结果通知 3 个新路由；前端包含对应入口 |
| 升级前后数据库只读计数与新表查询 | 0 | `tasks=30`、`runs=49`、`alerts=89` 保持不变；新增三张通知表均存在；升级后执行器心跳与领取请求正常 |

所有自动化钉钉响应均用 `httpx.MockTransport`；没有向真实钉钉群发送消息。隔离 E2E 使用独立 PostgreSQL schema，测试后已核验名称并删除；会话秘密文件和测试程序 ZIP 已删除。未接触真实业务文件或生产数据库。

## 问题与风险

- P1 已修复并复验：脚本失败摘要原先可能直接进入告警群消息；现在所有告警在入库前共用凭据遮蔽，并以模拟群消息验证。
- P1 已修复并复验：无加签连接从旧页面发送 `null` 曾被后端拒绝；后端现接受并规范为无加签。
- P2 运行中配置语义：任务结果接收群在**运行完成时**决定；管理员在任务进行中修改设置会影响该运行。页面和使用说明已标明。若业务要求仅对后续受理生效，需另设计运行级快照。
- P2 外部送达语义：网络超时后群消息可能已送达，有限重试可能产生重复消息；使用运行/事件编号供核对，不能宣称恰好一次。
- P2 环境：当前电脑的执行器全局运行锁使全仓 agent CLI 回归不能通过；通知相关后端、前端及专项 E2E 已通过。
- 未验证：真实钉钉群 Webhook/加签、网络可达性和群消息实际呈现；备份恢复演练未执行。缺少获授权测试群连接，未执行外发。Docker Hub 当前不可达，已通过备用镜像来源完成独立构建。

升级前备份：`backups/notification-upgrade-20260919-133250/`，含 `database.dump`、`platform-data.tar` 和 `manifest.json`；不含执行电脑本地状态或解密秘密。回滚时先停用连接并停止 API/worker，再将 `automation-workbench:rollback-pre-notifications-20260919` 标记为 `dev` 重建服务；旧版本代码忽略新增表。若需恢复数据，须另外核对节点状态，按部署手册执行恢复；本次未演练恢复。已发出的群消息无法撤回。

## 质量状态

| 门禁 | 状态 |
| --- | --- |
| Codex 开发完成 | 是，功能代码及页面完成 |
| 测试计划/用例已生成 | 是，见 `dingtalk-notification-plan.md` |
| 自动化测试已执行 | 是，见上表；全仓测试未通过 |
| 人工验证已执行 | 否 |
| 剩余未验证项 | 真实钉钉送达、备份恢复演练、全仓执行器 CLI 测试 |
| 独立复核通过 | 已独立代码复核；发现的 P1 已修复，P2 已披露 |
| CI 通过 | 未执行 |
| 开发最终确认 | 未获得 |
| 人工代码评审通过 | 未执行 |
| 允许发布 | 否，仍需真实测试群验收和部署门禁 |

AI 自检结论：**有条件通过（功能开发、隔离验证及本机 Docker 升级）**，不表示真实钉钉已验收或允许生产发布。Codex 自检不等于开发自测确认、人工代码评审、CI 验证或发布审批通过。
