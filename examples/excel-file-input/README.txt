Excel 文件选择与日期测试（独立示例）

用途：脚本直接读取执行电脑所选 .xlsx，输出各文件每页的行列数、非空单元格数和报表日期。
不联网、不发送消息、不修改源文件；不合并业务数据、不计算公式。

准备程序包：
python examples/excel-file-input/build_package.py --output <新目录>/excel-file-input.zip
将 ZIP 上传程序库，入口 main.py，运行环境 Python；参数定义复制旁边的 parameter-schema.json。
完整程序配置见 program.json。结果必需，能力包含 python 和 node-files-v1。

使用只需两步：
1. 在执行电脑为执行器实际使用的 Python 安装依赖：
   <执行器的Python路径> -m pip install -r requirements.txt
   requirements.txt 可从程序 ZIP 解压获得。固定版本 openpyxl 3.1.5、et_xmlfile 2.0.0。
2. 工作台进入绑定的执行电脑，创建任务并选本程序。选择授权目录下的 Excel 文件，
   选择报表日期、结果保存文件夹后运行。定时计划可将日期设为“计划当天”。
   本示例要求完整日期，系统不提供“上一月”选项。Excel 内容由脚本读取，不需手写 JSON。

结果：本机所选保存目录/运行编号/excel-summary.xlsx，运行详情也可下载同一文件。
脚本只写 context.output_dir，工作台负责复制到指定保存目录并归档；无需服务器共享文件夹。

限制：最多 10 文件，总计 10 MiB；每文件最多 10 页，每页最多 10000 行、100 列，
全部工作表合计最多 20 万单元格位置；每文件解压大小最多 100 MiB、最多 2000 ZIP 项。
行数含表头和表格有效矩形内空行；空单元格不计入非空数；公式只读取文件已有缓存值。
仅支持 .xlsx，不保留样式、不求和业务金额、不读取图表；只使用合成文件首次测试。
结果文件名固定，不接受用户提供的路径或文件名作为输出文件名。
