# 简化执行电脑接入：开发与验证记录

日期：2026-09-17。模式：全过程开发；等级 L3（一次性凭据、文件权限与跨端配置）。范围限定为节点页面、专属表单/下载工具、连接导入入口及测试；无后端接口、数据库或生产配置变更。Git 为 unborn/untracked 基线；未 commit。

## 任务与验收

| 工作包 | 实现 | 验收场景 | 证据 |
| --- | --- | --- | --- |
| 普通表单接入 | `frontend/src/views/NodesView.vue`、`frontend/src/components/NodeConnectionForm.vue` | 电脑名称、Python/Node.js、执行电脑程序绝对路径；能力与运行时别名收在高级设置；固定单槽；下载后仍显示等待连接。 | 前端 10 项测试 |
| 一次性连接文件 | `frontend/src/nodeConnection.ts` | 内存持有一次性 token，不写浏览器 storage；真实 Blob JSON 与格式一致；下载失败可重试、不重新登记；释放临时下载地址。 | 前端 10 项测试 |
| 故障与作用域 | 节点表单、节点视图 | pending 重复提交只发一次 POST；列表刷新失败不丢签发结果；网络结果不明禁止盲目重试；切项目或身份时销毁旧表单，晚到结果不进入新项目。 | 前端 10 项测试（切项目已自动化；身份共用同步清理逻辑） |
| 导入与原入口兼容 | `agent/connection.py`、`agent/__main__.py`、`agent/README.md` | 新 `--import-connection` 离线导入，不安装/启动；默认用户专属目录，可指定全新目录；保留 `--config`；校验 HTTPS/本机运行时/字段/凭据/目录；token 与配置分文件。 | 原 agent 64 项 + 新导入 17 项 |
| 文件权限与失败清理 | `agent/connection.py` | Windows 真实当前身份 SID 与 icacls 收紧新目录权限；拒绝覆盖；坏输入不建目录；权限/写盘/权限工具超时故障不留下半份凭据；CLI 不输出 token。 | 导入 17 项（外部权限/写盘故障注入；正常导入和 CLI 为真实进程） |

## 实际执行

在仓库根目录：

```powershell
.venv/Scripts/python.exe -m pytest tests/agent -q
.venv/Scripts/python.exe -m pytest tests/agent/test_connection.py -q
.venv/Scripts/python.exe -m ruff check agent tests/agent/test_connection.py
.venv/Scripts/python.exe -m compileall -q agent
git diff --check
```

完整 agent 回归在增加权限工具超时分支前为 **80 passed in 19.60s**（原有 64 项、新增 16 项）。最终导入专项补充超时回归后为 **17 passed in 1.54s**，失败 0/跳过 0/退出码 0。Ruff、compileall、差异检查退出码 0。未把分开执行的数量伪称为一次完整 81 项运行。

在 `frontend` 目录：

```powershell
npm test -- tests/nodes.test.ts
npm exec -- prettier --check src/views/NodesView.vue src/components/NodeConnectionForm.vue src/nodeConnection.ts tests/nodes.test.ts
npm run build
```

最终节点测试 **10 passed**、Prettier 通过，退出码 0；包含实际 Blob 文件序列化，但 API 是测试边界 Stub，不宣称等于真实浏览器/服务全链路。vue-tsc 与 Vite 整体构建实际通过。第一次构建曾被其他并行工作包的 ProgramsView 模板错误阻断，其 owner 修复后重跑成功；未擅改该文件。新增下载测试曾因测试虚拟计时器冻结 FileReader 超时，改为仅模拟下载地址的 setTimeout 后通过；没有删测或放宽断言。

独立复核补充 P1：节点创建已发出后，201 正文损坏或缺少有效 id/token 不能视作可直接重试。已改为仅明确 4xx 拒绝允许修改后重试，其余未取得有效签发内容均标记结果不明；赋值前校验 id/token 类型与格式。新增损坏 JSON、缺少 token、token 类型错误 3 项全部通过，且断言重复提交仍仅一次 POST、不提供无效下载。最终前端 10 项与整体构建退出码 0。另一独立代理对导入/下载边界只读复核未报告新增 P0/P1；此结果不等于人工评审。

## 限制、回滚与质量状态

连接文件是明文凭据载体，应在导入后删除下载文件；本机落盘权限不构成同一 OS 用户业务程序之间的隔离。生产独立身份仍拒绝，当前保持 development/无外部写入限制。Windows 正常权限路径已真实执行；POSIX 权限分支未在本轮 Windows 环境实测。

仅下载/导入不会确认节点身份和在线状态；需用户启动现有执行器后由真实心跳完成验证。此流程不是一键安装、远程安装或系统服务注册。未操作真实业务接口、真实节点账号、影刀或钉钉。

无数据库迁移。回滚入口可恢复旧节点页面并移除导入功能，已有 `--config` 配置保持可用；本机导入目录不会自动删除。节点 REST 接口与身份验证规则不变。

Codex 开发完成：是；测试计划/用例：已生成；自动化执行：已执行；本工作包自检未发现未关闭 P0/P1。独立代码复核与真实 UI 全链路交由主线整合，本文不冒称通过。人工验证、人工代码评审、CI、开发最终确认、发布审批均未执行。AI 自检结论：**有条件通过**，限上述本机和组件验证边界。

Codex 自检不等于开发自测确认、人工代码评审、CI 验证或发布审批通过。
