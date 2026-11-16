# 通知工作台 0.5.0 验证记录（2026-09-19）

## 任务闭环

执行模式：全过程开发；等级：L3（外部消息投递链路及执行器结果协议可选扩展）。基线 `main` 尚无首次提交，仓库文件均为未跟踪，无法用 Git 计算本次差异；未 commit、push。经此前对本机测试实例升级的授权，已备份并更新 `127.0.0.1:18080`。未改既有数据库表结构、权限模型或钉钉机器人发送器。

| 需求 / 工作包 | 实现 | 用例和实际状态 |
| --- | --- | --- |
| NW-01 页面职责分开 | `AlertsView.vue` 仅处理异常；`NotificationSettingsView.vue` 两个独立路由维护机器人和通知规则；`NotificationRecordsView.vue` 查看投递；任务页进入规则页 | Vue 组件、隔离 Chrome 桌面/390px 窄屏通过；按项目查询、规则修订冲突、凭据不回显、空态、权限与消息状态覆盖 |
| NW-02 脚本成功消息 | `result.schema.json` 可选 `notification_text`；`execution.complete()` 验证、遮蔽、复用既有任务配置和 `ResultNotification` 队列；新增按项目汇总规则只读 API | 显式文本优先、旧 `summary` 回退、非法字段/空白/超长拒绝、重复完成只入队一次、失败不发、同项目接收群、模拟发送与重试通过 |
| NW-03 文档与接入 | `docs/dingtalk-notification-guide.md`、协议说明、隔离 UI 测试入口、OpenAPI 导出 | 开发者无需机器人密钥；旧程序兼容，新增字段需先更新对应执行器；运行中任意次数发信/附件不在此版本 |

## 测试证据

| 环境与命令 | 退出码 | 实际结果 |
| --- | ---: | --- |
| `.venv/Scripts/python.exe -m pytest tests/backend -q` | 0 | SQLite：103 通过、7 跳过（PostgreSQL 专项），0 失败 |
| `.venv/Scripts/python.exe -m pytest tests/backend/test_notification_policies.py tests/backend/test_alert_lifecycle.py tests/backend/test_scheduling_notifications.py -q` | 0 | 20 通过、2 跳过；包含合成运行、HTTP 模拟机器人及状态恢复 |
| `.venv/Scripts/python.exe -m ruff check backend/workbench tests/backend/test_notification_policies.py tests/notification_e2e_local.py` | 0 | 通过；E2E 启动器 `compileall` 通过 |
| `npm run test -- --reporter=dot`、`npm run build`、`npm run format:check`、`prettier --check e2e/notifications.mjs` | 0 | Vue 最终 148 通过，TypeScript/Vite 构建与格式检查通过；连接启用失败反馈、连接页不混入任务规则已复验 |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE=<本机浏览器> .venv/Scripts/python.exe tests/notification_e2e_local.py` | 0 | 独立 SQLite、临时账号与本机 Chrome：四个入口、配置、规则、消息记录、390px 导航与无页面横向滚动通过；测试连接始终停用；临时库和会话文件已清理 |
| `docker build --build-arg NODE_IMAGE=public.ecr.aws/docker/library/node:22-bookworm-slim --build-arg PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim-bookworm -t automation-workbench:notification-workspace-review -f Dockerfile .` | 0 | 最终镜像 `sha256:678fbf44…` 构建成功；0.5.0 页面和新结果协议进入镜像；页面边界修复后复跑 148 个前端用例与构建并更新镜像 |
| `docker run --rm --tmpfs /tmp -e WB_DATA_DIR=/tmp/workbench -e WB_DATABASE_URL=sqlite:////tmp/notification-smoke.db --entrypoint python automation-workbench:notification-workspace-review -c "..."` | 0 | 镜像可导入应用，新规则路由出现在 OpenAPI |
| `docker compose ... stop api worker`；`.venv/Scripts/python.exe deploy/operations.py backup ...`；`docker compose ... up -d --no-build --force-recreate init-db api worker` | 0 | 升级前活动任务、五分钟内到期计划、启用连接、待发送消息均为 0；数据库和平台文件备份哈希一致且归档安全；旧镜像保留为 `automation-workbench:rollback-before-notification-workspace-20260919` |
| 升级后 `docker compose ... ps -a`、`GET /api/v1/health/ready`、`GET /`、`GET /api/openapi.json`、数据库只读计数 | 0 | API/数据库健康，worker 运行，`init-db` 退出 0；健康与页面 200，资源显示 0.5.0，新路由存在；未登录新接口 401；执行器心跳 200；30 任务、49 运行、89 告警保持不变 |

本次独立 PostgreSQL 容器专项的启动/清理命令被自动审批策略阻止，未触达当前服务数据库，也未执行该专项，不能将 SQLite 通过推断为 PostgreSQL 新规则查询已独立验证。此前通知模块在隔离 PostgreSQL 的验证属于上一版本；本次新增规则汇总查询仅在本机 Docker 升级后通过页面/API 路由与数据库健康冒烟，尚无独立 PostgreSQL 用例结果。

## 问题与风险

- P2 兼容：旧执行器严格校验 `result.json`，含 `notification_text` 的新脚本须先更新对应执行电脑；既有仅输出 `summary` 的程序无需修改。未执行现有执行器版本更新与新脚本在真实执行电脑上的端到端运行。
- P2 外部投递：网络响应丢失可能导致有限重试时重复群消息；依运行/事件编号核对。脚本文本常见凭据形式虽会遮蔽，业务敏感内容仍由脚本作者与接收群负责人确认。
- 未验证：真实钉钉 Webhook/加签与群呈现、独立 PostgreSQL 专项、备份恢复演练、人工 UI 验收、CI 和人工代码评审。当前所有连接停用，未向外部群发送消息。

备份位于 `backups/notification-workspace-upgrade-20260919-144729/`，含 `database.dump`、`platform-data.tar`、`manifest.json`；不含执行电脑本地状态和秘密。回滚先停用连接、停 API/worker，将保留的旧镜像重新标记为 `dev` 并重建 API/worker；本版未改数据库表，旧版本可忽略可选结果字段但旧执行器不接受含该字段的新脚本。需要数据恢复时须按部署手册先核对节点与运行，恢复过程本次未演练。已经发出的群消息无法撤回。

## 质量状态

| 门禁 | 状态 |
| --- | --- |
| Codex 开发完成 | 是，0.5.0 功能与本机测试实例升级完成 |
| 测试计划/用例已生成 | 是，`notification-workspace-plan.md` |
| 自动化测试已执行 | 是，见上表；PostgreSQL 专项未执行 |
| 人工验证已执行 | 否 |
| 剩余未验证项 | 真实钉钉、执行器更新后的新字段运行、独立 PostgreSQL 专项、恢复演练 |
| 独立复核通过 | 未执行本轮独立审查 |
| CI 通过 | 未执行 |
| 开发最终确认 | 未获得 |
| 人工代码评审通过 | 未执行 |
| 允许生产发布 | 否；当前仅本机测试实例升级 |

AI 自检结论：**有条件通过**。Codex 自检不等于开发自测确认、人工代码评审、CI 验证或发布审批通过。
