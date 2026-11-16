# Windows 原生 PostgreSQL 17 隔离验证环境

日期：2026-09-16。用途仅为当前工作台真实 PostgreSQL 测试，不是生产数据库或 Docker 验收替代品。

## 来源与实际状态

系统 PATH 和常见 PostgreSQL 安装目录未发现现成服务器。通过 [PostgreSQL 官方 Windows 下载页](https://www.postgresql.org/download/windows/) 指向的 [EDB 官方二进制页](https://www.enterprisedb.com/download-postgresql-binaries) 获取 Windows x64 PostgreSQL 17.11 便携 ZIP；最终 HTTPS 地址为 EDB 官方 `get.enterprisedb.com`，没有使用第三方镜像。

归档版本：`postgresql-17.11-3-windows-x64-binaries.zip`，341325378 字节。本地 SHA256 为 `4b8db0930c38f6ef845db919551dedda3b6b845aeb0927b3d79a6e8e9e4537cf`；这是下载后计算的追溯值，**不是厂商公布或签名验证的校验值**。只解压 `pgsql/bin`、`pgsql/lib`、`pgsql/share`，没有运行安装器、注册 Windows 服务、修改 PATH 或访问其他数据库。

实际服务版本：`PostgreSQL 17.11 on x86_64-windows, compiled by msvc-19.44.35228, 64-bit`。

- 实例目录：`C:/WINDOWS/TEMP/codex-workbench-pg17-141beea5`。
- 数据目录：上述目录内的 `data`。
- 监听：仅 `127.0.0.1:8646`。端口由本机空闲端口分配，未占用标准 5432；实际 `Get-NetTCPConnection` 验证只绑定回环地址。
- 专用数据库：`workbench_test`；专用角色 `wb_test`，用于隔离测试。
- 认证：随机密码，初始化本地与主机连接均使用 `scram-sha-256`。
- 当前已通过真实密码认证连接，查询版本、数据库及监听地址，退出码 0。
- 实例暂保持运行供主线测试；启动不代表应用并发测试已通过。

连接密码只保存在受 ACL 限制的 TEMP 文件。`runtime/postgres17/instance.local.json` 是不含密码的实例指针，指向真实 `connection.local.json`；连接文件的 `database_url` 可供 SQLAlchemy/psycopg 测试读取，禁止打印或提交。TEMP 目录取消继承权限，仅当前账号与 SYSTEM 可访问。`runtime/` 已在 Git/Docker 排除范围内。

## 启动与停止

首次创建通过以下辅助脚本完成；再次执行发现连接配置后会拒绝覆盖，不会重置现有数据库：

```powershell
.\.venv\Scripts\python.exe deploy/temporary_postgres.py
```

正常启停使用已有实例指针，不重新初始化：

```powershell
$pgTestInstance = Get-Content -LiteralPath runtime/postgres17/instance.local.json -Raw | ConvertFrom-Json
& $pgTestInstance.pg_ctl -D $pgTestInstance.data_directory status
# 仅确认本专用实例已停止时启动；日志写在实例目录。
$pgTestLog = Join-Path (Split-Path $pgTestInstance.data_directory) 'server.log'
Start-Process -FilePath $pgTestInstance.pg_ctl -ArgumentList @('-D', $pgTestInstance.data_directory, '-l', $pgTestLog, '-w', 'start') -WindowStyle Hidden -Wait
# 全部测试结束且主线确认不再使用后停止；不删除数据目录。
& $pgTestInstance.pg_ctl -D $pgTestInstance.data_directory -m fast -w stop
```

不要把此命令改成全局进程终止，不要停止不属于该目录的 PostgreSQL。测试连接从配置文件读取；不把 DSN 填入命令参数、日志或报告。创建额外隔离库或清理测试表必须明确针对该测试实例。

## 已发现并处理的本机限制

首次将 EDB 二进制和数据放在中文工作区路径时，`initdb` 在 post-bootstrap 阶段因系统代码页与 UTF-8 混用报非法字节，退出码 1，并自行清除了失败的数据目录。调整为专用 ASCII TEMP 路径后初始化通过；没有修改系统 locale 或项目路径。

Windows 下 `pg_ctl start` 使用 Python 输出管道时，后台进程继承句柄会令调用等待，即使服务器已就绪。辅助脚本已改为隐藏窗口并将启动进程输出接到空设备，服务器日志单独落盘；明确的 `--resume-initialized` 仅用于本工具已初始化但记录未完成的目录，不重置密码或覆盖运行中的二进制。

Docker Desktop 的 Inference manager 故障仍未修复。此原生实例可以提供 PostgreSQL 事务、并发和 SQL 兼容验证，但不能代替镜像构建、Linux 文件权限、容器网络/健康、Compose 恢复及生产演练。
