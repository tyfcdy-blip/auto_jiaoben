"""只读统计所选 Excel 的规模，日期直接采用工作台传入的固定值。"""
import argparse
from datetime import date
import json
from pathlib import Path
from xml.etree.ElementTree import ParseError
import zipfile

from openpyxl import Workbook, load_workbook

MAX_FILES = 10
MAX_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_CELLS = 200_000
OUTPUT_NAME = 'excel-summary.xlsx'


def read_summary(parameters: dict) -> list[list]:
    sources = parameters.get('sources')
    report_date = parameters.get('report_date')
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_FILES:
        raise ValueError('请选择 1 至 10 个 .xlsx 文件')
    if not isinstance(report_date, str) or date.fromisoformat(report_date).isoformat() != report_date:
        raise ValueError('报表日期必须是 YYYY-MM-DD 格式的有效日期')
    paths = []
    for source in sources:
        if not isinstance(source, str) or not source:
            raise ValueError('文件参数必须是非空路径')
        path = Path(source)
        if path.suffix.lower() != '.xlsx' or path.is_symlink() or not path.is_file():
            raise ValueError('仅支持已存在的普通 .xlsx 文件')
        paths.append(path.resolve())
    if len(set(paths)) != len(paths):
        raise ValueError('不能重复选择同一文件')
    if sum(path.stat().st_size for path in paths) > MAX_BYTES:
        raise ValueError('输入文件合计不得超过 10 MiB')
    rows, cells = [], 0
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > 2000 or sum(entry.file_size for entry in archive.infolist()) > MAX_EXPANDED_BYTES:
                raise ValueError('工作簿解压规模超过示例上限')
        workbook = load_workbook(path, read_only=True, data_only=True, keep_links=False)
        try:
            if len(workbook.worksheets) > 10:
                raise ValueError('每个文件最多支持 10 个页签')
            for sheet in workbook.worksheets:
                if (sheet.max_row or 0) > 10000 or (sheet.max_column or 0) > 100:
                    raise ValueError('每页最多支持 10000 行、100 列')
                row_count, columns, nonempty = 0, 0, 0
                for values in sheet.iter_rows(values_only=True):
                    row_count += 1
                    columns = max(columns, len(values))
                    cells += len(values)
                    if row_count > 10000 or columns > 100 or cells > MAX_CELLS:
                        raise ValueError('输入数据量超过示例上限')
                    nonempty += sum(value is not None for value in values)
                rows.append([path.name, sheet.title, row_count, columns, nonempty, report_date])
        finally:
            workbook.close()
    return rows


def execute(context: dict) -> dict:
    output = Path(context['output_dir'])
    result = {'protocol_version': '1', 'run_id': context['run_id'], 'attempt_id': context['attempt_id'],
              'status': 'failed', 'summary': 'Excel 统计未完成', 'artifacts': []}
    try:
        rows = read_summary(context['parameters'])
        workbook = Workbook()
        try:
            sheet = workbook.active
            sheet.title = '文件统计'
            sheet.append(['文件名', '页签', '行数（含表头）', '列数', '非空单元格', '报表日期'])
            for row in rows:
                sheet.append(row)
                # 文件名、页签来自输入文件，按纯文本保存，防止被 Excel 当作公式执行。
                for column in (1, 2, 6):
                    sheet.cell(sheet.max_row, column).data_type = 's'
            with (output / OUTPUT_NAME).open('xb') as stream:
                workbook.save(stream)
        finally:
            workbook.close()
        result.update(status='success', summary=f'已统计 {len(rows)} 个页签',
                      metrics={'files': len(context['parameters']['sources']), 'sheets': len(rows),
                               'report_date': context['parameters']['report_date']},
                      artifacts=[{'name': OUTPUT_NAME, 'required': True}])
    except (OSError, ValueError, TypeError, KeyError, ParseError, zipfile.BadZipFile) as exc:
        result['summary'] = f'Excel 统计失败：{exc}'[:2000]
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context-file', type=Path, required=True)
    context = json.loads(parser.parse_args().context_file.read_text(encoding='utf-8'))
    result = execute(context)
    print(result['summary'], flush=True)
    raise SystemExit(0 if result['status'] == 'success' else 1)
