# 复用服务器 PostgreSQL 17 验证报告

日期：2026-11-16。模式：全过程开发。等级：L3；改动涉及共享数据库、凭据、Docker网络、初始化、备份恢复边界及共享Nginx。测试仅在本机独立命名的合成容器、网络、临时卷和账号执行；没有连接服务器、真实数据库、共享Nginx或真实业务文件。

## 需求闭环

| 需求/工作包 | 实现 | 用例 | 结果 |
| --- | --- | --- | --- |
| 自带数据库可选 | `db`进入`bundled-db` profile；开发env显式启用 | EP01/EP02 | 通过 |
| 连接已有PG17 | external-db覆盖把init/API/Worker接入管理员指定网络；不写容器IP | EP02/EP08 | 通过 |
| 安全初始化 | 仅暂时性连接错误有限重试；错误密码、权限/结构错误立即失败 | EP03/EP04 | 通过 |
| 凭据注入 | bootstrap从单行密码文件生成URL，编码特殊字符，秘密文件路径可由环境指定 | EP05/EP06 | 通过 |
| 共享实例资源 | API/Worker连接池大小、溢出和等待时间有界 | 连接池单元/EP08 | 通过 |
| 共享Nginx | 仅API加入入口网络并使用`automation-workbench-api`别名 | Compose/EP08 | 通过 |
| 备份恢复边界 | 内置工具外部模式在IO前拒绝；部署单要求管理员项目级dump+平台卷同恢复点 | EP07 | 通过 |
| 服务器实际约束 | 部署单记录已有容器/卷、`basic_basic_net`、离线镜像、容量历史和原服务回归 | 文档审查 | 已纳入；现场未验证 |

## 实际测试证据

| 层级 | 命令/环境 | 结果 |
| --- | --- | --- |
| 专项单元与Compose | `.venv/Scripts/python.exe -m pytest tests/backend/test_cli_database.py tests/deployment/test_deployment_safety.py -q` | 退出0；34通过、0失败、0跳过、2条第三方弃用警告 |
| Python全回归 | `.venv/Scripts/python.exe -m pytest tests -q --junitxml=runtime/verification/external-postgres-python.xml` | 退出0；354通过、0失败、36跳过、2警告，98.09秒 |
| 前端回归 | `npm run test -- --run` | 退出0；17文件、154用例通过 |
| 前端格式/构建 | `npm run format:check`；`npm run build` | 均退出0；100模块构建成功 |
| Python静态/编译 | `.venv/Scripts/python.exe -m ruff check backend agent integrations deploy tests`；`compileall` | 均退出0；Ruff All checks passed |
| 两种Compose | 开发env基础Compose；生产env叠加external-db和shared-nginx | 均退出0；外部模式活动服务无db |
| 隔离测试镜像 | `automation-workbench:external-pg-test` | 构建退出0；仅用于本机外部PG验证 |
| 发布候选镜像 | `automation-workbench:0.6.0-rc.2` | 构建退出0；OCI version/revision与tag提交一致；镜像ID`sha256:878b2a…2868` |
| 离线包 | `runtime/releases/v0.6.0-rc.2/automation-workbench_0.6.0-rc.2.tar` | 72,007,680字节；SHA-256 `3aadfb85…5ba3`；docker load后镜像ID不变 |
| Docker外部库冒烟 | PostgreSQL 17独立管理员、低权限应用角色、外部DB网络、入口网络、生产模式 | init-db退出0；API健康、Worker运行；ready/page均200；公共schema 29表；日志无异常 |
| 权限反向验证 | 应用角色连接自己的库；再连接管理库 | 自有库退出0；管理库退出2（拒绝）；角色非superuser/createdb/createrole，连接上限25 |
| 清理 | 按明确项目标签和测试前缀删除容器、网络、卷及合成秘密 | 已完成；未触碰其他Docker项目 |

36项跳过仍是需要显式专用PostgreSQL连接、真实Agent会话或授权nine源码的既有环境专项，不能计为通过。本轮核心外部数据库拓扑已由独立PostgreSQL 17容器真实验证，但没有替代这些既有专项。

## 问题与风险

- P0/P1：未发现未关闭项。
- P2：服务器数据库实际网络名和可解析主机名仍未知；上线前必须现场只读核对。
- P2：2026-11-13资料显示`/data`曾使用88%、仅余约24GB；上线前必须重测并完成容量、备份和迁移临时空间预算。
- P2：真实共享Nginx配置检查、平滑重载、新路径和原`/data-service/health/ready`回归未执行。
- P2：真实管理员尚未创建工作台库/用户，也未验证工作台账号无法连接现有生产库；本地合成权限验证不能替代服务器验收。
- P2：共享数据库的项目级备份、平台卷同恢复点和隔离恢复演练未执行；当前内置操作工具明确不支持外部库。
- P2：发布镜像的Docker Scout漏洞扫描因Docker账号未登录而退出1，尚未完成；不得据此批准发布。
- P3：测试依赖有2条既有弃用警告，未影响本次结果。

## 质量状态

| 状态 | 结果 |
| --- | --- |
| Codex开发完成 | 是，本地外部PostgreSQL部署能力完成 |
| 测试计划/用例已生成 | 是，EP01–EP09 |
| 自动化测试已执行 | 是，见证据表 |
| 人工验证已执行 | 否 |
| 剩余未验证项 | 服务器网络/权限/容量/Nginx/备份恢复、真实域名TLS、36项既有环境专项 |
| 独立复核通过 | 未执行；不能推定通过 |
| CI通过 | 未执行 |
| 开发最终确认 | 未确认 |
| 人工代码评审通过 | 未执行 |
| 允许发布 | 否；固定提交、tag和离线镜像已形成，仍需完成服务器部署前门禁 |

AI自检结论：**有条件通过**。本地核心路径无P0/P1，测试证据完整；真实服务器共同故障域、权限和恢复门禁尚未完成。Codex自检通过不等于开发自测确认、人工代码评审、CI验证或发布审批通过。
