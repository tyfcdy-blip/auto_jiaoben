# 2026-09-17 Docker本地隔离验收补充

用户确认Docker已启动后，实测Docker Desktop 4.82.0、Engine29.6.1、linux/amd64可用。本记录更新昨日“引擎不可用”状态，不覆盖昨日测试事实，也不代表完整系统或生产发布验收。

## 范围与结果

模式：修复与复验，沿用系统L3门禁。只使用本项目新建的 `automation-workbench-dev` 与 `automation-workbench-restore-test-20260917` 两个隔离Compose项目。没有修改其他项目、全局Docker配置、原nine、生产账号或外部业务系统；无commit/push。

| 验证 | 实际结果 |
| --- | --- |
| 引擎连接 | 通过；Linux/amd64服务端返回29.6.1 |
| 多阶段镜像构建 | 通过，退出0；Node22、Python3.12、锁定前后端依赖在容器内构建 |
| 数据库/初始化/管理员 | 通过；PostgreSQL17.11，独立新卷；一次性管理员初始化成功，无默认密码 |
| API/存储/后台 | 通过；API与数据库healthy；单独监控令牌探针确认scheduler/reconciler/notifier水位正常 |
| 运行身份 | API实际UID10001，存储可写；根文件系统只读配置生效，镜像/app/secrets不存在 |
| 浏览器到容器真实链路 | 17项通过，退出0；浏览器错误/警告/API错误均0，含Windows执行器真实子进程、日志、产物下载与失败告警关闭 |
| 应用容器重建 | 通过；选定业务表记录摘要、3份对象内容摘要、1份关闭的合成通知配置解密保持一致 |
| 实际备份 | operations.py生成真实pg_dump、平台tar、摘要manifest，退出0；备份前无在途运行、无启用计划，API/worker已停止 |
| 新实例恢复 | 真实pg_restore与文件恢复成功，退出0；落库dispatch_enabled=false，业务表/文件/解密一致；恢复实例未启动API/worker |
| 收尾复验 | 主开发容器重新启动，健康及持久状态再次一致；恢复测试DB已停止，卷和备份保留 |

浏览器结果位于 `frontend/e2e-results/summary.json`（本轮17项），合成快照位于 `runtime/docker/`，备份位于 `backups/docker-rehearsal-20260917-01/`；均由Git忽略。静态探针为 `tests/deployment/docker_state_probe.py`，仅输出摘要不输出业务内容或凭据，Ruff与compileall退出0。

## 镜像来源与构建

首次默认Docker Hub构建退出1：auth.docker.io连接超时。随后仅本轮进程通过既有镜像参数使用 `public.ecr.aws/docker/library/` 地址，同版本构建成功。未修改daemon、DNS、镜像全局配置或依赖版本。

```powershell
$env:WB_NODE_IMAGE = 'public.ecr.aws/docker/library/node:22-bookworm-slim'
$env:WB_PYTHON_IMAGE = 'public.ecr.aws/docker/library/python:3.12-slim-bookworm'
$env:WB_POSTGRES_IMAGE = 'public.ecr.aws/docker/library/postgres:17-bookworm'
docker compose --env-file deploy/.env.example -f deploy/compose.yaml build api
docker compose --env-file deploy/.env.example -f deploy/compose.yaml up -d --no-build db init-db api worker
docker compose --env-file deploy/.env.example -f deploy/compose.yaml run --rm --no-deps init-admin
```

本地开发秘密通过 `python deploy/bootstrap.py` 新建，不输出内容，不覆盖已有文件；Windows目录ACL已限制。未把秘密写入镜像构建参数或命令行。

最终本地镜像ID为 `sha256:030fddcf477c5a54cc2ee05cf5fe4ad9e09cb2122efe0c7b1591001b756d2981`，未推送仓库。运行配置摘要为 `sha256:0a4f43cb92a150630e7c24c7e89146f41fa3ea8b7575f56615eaa62200b265ae`。基础Node摘要 `sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5`，Python摘要 `sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254`，Postgres拉取摘要 `sha256:051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0`。

本轮增加 `.dockerignore` 对frontend/e2e-results的显式排除，避免浏览器截图和测试运行输出进入构建上下文。重新构建后运行镜像配置和业务层保持一致，重新启动并再次验证状态；不声称对全部构建缓存层做了秘密扫描。

## 重建、备份与恢复步骤

新探针读取程序、版本、任务、批次、输入文件、运行、事件、日志、产物、通知配置，比较按ID排序的完整记录SHA256；另外校验平台对象文件名与内容摘要，逐一解密关闭的合成通知配置。它不比较登录会话、审计追加行、心跳水位，也不证明任意生产数据恢复。

```powershell
docker compose --env-file deploy/.env.example -f deploy/compose.yaml up -d --no-build --force-recreate api worker
docker compose --env-file deploy/.env.example -f deploy/compose.yaml stop api worker
python deploy/operations.py backup --project automation-workbench-dev --env-file deploy/.env.example --directory backups/docker-rehearsal-20260917-01 --note 'Isolated synthetic test: no active runs; schedules disabled; executors finished'
docker compose --project-name automation-workbench-restore-test-20260917 --env-file deploy/.env.example -f deploy/compose.yaml up -d --wait db
python deploy/operations.py restore --project automation-workbench-restore-test-20260917 --env-file deploy/.env.example --directory backups/docker-rehearsal-20260917-01 --confirm-project automation-workbench-restore-test-20260917
```

以上为本次真实执行过的专用测试项目。复跑必须选新的隔离恢复项目/空卷/备份目录，不能把示例直接指向存量生产环境。恢复后的比对只通过 `run --rm --no-deps -T api python -` 执行只读探针，没有启动长期API或worker。之后停止恢复DB，保留卷；没有删除任何卷。

测试辅助读取脚本首次遇到Windows默认GBK编码错误，改为显式UTF-8后探针通过；未改业务实现或降低断言。此前真实浏览器与持久性验证全部复验通过。

独立只读复核已核验4份摘要与浏览器结果，未发现P1。摘要只覆盖所选10张表、3个对象和1条通道；不覆盖users/projects/members/nodes/callers/schedules/alerts/deliveries/audit/locks等全部持久内容。`final-state.local.json`是重新启动的开发实例，派发true；`after-restore.local.json`才是恢复实例，派发false，恢复实例随后停止。快照没有目标标识或时间戳，实例归属与启停依据本轮对应Compose执行记录，不单靠摘要推断。

## 仍未关闭的门禁

本次是**同一Docker主机上的新实例恢复**，不是异机灾备。没有验证真实外部写入、nine编号与请求状态恢复、正式OS身份、Linux业务执行器、完整版本迁移/回退、生产代理/TLS/子路径容器组合、RPO/RTO或容量指标。正式凭据注入等剩余开发项仍见 [当前工作包状态](../current-status.md)。

当前预览6465账号与Docker测试18080账号不同，分别保存于各自受忽略的本地会话文件。没有更改6465的管理员密码。真实钉钉未发送，唯一新通知配置为关闭的合成恢复样例。

质量状态：本轮Docker本地验收切片有条件通过；完整系统开发未完成，人工验收/代码评审、CI、开发最终确认和生产发布批准均未执行，仍不允许生产发布。Codex自检不等于上述确认。
