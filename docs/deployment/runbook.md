# Docker 部署与恢复

本文件是可执行交付说明。当前真实通过项以 `verification.md` 为准；未执行的容器、节点和生产步骤不能视为已经验收。平台容器不执行用户上传的业务程序，Windows 执行器另行安装。

正式服务器部署必须先完整填写并签署[生产部署单](production-deployment-order.md)，不能把下面的本地示例命令直接当作生产变更单。

## 本地隔离部署

前提：Python 3.12、可用的 Docker Linux 引擎和 Compose v2 以上、允许获取依赖的网络。默认仅监听本机 18080，项目名 `automation-workbench-dev`。运行命令前位于仓库根目录。程序代码与配置中没有默认管理员密码。

```powershell
python deploy/bootstrap.py
docker compose --env-file deploy/.env.example -f deploy/compose.yaml config --quiet
docker compose --env-file deploy/.env.example -f deploy/compose.yaml build
docker compose --env-file deploy/.env.example -f deploy/compose.yaml up -d db init-db api worker
docker compose --env-file deploy/.env.example -f deploy/compose.yaml run --rm init-admin
docker compose --env-file deploy/.env.example -f deploy/compose.yaml ps
```

首次密码在 `secrets/admin_password`，由获授权管理员在本机安全读取，不粘贴到日志、命令参数或群消息。`init-admin` 不在常规启动链路内，不自动重置既有管理员。秘密目录已被 Git 和 Docker 构建上下文排除；初始化脚本拒绝覆盖任何既有秘密。部分生成失败时保留文件供核对，不自动清理。

Linux 秘密目录权限为 0700，文件为 0444；目录阻止其他宿主账号遍历，文件只以只读绑定形式挂入获授权的服务，允许非 root 平台进程读取。Windows 使用当前账号及 SYSTEM 的目录 ACL。Compose 文件型 secrets 不提供加密保管；生产应由运维保护源文件、宿主账号和备份。

`api` 与 `worker` 使用同一镜像、不同命令，UID/GID 为 10001。数据库无宿主端口。`init-db` 对数据库连接做有限次数等待，成功后才启动应用；结构不兼容、权限不足等非连接错误立即失败。worker 没有伪造的 HTTP 容器健康探针，应通过平台组件水位和独立监控检查其实际进展。应用就绪探针随 `WB_BASE_PATH` 请求完整路径。

停止本项目而保留数据：

```powershell
docker compose --env-file deploy/.env.example -f deploy/compose.yaml stop api worker
```

不要在共享项目执行整栈 `down`，不要使用 `down -v` 或删除卷。数据库及平台文件分别位于该 Compose 项目的 `database_data` 和 `platform_data` 卷中。卷数据必须另行备份，容器自身可重建不等于数据可恢复。

## 生产配置与镜像

开发镜像的 Node 22、Python 3.12、PostgreSQL 17 默认使用系列标签，便于首次验证；**不能把系列标签作为不可变生产发布依据**。发布负责人先解析、审核基础镜像摘要，记录漏洞及许可证评估，再通过 `WB_NODE_IMAGE`、`WB_PYTHON_IMAGE` 和 `WB_POSTGRES_IMAGE` 指定 `仓库@sha256:...`。Python 与前端依赖使用锁文件。新机器须验证同一 OS/架构；不能凭本地 Windows 测试宣布 Linux 已兼容。

正式实例使用独立目录、秘密和明确项目名；配置 `WB_ENV=production`，设置已分配的 `WB_BASE_PATH`、端口、资源预算和审核后的 `WB_IMAGE`。后端正式环境拒绝 SQLite，必须使用 PostgreSQL。Fernet 加密密钥和数据库备份分别受控保存，丢失密钥会导致已加密凭据不可恢复。

### 连接服务器已有 PostgreSQL 17

自带数据库仅在 `bundled-db` profile 启用。复用已有实例时不设置该 profile，并同时加载 `deploy/compose.external-db.yaml`。部署方必须先提供：数据库网络名、网络内可解析的数据库主机名、独立数据库名、独立用户名，以及只授予该项目的密码文件。不得使用容器IP；本项目当前不依赖额外 PostgreSQL 扩展。

先由服务器管理员创建独立用户和由该用户拥有的独立数据库，并在数据库侧完成连接、schema创建和表创建权限核对。还必须反向验证该账号连接现有数据服务生产库会被拒绝；PostgreSQL数据库的默认 `CONNECT` 权限不能只凭“新建了用户”推定已经隔离。账号连接上限要覆盖API和Worker连接池及初始化余量；默认每个应用进程 `pool_size=5/max_overflow=5`，两个常驻进程合计最多20条应用连接。再生成本项目秘密；密码只从文件读取，不放入命令参数或env：

```bash
python deploy/bootstrap.py \
  --directory /srv/automation-workbench/secrets \
  --external-database \
  --database-host <网络内主机名> \
  --database-port 5432 \
  --database-name <独立数据库名> \
  --database-user <独立用户名> \
  --database-password-file <管理员交付的密码文件>
docker compose --env-file deploy/.env.production \
  -f deploy/compose.yaml -f deploy/compose.external-db.yaml config --quiet
```

配置结果中活动服务不得包含 `db`，`init-db/api/worker` 必须同时加入本项目网络和管理员指定的数据库网络。启动时使用同一组Compose文件：

```bash
docker compose --env-file deploy/.env.production \
  -f deploy/compose.yaml -f deploy/compose.external-db.yaml \
  up -d --no-build api worker
```

`init-db` 会作为依赖先运行，只创建/核对本项目数据库中的表；检测到同名但缺字段的旧结构会拒绝覆盖。共享实例的重启、磁盘、内存、连接数和故障会同时影响现有数据服务与工作台，必须由服务器管理员统一设置资源水位和变更窗口。

构建和推送分开；构建命令不会自动发布。以下使用占位环境变量，必须由具体发布单补齐后执行：

```powershell
docker compose --env-file deploy/.env.production -f deploy/compose.yaml build
docker image inspect $env:WB_IMAGE --format '{{.Id}}'
# 获得本次仓库推送授权后才执行：
docker push $env:WB_IMAGE
```

服务器只拉取经批准的镜像，Compose 的 `build` 配置不等于允许服务器临时重新构建；部署使用 `up --no-build`。获取并保存仓库 RepoDigest，生产 `WB_IMAGE` 应使用该摘要。当前没有生产镜像推送或部署授权。

## 代理子路径与共享 Compose

`deploy/nginx.conf.example` 是现有 HTTPS server 的追加片段，不能覆盖现有站点配置。`WB_BASE_PATH=/automation` 时代理保留 `/automation`，不剥离前缀。前端资源、API、登录和下载均需通过该前缀验收。默认 API 不信任任意代理头；若部署层确实依赖它们，只信任明确的代理地址，不能全局信任所有来源。

前端 `VITE_BASE_PATH` 是构建参数，Compose 从同一 `WB_BASE_PATH` 派生并补尾斜线。修改部署前缀必须重新构建相应前端镜像，不能只改后端运行环境；每个发布清单记录其编译前缀。

推荐使用独立 Compose 项目。复用数据库只共享管理员指定的外部Docker网络，不把工作台服务并入数据服务的Compose项目。若必须追加到共享 Compose，先把 `db/init-db/api/worker/init-admin`、命名卷、网络及 secrets 改成唯一的 `workbench_` 前缀，同步数据库 URL 的主机名、服务依赖、配置路径和操作脚本。不得直接把根级 `name:` 和 `services:` 文本覆盖共享文件。

两个 Compose 项目的同名默认网络不会自动互通。代理运行在宿主机时可使用本机绑定端口；代理在容器中时由运维配置共享受控网络并使用服务别名，不能使用容器内 `127.0.0.1` 冒充宿主。检查合并配置后仅更新本项目服务，验证其他服务未中断。

当前服务器资料说明共享Nginx位于Docker网络 `basic_basic_net`，现有数据服务路径为 `/data-service/`。工作台应申请独立路径并把API加入入口网络；数据库使用另一条由部署方现场核对的网络。修改共享Nginx前必须先做配置检查，平滑重载后同时验证新路径和原 `/data-service/health/ready`，不能以工作台可访问替代原服务回归。

## 一致性备份

先将 `WB_DISPATCH_ENABLED=false` 部署到 API 和 worker，等待/核对在途运行，记录执行器本地持久状态及外部写入，再停止 API 与 worker。运维必须确认没有旧节点继续写平台文件；仅停止平台不等于外部写入已停止。备份命令要求服务已停：

```powershell
python deploy/operations.py backup --project automation-workbench-dev --env-file deploy/.env.example --directory backups/rehearsal-001 --note "隔离演练：入口停止，无在途运行；节点状态已核对"
```

产物包括 PostgreSQL custom dump、平台数据 tar 和 SHA256 manifest；备份目录必须是新目录。失败不会写成功 manifest，也不会自动删除现场。备份**不包括**执行器电脑的状态文件、nine 编号/请求响应记录、秘密文件或镜像本身；这些需按同一恢复点单独备份和登记。将一份副本保存在独立故障域并验证能解密，不能只有同盘副本。

上述 `deploy/operations.py` 只支持 `bundled-db`。外部共享数据库模式传入 `--database-mode external` 会在任何容器和文件操作前拒绝执行。该模式必须由服务器管理员按项目单独执行数据库 custom-format dump，并在同一停派发窗口备份工作台 `platform_data`；两者记录同一个恢复点、摘要和保留策略。不得对共享实例整卷回滚，也不得重启或恢复现有数据服务来替代本项目级恢复。

## 恢复到隔离空实例

恢复具有破坏性，仅在明确授权的目标实例执行。先准备匹配镜像和秘密（尤其原加密密钥），创建独立项目及空平台数据卷，启动数据库，不启动 API/worker；不先运行 init-db。脚本核验备份摘要和 tar 路径，拒绝符号链接、设备或越界条目，要求显式重复目标项目名：

```powershell
docker compose --project-name automation-workbench-restore-test --env-file deploy/.env.example -f deploy/compose.yaml up -d db
python deploy/operations.py restore --project automation-workbench-restore-test --env-file deploy/.env.example --directory backups/rehearsal-001 --confirm-project automation-workbench-restore-test
```

数据库恢复成功后，工具立即通过目标数据库中的固定 SQL 将 `public.system_gate.dispatch_enabled=false`，要求恰好一个 `id=1/schema_version=1` 的开关行；随后才恢复平台文件。缺失表/字段、未知版本、零行或多行及 SQL 失败均中止，不恢复文件、不启动服务。旧备份中的派发开启状态因此不会被当成恢复后的正常状态；该检查失败时数据库可能已恢复，必须保持停机处理，不能自行启动核对界面。

恢复后保持 API/worker 停止。核对原实例及执行器是否仍存活、恢复点后是否受理或完成业务；禁止原实例和恢复实例同时领取任务。以 `WB_DISPATCH_ENABLED=false` 启动可查询的核对界面，检查去重、游标、锁、密钥解密及外部结果。数据库开关关闭是落库保护，环境开关关闭是核对阶段的另一层限制。管理员凭据和节点身份亦属于恢复数据，不能不核对就复制两套正式节点。

只有确认实际业务结果、编号状态和输入一致并取得恢复授权后，才开启派发。恢复脚本不会自动清卷、重置编号、启动 worker 或宣称业务回滚完成。解密材料丢失、tar 解包或数据库恢复失败时保持停机，使用明确的修复方案。

## 升级与回退

版本发布前记录应用/执行器协议/数据库结构兼容矩阵。先停派发、核对在途、备份，再执行经过测试的迁移与镜像更新；初次管理员创建不重复运行。迁移失败不得继续启动可写服务。确认就绪、子路径和无副作用冒烟后恢复批准的计划及事件入口。

旧镜像不能保证兼容新数据库；不可逆迁移不能用“换镜像”回退。业务程序版本回退也不能撤销已上传数据。当前 `init-db` 是初始库准备入口，是否已有正式增量迁移须以该版本实现及发布证据为准，不把建表命令当作任意版本升级方案。
