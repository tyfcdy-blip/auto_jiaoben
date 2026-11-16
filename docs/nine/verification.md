# nine M5.1 验证记录

日期：2026-09-16。模式：修复与接入验证，仅本工作包代码及合成数据。没有执行 `--execute`、真实网络请求、真实账单读取、钉钉发送或原项目修改。

## 源码与依赖依据

只读来源为用户明确指定的 nine 目录，两份脚本的 SHA256：

| 文件 | SHA256 |
| --- | --- |
| pdd_billing_check.py | `1a688ae7810cc553641307d7fbee9e7b08a202ddd55d3cb8a11ed41f39c562ad` |
| pdd_billing_warehouse_load.py | `a6e39b4df19bdb1995c5ba628f404c900f3fe0c0216365a4135568ff0678e409` |

测试期间将源文件逐字节复制到临时包，逐文件验证与来源一致。未读取 `warehouse.local.json`、真实 `拼多多店铺账号.xlsx` 或账期文件。

独立运行环境位于 `integrations/nine/.venv`，Python 3.12，安装 `openpyxl 3.1.5` 和 `et_xmlfile 2.0.0`。依赖通过本机已配置的清华 PyPI 镜像获取；没有修改平台 requirements 或全局 Python。两个库使用 MIT 许可，发布前仍需按完整交付清单核对依赖来源、许可证和安全状态。

## 实际命令与结果

```powershell
.\.venv\Scripts\python.exe -m venv integrations/nine/.venv
.\integrations\nine\.venv\Scripts\python.exe -m pip install -r integrations/nine/requirements.txt
$env:NINE_SOURCE_DIRECTORY = 'C:\Users\JIE\Desktop\拼多多\nine'
$env:NINE_TEST_PYTHON = (Resolve-Path 'integrations/nine/.venv/Scripts/python.exe').Path
.\.venv\Scripts\python.exe -m pytest tests/nine -q
.\.venv\Scripts\python.exe -m ruff check integrations/nine tests/nine
.\integrations\nine\.venv\Scripts\python.exe -m pip check
```

以上均退出 0；pytest **17 通过、0 失败、0 跳过**；Ruff 通过，依赖检查未发现冲突。未配置两个环境变量的机器会明确跳过原程序集成测试，不能把该情况计为通过；不擅自寻找或读取个人业务目录。

| 编号 | 实际场景 | 结果 |
| --- | --- | --- |
| N01 | 两店铺报表真实生成；每店商家实收130、推广费-8.5、评价有礼-2、流水收入130/支出-6 | 通过；输入SHA256不变 |
| N02 | 原程序离线预检，2文件22行，manifest execute=false | 通过；无请求日志或编号状态生成 |
| N03–N06 | check/preflight分别遇到缺sheet或缺店铺映射 | 全部非零退出且failed |
| N07–N08 | 非法金额/日期的原预检仍通过，而原报表退出2 | 如实验证并披露；未冒充完整数据校验 |
| N09–N10 | 各表保留表头但无明细，原报表/预检仍成功 | 保留原行为，零合计/零行数不等于业务数据已齐套 |
| N11–N15 | upload、修改后输入、execute额外参数、非法账期、重复账单文件 | 在旧程序启动前拒绝 |
| N16 | 启动前取消标志已存在 | 原程序未执行，返回canceled |
| N17 | 合成脚本试图建立socket | 审计钩子在连接前阻止；没有发出请求 |

所有输出位于 pytest 临时目录，来源代码及真实文件保持原样。结果 JSON 逐次按现有 `contracts/runtime/result.schema.json` 校验，上下文也按已有 schema 校验。

## 质量与边界

M5.1 的本地适配/合成数据验证已完成；本记录不宣称 M5.2 授权影刀联调、M5.3 正式上传/恢复、Linux 兼容、容器部署或生产验收通过。预检不逐字段校验金额和日期是已复现的原有局限，原程序未被擅自修改。

Codex 自检通过不等于开发自测确认、人工代码评审、CI 验证或发布审批通过。
