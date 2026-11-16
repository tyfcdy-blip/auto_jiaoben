# Excel单页签合并为多页签

将同一文件夹中的多个单页签 `.xlsx` 合并成一个工作簿。每个源文件对应一个输出页签，页签顺序按相对文件名排序，页签名称来自文件名；同名自动加 `(2)`、`(3)`。源文件只读，结果从工作台运行详情下载。

## 可直接上传的文件

使用 `dist/excel-sheet-merger.zip`。ZIP已包含入口、openpyxl 3.1.5、et_xmlfile 2.0.0及MIT许可证，执行电脑只需工作台登记的Python 3.12，不需要另行安装Excel处理包。

## 放入工作台

1. 输入文件先放入执行电脑已允许读取的根目录或子目录；关闭所有源文件。
2. 打开工作台，选择项目，进入“程序管理”，点击接入程序。
3. 程序名称填“Excel单页签合并为多页签”；输入方式选“执行电脑上的文件夹”；运行环境选Python；入口填 `main.py`。
4. 选择 `dist/excel-sheet-merger.zip`，勾选“校验通过后发布”，点击“上传并发布”。
5. 进入“任务管理”创建任务，选择刚发布的程序和固定执行电脑；输入文件夹填源Excel所在目录；批次编号先填测试编号，例如 `excel-merge-test-001`。
6. 展开高级设置，把“最长执行时间”设为300秒；“其他默认业务参数”保持 `{}`。创建任务。
7. 点击“立即运行”，每次换一个批次编号。到“运行中心”查看日志，成功后下载 `合并结果.xlsx`。

定时计划和影刀触发沿用目录任务规则。影刀必须在全部文件下载并关闭后触发；只传 `input_dir`、`batch_key`。网络重试沿用原event_id和batch_key。输出文件名固定为 `合并结果.xlsx`。

## 明确限制

- 每个源 `.xlsx` 必须且只能有一个可见普通工作表。
- 最多50个源文件，输入合计不超过50MiB，输出不超过20MiB；单个工作表的有效矩形区域最多200万个单元格位置，防止极端稀疏表拖垮执行电脑。
- 保留单元格值、普通公式、样式、批注、超链接、合并、行列尺寸、冻结窗格、筛选、打印设置、数据验证和条件格式。公式由Excel打开时重算。
- 首版拒绝 `.xls`、`.xlsm`、模板、密码保护、外部链接、名称定义、Excel表格对象、图表、图片和透视对象，避免生成内容缺失但显示成功的结果。
- 文件夹中的非Excel文件会忽略；发现旧格式或宏Excel会整次失败并提示文件名。

## 重新构建程序包

依赖Python必须已有requirements.txt中的固定版本。示例：

```powershell
.venv/Scripts/python.exe examples/excel-sheet-merger/build_package.py `
  --dependency-python integrations/nine/.venv/Scripts/python.exe `
  --output examples/excel-sheet-merger/dist/excel-sheet-merger.zip
```

构建器拒绝覆盖已有ZIP。发布新版本时使用新的空输出路径，并重新执行测试。
