"""仅创建合成账单/映射，并读取测试生成的报表；不接触真实业务Excel。"""

import argparse
import json
from pathlib import Path

from openpyxl import Workbook, load_workbook


def create(directory: Path, scenario: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    mapping = Workbook()
    mapping.active.title = "财报"
    mapping.active.append(["店铺编码", "店铺名称"])
    shops = ["SYNTHETIC_SHOP_A", "SYNTHETIC_SHOP_B"]
    for index, shop in enumerate(shops, start=1):
        if scenario != "missing_mapping" or index == 1:
            mapping.active.append([f"SYNTHETIC_{index:03}", shop])
        workbook = Workbook()
        orders = workbook.active
        orders.title = "订单明细"
        orders.append(["订单号", "订单状态", "订单成交时间", "商家实收金额(元)"])
        orders.append(["SYNTHETIC_ORDER_1", "已完成",
                       "INVALID_DATE" if scenario == "invalid_date" and index == 1 else "2026-07-10 12:00:00",
                       "INVALID_AMOUNT" if scenario == "invalid_amount" and index == 1 else "100.10"])
        orders.append(["SYNTHETIC_ORDER_2", "已取消", "2026-07-11 12:00:00", "999"])
        orders.append(["SYNTHETIC_ORDER_3", "已完成", "2026-08-01 12:00:00", "200"])
        orders.append(["SYNTHETIC_ORDER_4", "已完成", None, "20.20"])
        orders.append(["SYNTHETIC_ORDER_5", "已完成", "2026-07-15 12:00:00", "9.70"])
        promotion = workbook.create_sheet("推广明细")
        promotion.append(["时间", "资金类型", "流水类型", "交易金额"])
        promotion.append(["2026-07-10 12:00:00", "现金", "支出", "-8.50"])
        promotion.append(["2026-07-10 12:00:00", "现金", "收入", "12.00"])
        gift = workbook.create_sheet("评价有礼")
        cashflow = workbook.create_sheet("资金流水")
        for sheet, time_column in ((gift, "入账时间"), (cashflow, "发生时间")):
            for _ in range(4):
                sheet.append([None])
            sheet.append(["商户订单号", time_column, "账务类型", "收入金额（+元）", "支出金额（-元）", "业务描述"])
        gift.append(["SYNTHETIC_ORDER_1", "2026-07-10", "合成明细", "0", "-3", "合成评价奖励"])
        gift.append(["SYNTHETIC_ORDER_2", "2026-07-11", "合成明细", "1", "0", "合成奖励返还"])
        cashflow.append(["SYNTHETIC_ORDER_1", "2026-07-10", "合成明细", "130", "0", "0010002|交易收入-订单收入"])
        cashflow.append(["SYNTHETIC_ORDER_2", "2026-07-11", "合成明细", "0", "-6", "0030001|技术服务费-技术服务费"])
        if scenario == "missing_sheet" and index == 1:
            workbook.remove(cashflow)
        if scenario == "empty_data":
            for sheet in (orders, promotion):
                sheet.delete_rows(2, sheet.max_row)
            for sheet in (gift, cashflow):
                sheet.delete_rows(6, sheet.max_row)
        workbook.save(directory / f"{shop}.xlsx")
        workbook.close()
    mapping.save(directory / "shops.xlsx")
    mapping.close()


def inspect(path: Path) -> dict:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(workbook["资金流水"].iter_rows(values_only=True))
        return {"sheets": workbook.sheetnames, "merchant_receivable": workbook["订单明细"]["B2"].value,
                "promotion": workbook["推广费"]["B2"].value, "review_gift": workbook["评价有礼"]["B2"].value,
                "cashflow_total": list(rows[-1])}
    finally:
        workbook.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    make = sub.add_parser("create")
    make.add_argument("--directory", type=Path, required=True)
    make.add_argument("--scenario", choices=["normal", "missing_sheet", "invalid_amount", "invalid_date",
                                            "empty_data", "missing_mapping"], default="normal")
    read = sub.add_parser("inspect")
    read.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "create":
        create(args.directory, args.scenario)
    else:
        print(json.dumps(inspect(args.report), ensure_ascii=False))


if __name__ == "__main__":
    main()
