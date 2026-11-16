# 部署验证记录

> 2026-09-17增量：Docker引擎已恢复，本项目已完成真实镜像构建、17项浏览器链路、容器重建及新实例备份恢复。详见[本地Docker验收](docker-rehearsal-20260917.md)。下文保留2026-09-16历史结果，不再代表引擎当前不可用。

状态：v0.1 部署文件与本地安全验证已完成；容器运行门禁受 Docker Desktop 启动故障阻断。不把配置解析、安全单元测试当作 Docker 引擎或生产验证。

环境：Windows，本项目 Python 3.12 虚拟环境；Docker Compose v5.3.0。Docker 引擎连接、镜像构建、真实 PostgreSQL 与恢复演练按实际执行结果补记。

## 实际执行证据（2026-09-16）

| 命令/检查 | 退出码及结果 | 范围 |
| --- | --- | --- |
| `WORKBENCH_TEST_DATABASE_FILE` 指向专用连接文件后执行 `.venv/Scripts/python.exe -m pytest tests/deployment -q` | 0；24 通过、0 失败、0 跳过（2026-09-16） | 实际 Compose 解析；非 root、只读及秘密边界；私有数据库和启动依赖；真实临时目录秘密生成/拒绝覆盖；归档路径与恢复前校验；活动服务拒绝备份；恢复保护执行顺序及失败中止；真实 PostgreSQL 派发保护 SQL |
| `.venv/Scripts/python.exe -m ruff check deploy tests/deployment` | 0；All checks passed | 当前部署 Python 及对应测试 |
| `.venv/Scripts/python.exe -m pip check` | 0；No broken requirements found | 当前 Windows 开发虚拟环境依赖一致性，不代表 Linux wheel 已验证 |
| `C:/Program Files/Docker/Docker/resources/bin/docker-compose.exe --env-file deploy/.env.example -f deploy/compose.yaml config --quiet` | 0；无配置错误 | 无须引擎的 Compose 模式、变量及引用解析 |
| `wsl --list --verbose` | 0；docker-desktop 为 Stopped、WSL 2 | 仅诊断，不启动/重置发行版 |
| `docker --context desktop-linux version` | 命令挂起，主动中止（退出码 1）；服务端未返回 | 引擎连接未通过 |

测试中的进程边界替身仅用于验证“发现活动服务后不继续备份”“校验失败前不操作容器”等防护；不模拟真实数据库恢复来宣称通过。恢复工具新增固定 SQL 落库关闭 `system_gate.dispatch_enabled`：通过子进程边界替身验证 pg_restore → psql 关闭并检查唯一已知版本行 → tar 的顺序；数据库还原或 psql 失败均不继续恢复文件、不启动服务。

恢复保护 SQL 另在本项目专用 PostgreSQL 17.11 实例真实执行 6 个场景：开启→关闭及重复执行、空开关表、未知版本、错误 ID、多行、缺表。每个测试创建随机 schema，仅把固定 SQL 的 `public.system_gate` 限定名替换为该隔离 schema，其他 SQL 保持原样；测试结束整事务回滚，没有更改现存表。连接文件由 `WORKBENCH_TEST_DATABASE_FILE` 显式提供，不输出密码。未提供专用连接时这 6 项明确跳过，不能视为通过。真实容器中的 psql/pg_restore 命令和完整备份恢复仍未验证。

## 当前阻塞及未验证

Docker Desktop 已由主线尝试启动。今日 18:33 的 Docker 后台日志报告初始化 Inference manager 失败，无法访问/移除本地 `Docker/run/dockerInference` 监听文件，出现文件不可访问与文件名语法错误。独立 Compose 客户端可解析配置，但 Linux 引擎没有启动。没有重置 Docker Desktop、删除监听文件、修改全局 daemon、清理镜像或影响其他项目。

因此镜像构建、实际镜像层的秘密排除、容器非 root 权限、PostgreSQL 健康及并发、首次建库/管理员、容器重建持久性、代理子路径、真实备份恢复与业务外部结果核对均保持**未验证**。恢复工具仅通过安全边界测试，不宣称已完成 DEP05、DEP09 或 DEP10。可在用户确认的可用隔离 Docker 引擎上继续相关验证；当前不得据此发布。

后续环境补充：已通过 PostgreSQL 社区官方链接获取 EDB PostgreSQL 17.11 Windows 便携二进制，建立只监听回环地址的独立测试实例并验证真实认证连接。见[原生 PostgreSQL 测试环境](native-postgres-test.md)。主线可在该实例继续真实事务/并发测试；这不改变上述 Docker 验收未通过的事实，应用测试结果由对应版本记录单独报告。

备份只覆盖平台数据库与共享数据；执行器本地状态、nine 编号、秘密材料必须另行登记和验证。执行器与 API 的派发关闭逻辑由主线实现，未在本部署工作包中伪造其容器验收。

开发默认镜像系列标签用于首次验证；生产必须记录审核后的固定镜像摘要。当前未推送镜像、未连接生产服务器、未创建生产账号或发送钉钉消息。

Codex 部署文件自检不等于开发自测确认、人工代码评审、CI 验证或发布审批通过。
