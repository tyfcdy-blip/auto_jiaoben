"""使用隔离的openpyxl环境生成和检查合成工作簿。"""

import argparse
from datetime import datetime
import json
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.workbook.defined_name import DefinedName


def save_basic(path: Path, label: str) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Source"
    sheet.append(["区域", "金额", "说明", None])
    sheet.append([label + "-A", 10.25, "SYNTHETIC", None])
    sheet.append([label + "-B", 20.5, datetime(2026, 9, 17), None])
    sheet["B4"] = "=SUM(B2:B3)"
    sheet["B5"] = "='source'!B4"
    sheet["A1"].font = Font(bold=True, color="FFFFFFFF")
    sheet["A1"].fill = PatternFill("solid", fgColor="FF2563EB")
    sheet["A2"].comment = Comment("仅为合成测试", "Synthetic")
    sheet["A2"].hyperlink = "https://example.invalid/synthetic"
    sheet.merge_cells("C1:D1")
    sheet.column_dimensions["A"].width = 18
    sheet.row_dimensions[1].height = 24
    sheet.freeze_panes = "B2"
    sheet.auto_filter.ref = "A1:B3"
    validation = DataValidation(type="list", formula1='"Source!A,Keep"')
    validation.add("A2:A3")
    sheet.add_data_validation(validation)
    sheet.conditional_formatting.add("B2:B3", CellIsRule(operator="greaterThan", formula=["10"],
                                                          fill=PatternFill("solid", fgColor="FFFFC000")))
    sheet.conditional_formatting.add("A2", FormulaRule(formula=['"prefix"&Source!A2="prefixNORTH-A"'],
                                                        fill=PatternFill("solid", fgColor="FFDCFCE7")))
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 90
    sheet.print_area = "A1:D5"
    workbook.save(path)


def create(directory: Path, mode: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if mode == "basic":
        for subdirectory, label in (("north", "NORTH"), ("south", "SOUTH")):
            target = directory / subdirectory
            target.mkdir()
            save_basic(target / "report.xlsx", label)
    elif mode == "multi":
        workbook = Workbook()
        workbook.active["A1"] = "SYNTHETIC"
        workbook.create_sheet("Second")["A1"] = "SYNTHETIC"
        workbook.save(directory / "multi.xlsx")
    elif mode == "table":
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["name", "value"])
        sheet.append(["SYNTHETIC", 1])
        table = Table(displayName="SyntheticTable", ref="A1:B2")
        table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
        sheet.add_table(table)
        workbook.save(directory / "table.xlsx")
    elif mode == "defined-name":
        workbook = Workbook()
        sheet = workbook.active
        sheet["B1"] = 2
        sheet["A1"] = "=LocalRate*2"
        sheet.defined_names.add(DefinedName("LocalRate", attr_text="$B$1"))
        workbook.save(directory / "defined-name.xlsx")
    elif mode == "sparse":
        workbook = Workbook()
        sheet = workbook.active
        sheet["A1"] = "SYNTHETIC"
        sheet["XFD1048576"] = "SYNTHETIC"
        workbook.save(directory / "sparse.xlsx")
    elif mode == "corrupt":
        (directory / "corrupt.xlsx").write_bytes(b"SYNTHETIC_NOT_AN_XLSX")
    elif mode == "empty":
        (directory / "readme.txt").write_text("SYNTHETIC", encoding="utf-8")
    else:
        raise ValueError(mode)


def inspect(path: Path) -> None:
    workbook = load_workbook(path, data_only=False, read_only=False, rich_text=True)
    first = workbook[workbook.sheetnames[0]]
    conditional_formulas = [formula for conditional in first.conditional_formatting
                            for rule in first.conditional_formatting[conditional]
                            for formula in (rule.formula or [])]
    result = {
        "sheetnames": workbook.sheetnames,
        "values": [[first.cell(row, column).value for column in range(1, 5)] for row in range(1, 6)],
        "a1_bold": first["A1"].font.bold,
        "a1_fill": first["A1"].fill.fgColor.rgb,
        "a2_comment": first["A2"].comment.text if first["A2"].comment else None,
        "a2_hyperlink": first["A2"].hyperlink.target if first["A2"].hyperlink else None,
        "merged": [str(value) for value in first.merged_cells.ranges],
        "column_a_width": first.column_dimensions["A"].width,
        "row_1_height": first.row_dimensions[1].height,
        "freeze_panes": first.freeze_panes,
        "auto_filter": first.auto_filter.ref,
        "validations": len(first.data_validations.dataValidation),
        "validation_formula1": first.data_validations.dataValidation[0].formula1,
        "conditional_formats": len(first.conditional_formatting),
        "conditional_formulas": conditional_formulas,
        "show_grid_lines": first.sheet_view.showGridLines,
        "zoom": first.sheet_view.zoomScale,
        "print_area": str(first.print_area),
        "calculation": {"mode": workbook.calculation.calcMode,
                        "full": workbook.calculation.fullCalcOnLoad,
                        "force": workbook.calculation.forceFullCalc},
    }
    workbook.close()
    print(json.dumps(result, ensure_ascii=False, default=str))


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    creator = subparsers.add_parser("create")
    creator.add_argument("directory", type=Path)
    creator.add_argument("mode", choices=["basic", "multi", "table", "defined-name", "sparse", "corrupt",
                                                 "empty"])
    inspector = subparsers.add_parser("inspect")
    inspector.add_argument("path", type=Path)
    options = parser.parse_args()
    if options.operation == "create":
        create(options.directory, options.mode)
    else:
        inspect(options.path)


if __name__ == "__main__":
    main()
