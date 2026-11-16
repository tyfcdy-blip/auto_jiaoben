# 自动化工作台生产部署单

状态：已按服务器现场信息预填，容量、账号、备份和审批仍阻断上线。目标候选：v0.6.0-rc.3，2026-11-16。`v0.6.0-rc.2`前端按根路径构建，不得用于`/automation/`。

本部署单用于把已验证发布候选部署到明确服务器。任何占位项、失败检查或未关闭P0/P1都会阻断继续执行。首次上线范围只允许 `side_effects=false` 的无外部写入程序；当前版本在配置层明确拒绝开启正式副作用执行。

## 1. 发布事实

| 项目 | 实际值 | 核对人/证据 |
| --- | --- | --- |
| Git tag | `v0.6.0-rc.3`待建立；`v0.6.0-rc.2`保留但禁止部署 | rc.2静态资源为`/assets/*`，与现场子路径不兼容 |
| 完整 commit SHA | rc.3建立后填写 | tag必须解引用一致 |
| 应用镜像 | `linux/amd64`离线包待重新构建 | 必须核对OCI revision、`/automation/assets/*`和SHA-256 |
| 基础镜像摘要 | Node/Python/PostgreSQL均待填写 | 漏洞和许可证记录待填写 |
| 部署日期与窗口 | 待填写 | 待填写 |
| 发布负责人/回退负责人 | 待填写 | 待填写 |
| 变更单/审批记录 | 待填写 | 待填写 |

## 2. 服务器与容量事实

| 项目 | 实际值 |
| --- | --- |
| 服务器用途及资产编号 | 慕雅居多项目Docker服务器；资产编号待填写 |
| OS、版本、CPU架构 | CentOS Linux 7 (Core)，`x86_64`；目标镜像`linux/amd64` |
| CPU、内存 | 8逻辑CPU、15G内存 |
| Docker Engine / Compose版本 | 26.1.4 / v2.27.1 |
| 现有数据服务部署目录 | `/mnt/docker/data-reservoir`，仅作边界参考；工作台必须使用另一个目录 |
| 工作台部署绝对目录 | `/mnt/docker/automation-workbench` |
| 镜像交付 | 离线固定版本镜像包及SHA-256 |
| 共享入口 | Docker内共享Nginx；入口网络 `basic_basic_net` |
| 最终URL | `https://ai.muyaju.net/automation/` |
| TLS证书来源、有效期、续期责任人 | 待填写 |
| PostgreSQL实例 | 17.11；容器 `muyaju-data-reservoir-db-1`，容器端口5432，无宿主机映射 |
| PostgreSQL持久卷 | `muyaju-data-reservoir-pg17`；工作台不得直接操作或整卷恢复 |
| 工作台数据库/用户 | 拟定`automation_workbench` / `automation_workbench_user`；尚未创建 |
| 数据库Docker网络名/主机名 | 网络`muyaju-data-reservoir_database`已确认；候选主机名`muyaju-data-reservoir-db-1`待容器内验证 |
| PostgreSQL和平台文件备份目标 | 数据库建议`/data/automation-workbench-backups`；平台文件目标待定 |
| 日备份保留天数、异地副本 | 待填写 |
| `/data`容量现场值 | 总197GB、已用173GB、可用15GB、使用率93%；当前为上线阻断项 |
| 预计用户/电脑/任务/日运行量 | 待填写 |
| 单文件、单批次、项目容量配额 | 待填写并与磁盘预算核对 |

最低资源建议不能替代容量测算。当前生产模板原默认单项目输入与程序包配额合计可达15GB，已经等于`/data`全部可用空间，且尚未计数据库、结果、日志、备份和升级临时空间，因此模板已改为强制填写容量值，未批准前应用不能正常启动。还须核对Docker Root Dir和`platform_data`卷实际落盘文件系统。CentOS 7已停止常规维护，容器隔离不能替代宿主内核安全；上线前需要服务器升级计划或明确风险接受。

## 3. 网络矩阵

| 来源 | 目标 | 端口 | 用途 | 验证结果 |
| --- | --- | --- | --- | --- |
| 管理员浏览器 | `ai.muyaju.net/automation/` | TCP 443 | 页面、API和文件下载 | 待部署验证 |
| 执行电脑Agent | 工作台域名 | TCP 443 | 心跳、领取、日志和产物 | 待验证 |
| 影刀/外部调用方 | 工作台域名 | TCP 443 | 事件触发和文件上传 | 待验证 |
| 工作台服务器 | `oapi.dingtalk.com` | TCP 443 | 钉钉消息 | 待验证 |
| 执行电脑 | UNC文件服务器 | TCP 445或实际端口 | 共享目录输入，可选 | 待验证 |
| 服务器本地运维 | `127.0.0.1:18080` | TCP 18080 | 仅本机诊断入口 | 现场未占用，待部署验证 |
| 工作台容器 | `muyaju-data-reservoir-db-1`候选别名 | TCP 5432（Docker内网） | 仅访问工作台独立数据库 | 网络已确认，解析/权限待验证 |
| 共享Nginx容器 | `automation-workbench-api:8000` | TCP 8000（`basic_basic_net`） | `/automation/`转发 | 网络已确认，配置待审批 |

PostgreSQL继续不映射宿主端口，不对公网或办公网开放。工作台的 `init-db/api/worker` 只加入管理员指定的数据库网络，使用稳定主机名连接；执行电脑由Agent主动连接工作台，不开放执行电脑入站管理端口。

## 4. 部署前门禁

- [ ] Git tag、commit、镜像RepoDigest和镜像OCI revision完全一致；
- [ ] 镜像为`linux/amd64`，首页全部本地资源使用`/automation/assets/`，不得出现根路径`/assets/`；
- [ ] 当前提交的后端、前端、构建、静态检查和Compose验证全部通过；
- [ ] 实际生产 `.env.production` 不在Git中且没有占位值；
- [ ] 独立生产秘密目录已生成、权限已核对，秘密未出现在终端记录或群消息；
- [ ] 管理员已创建工作台独立数据库和独立用户，权限限制在自己的库；主机名和网络名已实测；
- [ ] 已验证工作台账号能连接自己的库、不能连接现有数据服务生产库；PostgreSQL默认`CONNECT`权限已按需收紧；
- [ ] 工作台账号连接上限已设置并覆盖API/Worker连接池及初始化余量；默认两个进程合计最多20条应用连接；
- [ ] 数据库密码、Fernet密钥、监控Token和管理员初始密码有独立受控备份；
- [ ] 域名、TLS和反向代理配置经过配置审查；
- [ ] 已处理`/data`使用率93%的容量阻断，并核对Docker Root Dir、平台卷落盘位置、增长配额、日志、备份和迁移临时空间；
- [ ] CentOS 7宿主风险已有升级计划或经授权的限期风险接受记录；
- [ ] 管理员已给出工作台数据库的项目级备份/恢复命令、位置、保留周期、恢复负责人和恢复演练日期；
- [ ] 首台试点执行电脑使用专用低权限Windows账号，Python/Node及目录权限已准备；
- [ ] 首批脚本均无生产秘密、无外部写入，并通过程序包扫描和脱敏数据测试；
- [ ] 真实钉钉测试群、机器人安全设置和接收责任人已确定；
- [ ] 回退触发条件和停止派发责任人已确定；
- [ ] 没有未关闭P0/P1。

## 5. 首次部署步骤

所有命令在服务器受控部署目录执行；实际文件名、项目名和镜像摘要以本部署单为准。

1. 拉取并核对源码tag或仅取得经过批准的部署文件；服务器不临时改代码。
2. 复制 `deploy/.env.production.example` 为不纳入Git的实际环境文件，填完全部占位项。
3. 由服务器管理员创建工作台独立数据库和用户；应用账号不得访问现有数据服务数据库。管理员把密码放入仅部署人员可读的临时文件，不把密码放到命令参数、env或群聊。
4. 管理员把数据库密码临时写入`/mnt/docker/automation-workbench/bootstrap-input/db_password`并限制权限。生成工作台秘密后安全删除该临时输入；发现输出目录已有凭据立即停止，不能覆盖：

   ```bash
   python deploy/bootstrap.py \
     --directory /mnt/docker/automation-workbench/secrets \
     --external-database \
     --database-host muyaju-data-reservoir-db-1 \
     --database-port 5432 \
     --database-name automation_workbench \
     --database-user automation_workbench_user \
     --database-password-file /mnt/docker/automation-workbench/bootstrap-input/db_password
   ```

5. 以 `WB_DISPATCH_ENABLED=false` 解析外部数据库配置：

   ```bash
   docker compose --env-file deploy/.env.production \
     -f deploy/compose.yaml -f deploy/compose.external-db.yaml \
     -f deploy/compose.shared-nginx.yaml config --quiet
   ```

   配置输出中的活动服务不得包含 `db`；`init-db/api/worker` 必须加入管理员提供的数据库网络。

6. 按服务器现有发布方式上传并核对离线镜像包，或拉取固定RepoDigest；两种方式都必须检查镜像标签和摘要：

   ```bash
   docker pull <完整RepoDigest>
   docker image inspect <完整RepoDigest> --format '{{json .Config.Labels}}'
   ```

   若离线交付，先核对交付清单SHA-256，再 `docker load`；不得覆盖或清理其他项目镜像。

7. 不启动第二套数据库，先单独运行一次 `init-db`：

   ```bash
   docker compose --env-file deploy/.env.production \
     -f deploy/compose.yaml -f deploy/compose.external-db.yaml \
     -f deploy/compose.shared-nginx.yaml \
     run --rm --no-deps init-db
   ```

   若出现权限不足、已有表结构不兼容或连接重试耗尽，立即停止；不得自动扩大权限、删表或覆盖。

8. 启动API和Worker，保持派发关闭：

   ```bash
   docker compose --env-file deploy/.env.production \
     -f deploy/compose.yaml -f deploy/compose.external-db.yaml \
     -f deploy/compose.shared-nginx.yaml \
     up -d --no-build api worker
   docker compose --env-file deploy/.env.production \
     -f deploy/compose.yaml -f deploy/compose.external-db.yaml \
     -f deploy/compose.shared-nginx.yaml ps
   ```

9. 首次且仅首次以同一组Compose文件运行 `init-admin`。管理员从受控秘密文件取得初始密码，首次登录后按组织规则保管；不要粘贴到部署记录。
10. `compose.shared-nginx.yaml`只把API加入 `basic_basic_net`（或部署方批准的等价共享入口网络），并提供唯一别名 `automation-workbench-api`。按 `deploy/nginx.container.conf.example` 在共享Nginx新增独立路径；先执行Nginx配置检查和平滑重载，再验证工作台页面、登录、API文档、文件上传下载、子路径、请求大小和安全响应头，并回归现有 `/data-service/` 健康入口。
11. 先保持无计划、无调用方、无在线Agent，由服务器管理员完成工作台数据库单独dump，并同步备份平台卷，形成首次恢复点。

## 6. 试点启用与验收

1. 创建普通管理账号和项目角色，验证管理员、开发者、操作员、查看者的权限边界。
2. 连接一台试点执行电脑；核对Agent只需出站HTTPS，目录白名单和Windows账号权限符合预期。
3. 配置钉钉测试群，发送连接测试；再用合成失败任务验证异常告警和消息记录。
4. 上传已验证的Python/Node无副作用程序，分别验证参数、拖拽文件、目录输入、JSON结果和文件产物。
5. 验证手动、定时和影刀/API事件触发；网络重试必须复用原事件ID。
6. 验证执行电脑离线、程序失败、超时、取消、Worker重启和服务器重启后的状态。
7. 由服务器管理员完成一次“工作台独立数据库dump + 平台卷”的一致性备份，并在隔离实例执行恢复演练；不得对共享PostgreSQL整卷回退，恢复实例保持派发关闭。
8. 连续观察期和通过标准由业务负责人填写。完成正式启用决定后才把 `WB_DISPATCH_ENABLED` 改为 `true` 并滚动更新API/Worker。

## 7. 发布后观察

记录API/Worker健康、共享数据库连接/连接数/磁盘增长、待处理通知、离线电脑、排队任务、失败率、备份状态和证书期限。当前Worker没有HTTP健康端点，必须使用组件水位和独立监控，不以容器“正在运行”代替调度正常。共享PostgreSQL重启会同时影响现有数据服务与工作台，任何重启都必须走共同变更窗口。

## 8. 回退和停止条件

出现下列情况立即关闭派发并保留证据：登录或越权P0/P1、数据库/文件不一致、任务重复执行、跨项目数据暴露、密钥泄露、调度状态无法确认、恢复失败、钉钉持续异常或磁盘逼近保留阈值。

应用回退顺序：关闭派发 → 核对在途/unknown任务 → 停止API和Worker → 保存日志与执行器状态 → 按一致性恢复点决定“仅换镜像”或“工作台独立数据库与平台文件同时恢复”。数据库结构变化后不得只换旧镜像。不得删除或回退 `muyaju-data-reservoir-pg17` 整卷；恢复实例不得与原实例同时派发任务。

镜像回退只允许使用部署单记录的上一RepoDigest或校验通过的离线镜像包。外部系统已经发生的写入不随镜像或数据库回退撤销，必须按业务补偿方案处理。

## 9. 最终签署状态

| 状态 | 结果 |
| --- | --- |
| Codex开发与自动化验证 | rc.2核心链路通过，但前端构建路径不符合现场；rc.3重新构建和验证中 |
| CI | 未执行 |
| 开发人员自测确认 | 未确认 |
| 人工代码评审 | 未执行 |
| 运维部署审批 | 未批准 |
| 业务试点验收 | 未执行 |
| 允许正式派发 | 否 |
