from io import BytesIO

from openpyxl import Workbook

from app.ledger.balances import trial_balance
from app.ledger.report_service import balance_sheet, income_statement

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _num(formatted: str) -> float:
    return float(formatted)


def _new_workbook(sheet_title: str, title: str, info: dict[str, str]):
    workbook = Workbook()
    ws = workbook.active
    ws.title = sheet_title
    ws.append([title])
    ws.append([f"账套：{info['book_name']}", f"期间：{info['period']}", "单位：元"])
    ws.append([])
    return workbook, ws


def _save(workbook) -> bytes:
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def export_trial_balance(payload: dict) -> bytes:
    workbook, ws = _new_workbook("科目余额表", "科目余额表", payload)
    ws.append([
        "科目编码", "科目名称", "期初借方", "期初贷方",
        "本期发生借方", "本期发生贷方", "期末借方", "期末贷方",
    ])
    for row in payload["rows"]:
        ws.append([
            row["account_code"], row["account_name"],
            _num(row["opening_debit"]), _num(row["opening_credit"]),
            _num(row["period_debit"]), _num(row["period_credit"]),
            _num(row["closing_debit"]), _num(row["closing_credit"]),
        ])
    totals = payload["totals"]
    ws.append([
        "合计", "",
        _num(totals["opening_debit"]), _num(totals["opening_credit"]),
        _num(totals["period_debit"]), _num(totals["period_credit"]),
        _num(totals["closing_debit"]), _num(totals["closing_credit"]),
    ])
    return _save(workbook)


def export_balance_sheet(payload: dict) -> bytes:
    workbook, ws = _new_workbook("资产负债表", "资产负债表", payload)
    ws.append(["项目", "期末余额", "年初余额"])
    for row in payload["rows"]:
        ws.append([row["name"], _num(row["closing"]), _num(row["year_begin"])])
    return _save(workbook)


def export_income_statement(payload: dict) -> bytes:
    workbook, ws = _new_workbook("利润表", "利润表", payload)
    ws.append(["项目", "本月金额", "本年累计金额"])
    for row in payload["rows"]:
        ws.append([row["name"], _num(row["month"]), _num(row["year_to_date"])])
    return _save(workbook)


EXPORT_BUILDERS = {
    "trial-balance": (trial_balance, export_trial_balance),
    "balance-sheet": (balance_sheet, export_balance_sheet),
    "income-statement": (income_statement, export_income_statement),
}
