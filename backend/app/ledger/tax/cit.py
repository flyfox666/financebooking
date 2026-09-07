from calendar import monthrange
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.ledger.balances import fmt_amount
from app.ledger.report_service import income_statement, _load_template, _metric_terms
from app.ledger.tax.params import get_param
from app.ledger.exceptions import LedgerError
from app.models.book import Book
from app.models.account import Account

TWO = Decimal("0.01")
ZERO = Decimal("0.00")


def _quarter_end(year: int, quarter: int) -> str:
    return f"{year}-{quarter * 3:02d}"


def calc_cit(
    db: Session,
    *,
    book_id: int,
    year: int,
    quarter: int,
    employees: Decimal | None = None,
    assets: Decimal | None = None,
    prepaid_prev: Decimal = ZERO,
    industry_eligible: bool | None = None,
    adjustment_net: Decimal = ZERO,
    adjustments_confirmed: bool = False,
) -> dict:
    if not 2000 <= year <= 2098 or quarter not in (1, 2, 3, 4):
        raise LedgerError("年度或季度无效")
    for value in (employees, assets, prepaid_prev, adjustment_net):
        if value is not None and (not Decimal(str(value)).is_finite() or abs(Decimal(str(value))) > Decimal("1e14")):
            raise LedgerError("请输入有效且范围合理的数值")
    if any(value is not None and Decimal(str(value)) < 0 for value in (employees, assets, prepaid_prev)):
        raise LedgerError("人数、资产和已预缴金额不能为负数")
    book = db.get(Book, book_id)
    if book is None:
        raise LedgerError("账套不存在")
    terms = _metric_terms(_load_template(db, book_id, "is"), "total_profit")
    codes = {row.code for row in db.query(Account).filter_by(book_id=book_id).all()}
    if set(terms) - codes:
        raise LedgerError("利润总额引用了不存在的科目，请先检查报表映射")
    period = _quarter_end(year, quarter)
    statement = income_statement(db, book_id=book_id, period=period)
    profit_row = next((r for r in statement["rows"] if r["key"] == "total_profit"), None)
    if profit_row is None:
        raise LedgerError("利润表缺少利润总额项目，请先检查报表映射")
    profit_ytd = Decimal(profit_row["year_to_date"])
    actual_profit = profit_ytd + Decimal(str(adjustment_net))
    on_date = date(year, quarter * 3, monthrange(year, quarter * 3)[1])

    max_income = get_param(db, book_id, "cit", "max_taxable_income", on_date)
    max_employees = get_param(db, book_id, "cit", "max_employees", on_date)
    max_assets = get_param(db, book_id, "cit", "max_assets", on_date)
    included_rate = get_param(db, book_id, "cit", "small_included_rate", on_date)
    rate = get_param(db, book_id, "cit", "rate", on_date)

    conditions = {
        "profit_within_limit": actual_profit <= max_income if adjustments_confirmed else None,
        "employees_within_limit": Decimal(str(employees)) <= max_employees if employees is not None else None,
        "assets_within_limit": Decimal(str(assets)) <= max_assets if assets is not None else None,
        "industry_eligible": industry_eligible,
    }
    preferential = False if False in conditions.values() else (None if None in conditions.values() else True)
    missing = []
    if employees is None:
        missing.append("截至本期的从业人数季度平均值")
    if assets is None:
        missing.append("截至本期的资产总额季度平均值")
    if industry_eligible is None:
        missing.append("是否从事国家非限制和禁止行业")
    if not adjustments_confirmed:
        missing.append("预缴调整净额核对（无调整也须确认）")
    applicable = book is not None and book.entity_type == "company"
    supported = 2026 <= year <= 2027
    status = "not_applicable" if not applicable else ("policy_unverified" if not supported else ("pending" if missing else "estimated"))

    taxable_base = max(actual_profit, ZERO)
    effective_rate = included_rate * rate if preferential else Decimal("0.25")
    tax_total = (taxable_base * effective_rate).quantize(TWO, rounding=ROUND_HALF_UP) if status == "estimated" else None

    prepaid_prev = Decimal(str(prepaid_prev))
    prepaid = max(tax_total - prepaid_prev, ZERO) if tax_total is not None else None

    hints = ["本结果仅为普通居民企业查账征收预缴辅助，不代替申报；未覆盖全部减免和抵免项目。",
             "人数、资产按截至本期各季度平均值的平均数填写，每季平均值＝（季初＋季末）÷2。",
             "预缴调整净额按申报项目核对：调增为正、调减为负；不能直接使用税后净利润。"]
    if not applicable:
        hints.append("此账套不是公司企业，不能套用本企业所得税预估。")
    if not supported:
        hints.append("该年度优惠政策尚未核验，不输出税额。")
    if missing:
        hints.append("待核对：" + "、".join(missing))
    if tax_total is not None and tax_total < prepaid_prev:
        hints.append("累计已预缴超过本次估算，本期预缴暂计0；多缴处置需在申报时核对。")

    return {
        "year": year,
        "quarter": quarter,
        "period": f"{year}Q{quarter}",
        "profit_ytd": fmt_amount(profit_ytd),
        "net_profit_ytd": statement["ytd_net_profit"],
        "adjustment_net": fmt_amount(Decimal(str(adjustment_net))),
        "actual_profit": fmt_amount(actual_profit) if adjustments_confirmed else None,
        "taxable_base": fmt_amount(taxable_base) if adjustments_confirmed else None,
        "status": status, "missing_fields": missing,
        "preferential": preferential if applicable and supported else None,
        "actual_rate": (format(effective_rate * 100, ".2f").rstrip("0").rstrip(".") + "%") if status == "estimated" else None,
        "tax_total_ytd": fmt_amount(tax_total) if tax_total is not None else None,
        "prepaid_prev": fmt_amount(prepaid_prev),
        "prepaid_this": fmt_amount(prepaid) if prepaid is not None else None,
        "conditions": conditions,
        "hints": hints,
    }
