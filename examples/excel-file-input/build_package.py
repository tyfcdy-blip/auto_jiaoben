"""生成独立示例包与参数定义，不覆盖已有文件。"""
import argparse
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent


def build(output: Path) -> None:
    schema_file = output.with_suffix('.parameter-schema.json')
    if output.exists() or schema_file.exists():
        raise FileExistsError('程序包或参数定义已存在，拒绝覆盖')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
        for name in ('main.py', 'requirements.txt', 'THIRD_PARTY_NOTICES.txt'):
            archive.write(ROOT / name, name)
    with schema_file.open('x', encoding='utf-8') as stream:
        program = json.loads((ROOT / 'program.json').read_text(encoding='utf-8'))
        json.dump(program['parameter_schema'], stream, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output)
