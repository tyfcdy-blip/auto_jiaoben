# 目录输入执行器验证记录

日期：2026-09-17。工作模式：全过程开发并独立复核主线后端；等级 L3（本地文件范围、领取能力与执行状态）。只修改 agent、领取 JSON Schema 和 tests/agent 新测试。现有 unborn/untracked 内容视为基线；无 commit、数据库变更、真实业务或共享目录操作。

## 实现与验收追踪

| 工作项 | 实现文件 | 验证与结果 |
| --- | --- | --- |
| 旧配置兼容、受控根目录 | config.py、connection.py、config.example.json | input_roots 可省略，旧七字段连接文件仍可导入；非列表、相对/缺失/设备路径拒绝；有效根须真实存在并无祖先链接。 |
| 领取能力及协议兼容 | runner.py、contracts/runtime/claim.schema.json | 普通 claim 不变；只有配置根目录才自动报告 directory-input-v1，手填能力不能代替根；自动能力计入服务端 30 项上限。目录 claim 须和 parameters.input_dir 同值且 input_files 为空。 |
| 直接读取目录 | directory_input.py、runner.py | 真实业务子进程通过原文件绝对路径读取；相对子目录名和 SHA256 正确；原 context 字段不变、input_dir 原始值保留；本地 input 目录没有复制文件。 |
| 文件和容量边界 | directory_input.py | 最多 1000 个普通文件、1000 个目录、深度 32、总量按 max_expanded_bytes；越界、缺失、空目录、超额、硬链接、实际 Windows junction/重解析点均拒绝。 |
| 变化与故障 | directory_input.py、runner.py | 真实文件增长、读取期间新增文件被检测；读取前后与整体清单结束时复查元数据；权限故障注入回报 startup_failed，无 started 请求；终态重传不重新执行。 |

新测试：`tests/agent/test_directory_input.py`，**25 项**。正常读取、hash、子进程与 Windows junction/hardlink 均实际执行；HTTP 控制面使用现有测试 Stub，目录权限失败通过文件系统边界故障注入。没有 Mock 核心目录枚举、摘要计算和业务进程。

## 实际命令与结果

```powershell
.venv/Scripts/python.exe -m pytest tests/agent/test_directory_input.py -q
.venv/Scripts/python.exe -m pytest tests/agent -q
.venv/Scripts/python.exe -m ruff check agent tests/agent/test_directory_input.py
.venv/Scripts/python.exe -m compileall -q agent
git diff --check
```

最终目录专项：**25 passed in 2.00s**；最终全部 agent 回归：**106 passed in 20.67s**。失败 0、跳过 0、退出码 0。Ruff、compileall、差异检查退出码 0。全部测试仅用可清理的合成目录；junction 清理只移除本测试连接，不递归删除目标。

独立只读复核后端 `input_sources.py`、`routes_catalog.py`、`execution.py`、`routes_agent.py`、`routes_operations.py`、`storage.py` 与 `test_folder_inputs.py`：版本元数据声明、自动能力、固定同项目节点、batch_key 去重、拒绝混合平台输入、计划与事件统一参数约束、领取门禁及派生 input_mode 成立，未发现可证实新增 P0/P1。

独立执行后端证据（读取本机隔离测试数据库指针，不打印凭据；fixture 自建独立 schema）：

```powershell
.venv/Scripts/python.exe -c "import json,pathlib,os,subprocess,sys; metadata=json.loads(pathlib.Path('runtime/postgres17/instance.local.json').read_text(encoding='utf-8')); os.environ['WORKBENCH_TEST_DATABASE_FILE']=metadata['connection_file']; raise SystemExit(subprocess.call([sys.executable,'-m','pytest','tests/backend/test_folder_inputs.py','-q']))"
```

结果：**22 passed, 2 warnings in 2.67s，退出码 0**。两个提示来自现有 Starlette/httpx/anyio 测试依赖弃用，未升级依赖或忽略失败。该后端测试采用真实 PostgreSQL 和 TestClient，不冒称真实网络全链路。

## 边界与质量状态

本能力**不复制、不上传、不冻结输入**。检查结束后文件仍可能变化；上游应提供稳定交付目录，业务程序不能假设这是不可变快照。白名单及摘要不是恶意代码 OS 沙箱；开发程序仍与执行器处于现有身份约束内，side_effects 与生产身份限制未放宽。

真实网络共享的访问账号、映射盘可见性、网络断开与操作系统单次 I/O 长时间阻塞未验证；Linux/POSIX 分支未在本轮 Windows 环境实测。虽然读取循环可按周期心跳，OS 阻塞无法由此白名单机制中断。真实平台/浏览器/新脚本综合测试由主线完成，本文不预先声明其通过。

回滚无数据库迁移；删除 input_roots 即停止报告目录能力，目录任务留队或失败而不由旧能力执行器接单；原批次方式、--config 与七字段连接导入保留兼容。不能删除未交付或 unknown 执行状态来“回滚”。

Codex 开发完成：是；计划/用例：已生成；自动化执行：已完成上述范围；新 P0/P1：未发现；本 agent 模块的外部独立代码复核：由主线安排，本文不自称完成。人工验证、人工代码评审、CI、开发最终确认和发布审批：未执行。AI 自检结论：**有条件通过，限可信开发与本机合成目录范围**。

Codex 自检不等于开发自测确认、人工代码评审、CI 验证或发布审批通过。
