# 目录输入合成示例

这四个程序只处理测试输入，不连接真实业务系统、不发送消息、不安装依赖。每个子目录只有一个独立入口，可以单独打包上传。

| 目录 | 运行环境 | 行为 | 预期结果 |
| --- | --- | --- | --- |
| `csv-merge` | Python 3.12 标准库 | 按清单递归选取 CSV，校验 SHA256、UTF-8 表头和行列数，流式合并 | 退出 0；`success`；产物 `merged.csv` |
| `node-summary` | Node.js 标准库 | 校验清单内文件 SHA256，统计文件数、字节数和扩展名 | 退出 0；`success`；产物 `directory-summary.json` |
| `fail` | Python 3.12 标准库 | 输出合成故障日志与失败报告 | 退出 7；`failed`；无产物 |
| `wait` | Python 3.12 标准库 | 每秒输出进度；检查平台取消文件；等待时间可配置 | 正常结束退出 0；主动取消退出 130 / `canceled`；平台强制超时时可能无结果报告 |

## 生成输入和程序包

在仓库根目录使用项目 Python 运行，路径换成你明确指定的测试空目录：

```powershell
.venv\Scripts\python.exe examples\folder-jobs\prepare.py --input-dir C:\workbench-test\input-001 --bundle-dir C:\workbench-test\packages-001
```

两个目标必须为空或不存在，并且互不包含；工具拒绝覆盖已有文件、链接路径。只想打包时仅传 `--bundle-dir`。输入包括根目录和子目录内共 2 个 CSV（3 行数据）、1 个 JSON、1 个文本文件。程序包目录生成 4 个 ZIP，以及包含启动命令、参数定义和示例参数的 `programs.json`。该文件中的批次号每次生成都不同。

工具不自动上传、创建任务或启动执行器。生成失败可能留下本次已写入的合成文件；不会自动删除，重新生成请指定新的空目录。

## 接入工作台

1. 上传相应 ZIP，选择 Python 或 Node.js 运行时，入口分别为 `main.py` / `main.js`。其他设置参考生成的 `programs.json`；四个程序均不勾选“向外部系统写入数据”。
2. 使用获授权的目录输入任务，并固定一台已配置输入根目录的执行电脑。生成元数据包含 `x-workbench-input: directory` 声明和 `directory-input-v1` 能力要求。任务不使用平台上传批次；按 `batch_key` 去重。参数 `input_dir` 是该执行电脑上的绝对目录，`batch_key` 是本次业务批次号；重复测试使用新的批次号。`wait` 另接受 `wait_seconds`，默认 30，范围 1 至 600。
3. 正常任务超时可设 60 秒；测试超时时将 `wait_seconds` 设 30、任务超时设 3 秒。平台实际任务状态应结合执行器观测确定，不能仅根据最后一条日志判断。
4. Node.js 示例需执行电脑配置可用的 `node` 运行时。本示例包不携带解释器，不自动安装软件。

平台仍以原协议添加 `--context-file <绝对路径>`。脚本使用 `context.parameters`、`input_manifest`、`output_dir`、`cancel_file`，结果 `run_id` 和 `attempt_id` 与当前上下文保持一致。目录输入直接读取原目录，不复制文件；清单中的 path 是原文件绝对路径。运行期间应保持输入文件稳定。输出目录必须已由平台创建；四个脚本均拒绝把输出放在输入目录内部。脚本不会修改输入文件。损坏的上下文属于接入错误，可能在写业务结果之前直接退出。

## 自动化验证

仓库根目录运行：

```powershell
.venv\Scripts\python.exe -m pytest tests\folder_jobs -q
.venv\Scripts\python.exe -m ruff check examples\folder-jobs tests\folder_jobs
```

测试使用临时合成目录，真实启动 Python / Node.js 子进程，覆盖递归输入、结果标识、产物、输入不变、坏表头/坏行/哈希不符、主动失败、正常等待、取消文件与超时清理。Node.js 路径由测试环境查找；缺少 Node.js 会明确失败，不把核心语言测试跳过。
