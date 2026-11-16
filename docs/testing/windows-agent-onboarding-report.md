# Windows执行电脑简化接入测试报告

日期：2026-09-17
执行模式：全过程开发
任务等级：L2。涉及后端下载接口、Windows安装器、执行器凭据导入、前端接入流程和Docker制品，属于多模块联动与节点权限边界变更。

## 一、任务闭环

基准为 `main` 分支的未提交工作树；仓库当前尚无首个提交，所有项目文件均为未跟踪状态。本次未执行 commit、push、部署、真实启动项写入或生产外部系统操作。

| 需求 | 工作包 | 实现位置 | 验收用例 | 实际结果 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 登录后下载通用接入包 | W1 | `backend/workbench/agent_package.py`、`routes_catalog.py` | 包白名单、可重复生成、鉴权、无凭据 | 通过 | 完成 |
| 双击完成安装和连接 | W2 | `deploy/windows-agent/installer.py`、`安装并连接.cmd` | 真实CMD失败码、配置导入、重复安装、无效配置、运行时路径不执行 | 通过 | 完成 |
| 后台运行和登录后自启 | W3 | 安装器生成的Python包装程序与VBS | 隔离Startup目录、无令牌泄漏、生成程序编译 | 通过 | 完成 |
| 页面只保留三步 | W4 | `frontend/src/components/NodeConnectionForm.vue` | ZIP/连接文件下载、异常提示、不重复登记 | 通过 | 完成 |
| 空闲资源与日志边界 | W5 | `agent/config.py`、安装器日志包装程序 | 4.18小时采样、8 MiB中文换行单次输出 | 通过 | 完成 |

最终操作流程为：工作台登记执行电脑，依次下载“一键接入包”和“连接文件”，解压后双击 `安装并连接.cmd`，核对电脑、工作台、运行环境和允许目录，输入 `YES`。安装器复制执行器、导入节点凭据、后台启动，并写入当前Windows用户的Startup目录。

相关文件：

- 后端：`backend/workbench/agent_package.py`、`backend/workbench/routes_catalog.py`
- 安装器：`deploy/windows-agent/installer.py`、`deploy/windows-agent/安装并连接.cmd`、`requirements-agent.txt`、`使用说明.txt`
- 前端：`frontend/src/components/NodeConnectionForm.vue`
- 容器：`Dockerfile`
- 文档：`docs/quick-start.md`、`agent/README.md`
- 测试：`tests/backend/test_agent_package.py`、`tests/agent/test_windows_installer.py`、相关前端测试
- 可交付接入包：`dist/自动化工作台执行电脑接入包.zip`

## 二、测试证据

测试环境：Windows，本地Python 3.12测试环境，隔离测试数据库、合成连接文件、隔离安装目录和隔离Startup目录；没有写入真实Windows启动项，没有连接生产数据库或发送真实钉钉通知。

| 层级 | 命令/方式 | 结果 |
| --- | --- | --- |
| Windows接入专项 | `.venv\Scripts\python.exe -m pytest tests/agent/test_windows_installer.py tests/backend/test_agent_package.py -q` | 8通过，0失败，退出码0 |
| Python全量回归 | `.venv\Scripts\python.exe -m pytest -q --junitxml=runtime/windows-agent-python-results.xml` | 306通过，0失败，退出码0；2条第三方弃用警告 |
| Python静态检查 | `.venv\Scripts\python.exe -m ruff check ...` | 通过，退出码0 |
| 前端全量测试 | `npm test -- --run` | 11个文件、124项通过，退出码0 |
| 前端生产构建 | `npm run build` | 通过，退出码0 |
| Docker构建 | 本地缓存Node 24、Python 3.13基础镜像构建 `automation-workbench:onboarding-test` | 通过，退出码0 |
| 容器内制品冒烟 | 容器内生成ZIP并检查根目录白名单 | 16个条目；根目录仅4个固定安装文件，退出码0 |
| 独立复核 | 独立只读审查与真实包装程序容量测试 | 通过；无P0/P1/P2 |

默认Docker基础镜像Node 22、Python 3.12首次构建因Docker Hub令牌服务网络超时失败；随后使用本机已有Node 24和Python 3.13镜像完整构建通过。正式发布仍须用审核后的Node 22、Python 3.12镜像摘要重建。

资源采样：当前测试Agent运行4.18小时，Windows显示两个Python进程，合计工作集11.19 MiB，累计CPU 41.97秒，折合单核平均约0.279%。此前瞬时工作集约29 MiB，说明内存会随Python运行时回收而波动。空闲状态每2秒轮询、每15秒心跳；实际任务的CPU和内存峰值取决于业务脚本。Agent日志严格限制为当前5 MiB加备份5 MiB；8 MiB中文换行和7 MiB纯换行均完成真实写入验证。

最终ZIP大小27226字节，SHA-256：`EC9E264F3996C4A56968304E9C990FFBFCA3C05FBEB37B022AAD34065E11F45F`。

## 三、问题与风险

- P0：0。
- P1：0。CMD入口失败码问题已修复并复验。
- P2：0。接入包目录枚举、无界日志及Windows换行容量问题均已修复并经独立复验。
- 第三方测试警告：FastAPI/Starlette测试客户端存在2条弃用警告，不影响本次执行结果，后续依赖升级时处理。
- 连接文件含节点凭据且没有防篡改签名。安装器默认显示关键配置并要求输入 `YES`，但这不提供密码学防篡改能力；文件必须直接从可信工作台下载，导入后删除。
- 首次安装在本机Python 3.12缺少依赖时需要联网。独立环境下载依赖路径未在断网环境中判定可用。
- 首版是当前用户登录后自启，不是无人登录即可运行的Windows系统服务。真实用户登录、自启恢复、休眠唤醒及企业杀毒软件兼容尚未人工验证。
- 当前正在运行的Docker工作台没有部署本次新镜像；页面上的“一键接入包”按钮要在后续获批更新环境后出现。

回滚方式：在工作台停用节点，结束Agent进程，删除当前用户Startup目录中的对应VBS。保留连接目录便于审计；确认无需恢复后再由管理员人工清理。

## 四、质量状态

- Codex开发完成：是。
- 测试计划/用例已生成：是。
- 自动化测试已执行：是。
- 人工验证已执行：否。
- 剩余未验证项：真实用户Startup、自启恢复、联网依赖安装、企业网络/杀毒兼容、审核镜像摘要构建。
- 独立复核通过：是。
- CI通过：未执行。
- 开发最终确认：待用户/开发负责人。
- 人工代码评审通过：未执行。
- 允许发布：否，需完成上述真实环境门禁并取得发布审批。
- AI自检结论：有条件通过。代码与自动化证据无P0/P1/P2；生产门禁尚未完成。

Codex自检通过不等于开发自测确认、人工代码评审、CI验证或发布审批通过。
