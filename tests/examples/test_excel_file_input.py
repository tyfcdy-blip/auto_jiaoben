"""独立 Excel 环境运行示例；不向平台核心环境添加 Excel 依赖。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import zipfile

from jsonschema import validate
import pytest

from workbench.bundle_scan import scan_bundle

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / 'examples/excel-file-input'


@pytest.fixture
def excel_python():
    configured = os.environ.get('EXCEL_TEST_PYTHON')
    if not configured:
        pytest.skip('需配置 EXCEL_TEST_PYTHON 指向安装固定 Excel 依赖的独立 Python')
    python = str(Path(configured).absolute())
    result = subprocess.run([python, '-c', 'import openpyxl; assert openpyxl.__version__ == "3.1.5"'],
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return python


def create_xlsx(python, path, rows=None, title='数据'):
    result = subprocess.run([python, '-c', '''import json, sys
from openpyxl import Workbook
config=json.loads(sys.stdin.read())
book=Workbook(); sheet=book.active; sheet.title=config['title']
for row in config['rows']: sheet.append(row)
book.save(config['path']); book.close()
'''], input=json.dumps({'path': str(path), 'title': title,
                       'rows': rows if rows is not None else [['编号', '数量'], ['SYNTHETIC', 3]]}),
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def run_example(python, tmp_path, parameters):
    output = tmp_path / 'output'
    output.mkdir(exist_ok=True)
    context = {'run_id': 'synthetic-excel', 'attempt_id': 'synthetic-attempt',
               'output_dir': str(output), 'parameters': parameters}
    context_file = tmp_path / 'context.json'
    context_file.write_text(json.dumps(context), encoding='utf-8')
    process = subprocess.run([python, str(EXAMPLE / 'main.py'), '--context-file', str(context_file)],
                             capture_output=True, timeout=20)
    result = json.loads((output / 'result.json').read_text(encoding='utf-8'))
    validate(result, json.loads((ROOT / 'contracts/runtime/result.schema.json').read_text(encoding='utf-8')))
    return process, result, output


def test_package_schema_scan_and_no_overwrite(tmp_path):
    spec = importlib.util.spec_from_file_location('excel_input_builder', EXAMPLE / 'build_package.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    package = tmp_path / 'example.zip'
    builder.build(package)
    with zipfile.ZipFile(package) as archive:
        assert set(archive.namelist()) == {'main.py', 'requirements.txt', 'THIRD_PARTY_NOTICES.txt'}
    schema = json.loads(package.with_suffix('.parameter-schema.json').read_text(encoding='utf-8'))
    assert schema['properties']['sources']['x-workbench-file'] == 'files'
    assert schema['properties']['report_date']['format'] == 'date'
    scanned = scan_bundle(package.read_bytes(), 'main.py', 'python')
    assert scanned['decision'] == 'pass', scanned
    with pytest.raises(FileExistsError):
        builder.build(package)
    package.unlink()
    with pytest.raises(FileExistsError):
        builder.build(package)
    assert not package.exists()


def test_summary_date_formula_text_and_inputs_unchanged(excel_python, tmp_path):
    source = tmp_path / '=SUM(1).xlsx'
    create_xlsx(excel_python, source, title='=SUM(2)')
    before = source.read_bytes()
    process, result, output = run_example(excel_python, tmp_path, {
        'sources': [str(source)], 'report_date': '2026-11-17'})
    assert process.returncode == 0, process.stderr
    assert result['status'] == 'success'
    assert result['metrics'] == {'files': 1, 'sheets': 1, 'report_date': '2026-11-17'}
    assert result['artifacts'] == [{'name': 'excel-summary.xlsx', 'required': True}]
    inspected = subprocess.run([excel_python, '-c', '''import json, sys
from openpyxl import load_workbook
book=load_workbook(sys.argv[1],read_only=True,data_only=False)
try: print(json.dumps([[(cell.value,cell.data_type) for cell in row] for row in book.active.iter_rows()]))
finally: book.close()
''', str(output / 'excel-summary.xlsx')], capture_output=True, text=True, timeout=10)
    assert inspected.returncode == 0, inspected.stderr
    cells = json.loads(inspected.stdout)
    assert cells[1] == [['=SUM(1).xlsx', 's'], ['=SUM(2)', 's'], [2, 'n'], [2, 'n'],
                        [4, 'n'], ['2026-11-17', 's']]
    assert source.read_bytes() == before
    source.rename(tmp_path / 'source-after-run.xlsx')


@pytest.mark.parametrize('failure', ['empty', 'too-many', 'duplicate', 'date', 'month-token',
                                      'missing', 'extension', 'corrupt', 'xml', 'columns', 'rows',
                                      'expanded', 'size', 'occupied'])
def test_invalid_inputs_fail_without_success_artifacts(excel_python, tmp_path, failure):
    source = tmp_path / 'synthetic.xlsx'
    create_xlsx(excel_python, source)
    parameters = {'sources': [str(source)], 'report_date': '2026-11-17'}
    if failure == 'empty':
        parameters['sources'] = []
    elif failure == 'too-many':
        parameters['sources'] *= 11
    elif failure == 'duplicate':
        parameters['sources'] *= 2
    elif failure == 'date':
        parameters['report_date'] = '2026-02-30'
    elif failure == 'month-token':
        parameters['report_date'] = '${previous_month}'
    elif failure == 'missing':
        parameters['sources'] = [str(tmp_path / 'missing.xlsx')]
    elif failure == 'extension':
        renamed = source.with_suffix('.xls')
        source.rename(renamed)
        parameters['sources'] = [str(renamed)]
    elif failure == 'corrupt':
        source.write_bytes(b'not a workbook')
    elif failure == 'xml':
        with zipfile.ZipFile(source) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries['xl/worksheets/sheet1.xml'] = b'<invalid'
        with zipfile.ZipFile(source, 'w') as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
    elif failure == 'columns':
        create_xlsx(excel_python, source, rows=[list(range(101))])
    elif failure == 'rows':
        create_xlsx(excel_python, source, rows=[[1]] * 10001)
    elif failure == 'expanded':
        with zipfile.ZipFile(source, 'a', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('synthetic-padding', b'x' * (100 * 1024 * 1024))
    elif failure == 'size':
        with source.open('ab') as stream:
            stream.write(b'x' * (10 * 1024 * 1024))
    elif failure == 'occupied':
        (tmp_path / 'output').mkdir()
        (tmp_path / 'output/excel-summary.xlsx').write_bytes(b'KEEP')
    process, result, output = run_example(excel_python, tmp_path, parameters)
    assert process.returncode == 1
    assert result['status'] == 'failed' and result['artifacts'] == []
    if failure == 'occupied':
        assert (output / 'excel-summary.xlsx').read_bytes() == b'KEEP'
    else:
        assert not (output / 'excel-summary.xlsx').exists()
