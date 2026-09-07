import json
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.ledger.balances import (
    ZERO,
    fmt_amount,
    gross_sums,
    leaf_accounts,
    next_period,
    opening_nets,
    trial_balance,
    _with_parent_rollups_gross,
    _with_parent_rollups_nets,
)
from app.models.book import Book
from app.models.report import ReportTemplate


def _load_template(db: Session, book_id: int, report: str) -> list[dict]:
    rows = db.scalars(
        select(ReportTemplate)
        .where(ReportTemplate.book_id == book_id, ReportTemplate.report == report)
        .order_by(ReportTemplate.row_no)
    ).all()
    if not rows:
        from app.ledger.exceptions import BookError

        raise BookError("报表模板未初始化")
    return [
        {
            "key": row.key,
            "row_no": row.row_no,
            "name": row.name,
            "kind": row.kind,
            "side": row.side,
            "formula": json.loads(row.formula) if row.formula else [],
            "in_total": row.in_total,
        }
        for row in rows
    ]


def balance_sheet(db: Session, *, book_id: int, period: str) -> dict:
    book = db.get(Book, book_id)
    year = period[:4]
    template = _load_template(db, book_id, "bs")

    closing_nets = _with_parent_rollups_nets(
        db, book_id, opening_nets(db, book_id, before_period=next_period(period))
    )
    year_begin_nets = _with_parent_rollups_nets(
        db, book_id, opening_nets(db, book_id, before_period=f"{year}-01")
    )

    def evaluate(nets: dict[str, Decimal]) -> dict[str, Decimal]:
        values: dict[str, Decimal] = {}
        for row in template:
            if row["kind"] == "line":
                total = ZERO
                for code, sign in row["formula"]:
                    net = nets.get(code, ZERO)
                    signed = net if row["side"] == "debit" else -net
                    total += Decimal(sign) * signed
                values[row["key"]] = total
            else:
                total = ZERO
                for term in row["formula"]:
                    value = values[term[1:]]
                    total += value if term[0] == "+" else -value
                values[row["key"]] = total
        return values

    closing_values = evaluate(closing_nets)
    year_begin_values = evaluate(year_begin_nets)

    rows = [
        {
            "key": row["key"],
            "row_no": row["row_no"],
            "name": row["name"],
            "closing": fmt_amount(closing_values[row["key"]]),
            "year_begin": fmt_amount(year_begin_values[row["key"]]),
            "bold": row["kind"] == "calc",
        }
        for row in template
    ]
    return {
        "book_id": book_id,
        "book_name": book.name if book else "",
        "period": period,
        "rows": rows,
        "total_assets": fmt_amount(closing_values["total_assets"]),
        "total_liabilities_and_equity": fmt_amount(closing_values["total_liabilities_and_equity"]),
        "year_begin_total_assets": fmt_amount(year_begin_values["total_assets"]),
        "year_begin_total_liabilities_and_equity": fmt_amount(
            year_begin_values["total_liabilities_and_equity"]
        ),
    }


def income_statement(db: Session, *, book_id: int, period: str) -> dict:
    book = db.get(Book, book_id)
    year = period[:4]
    template = _load_template(db, book_id, "is")

    month_sums = _with_parent_rollups_gross(
        db, book_id, gross_sums(db, book_id, period, period, exclude_pnl_carryover=True)
    )
    ytd_sums = _with_parent_rollups_gross(
        db,
        book_id,
        gross_sums(db, book_id, f"{year}-01", period, exclude_pnl_carryover=True),
    )

    def evaluate(sums: dict[str, list[Decimal]]) -> dict[str, Decimal]:
        values: dict[str, Decimal] = {}
        for row in template:
            if row["kind"] == "line":
                total = ZERO
                for code, sign in row["formula"]:
                    debit, credit = sums.get(code, [ZERO, ZERO])
                    directional = (credit - debit) if row["side"] == "credit" else (debit - credit)
                    total += Decimal(sign) * directional
                values[row["key"]] = total
            else:
                total = ZERO
                for term in row["formula"]:
                    value = values[term[1:]]
                    total += value if term[0] == "+" else -value
                values[row["key"]] = total
        return values

    month_values = evaluate(month_sums)
    ytd_values = evaluate(ytd_sums)

    rows = [
        {
            "key": row["key"],
            "row_no": row["row_no"],
            "name": row["name"],
            "month": fmt_amount(month_values[row["key"]]),
            "year_to_date": fmt_amount(ytd_values[row["key"]]),
            "bold": row["kind"] == "calc",
        }
        for row in template
    ]
    return {
        "book_id": book_id,
        "book_name": book.name if book else "",
        "period": period,
        "year": year,
        "rows": rows,
        "month_net_profit": fmt_amount(month_values["net_profit"]),
        "ytd_net_profit": fmt_amount(ytd_values["net_profit"]),
    }


def period_summary(db: Session, *, book_id: int, period: str) -> dict:
    """本期概要：一次聚合 KPI 与凭证状态计数，供首页右栏使用；无数据的科目固定返回 0.00。"""
    from app.models.voucher import Voucher

    bs = balance_sheet(db, book_id=book_id, period=period)
    inc = income_statement(db, book_id=book_id, period=period)

    def pick(rows: list[dict], key: str, field: str) -> str | None:
        row = next((r for r in rows if r["key"] == key), None)
        return row[field] if row else None

    status_counts = dict(
        db.execute(
            select(Voucher.status, func.count(Voucher.id))
            .where(Voucher.book_id == book_id, Voucher.period == period)
            .group_by(Voucher.status)
        ).all()
    )

    result = {
        "book_id": book_id,
        "period": period,
        "voucher_status": {
            "draft": int(status_counts.get("draft", 0)),
            "submitted": int(status_counts.get("submitted", 0)),
            "audited": int(status_counts.get("audited", 0)),
            "posted": int(status_counts.get("posted", 0)),
        },
        "operating_revenue": pick(inc["rows"], "operating_revenue", "month"),
        "operating_cost": pick(inc["rows"], "operating_cost", "month"),
        "net_profit": inc["month_net_profit"],
        "monetary_funds": pick(bs["rows"], "monetary_funds", "closing"),
        "accounts_receivable": pick(bs["rows"], "accounts_receivable", "closing"),
        "accounts_payable": pick(bs["rows"], "accounts_payable", "closing"),
        "total_assets": bs["total_assets"],
        "undistributed_profits": pick(bs["rows"], "undistributed_profits", "closing"),
    }
    result.update(_summary_expansion(db, book_id=book_id, period=period, income=inc, balance=bs))
    return result


PROFIT_LABELS = {
    "operating_revenue": "营业收入", "operating_cost": "营业成本",
    "gross_profit": "毛利", "period_expenses": "期间费用", "net_profit": "净利润",
    "selling_expenses": "销售费用", "administrative_expenses": "管理费用",
    "financial_expenses": "财务费用", "business_taxes": "税金及附加",
    "investment_income": "投资收益", "operating_profit": "营业利润",
    "nonoperating_income": "营业外收入", "nonoperating_expenses": "营业外支出",
    "total_profit": "利润总额", "income_tax_expense": "所得税费用",
}
DERIVED_METRICS = {
    "gross_profit": [("operating_revenue", 1), ("operating_cost", -1)],
    "period_expenses": [("selling_expenses", 1), ("administrative_expenses", 1), ("financial_expenses", 1)],
}
FUND_LABELS = {"monetary_funds": "账面资金余额", "accounts_receivable": "应收账款",
               "accounts_payable": "应付账款", "prepayments": "预付账款",
               "advances_from_customers": "预收账款", "other_receivables": "其他应收款",
               "other_payables": "其他应付款"}


def _metric_terms(template: list[dict], key: str, visiting=()) -> dict[str, Decimal]:
    """展开报表公式供下钻与有效性检查使用；缺项目/循环不能视为零。"""
    from app.ledger.exceptions import BookError
    if key in visiting:
        raise BookError("报表公式存在循环引用，请检查映射")
    rows = {row["key"]: row for row in template}
    if key in DERIVED_METRICS:
        children = DERIVED_METRICS[key]
    elif key not in rows:
        raise BookError(f"报表缺少项目 {key}，请检查映射")
    elif rows[key]["kind"] == "line":
        direction = -1 if rows[key]["side"] == "credit" else 1
        terms = {}
        for code, sign in rows[key]["formula"]:
            terms[code] = terms.get(code, ZERO) + Decimal(str(sign)) * direction
        return terms
    else:
        children = [(term[1:], 1 if term[0] == "+" else -1) for term in rows[key]["formula"]]
    terms = {}
    for child, sign in children:
        for code, weight in _metric_terms(template, child, (*visiting, key)).items():
            terms[code] = terms.get(code, ZERO) + weight * sign
    return {code: weight for code, weight in terms.items() if weight}


def _summary_expansion(db, *, book_id, period, income, balance):
    from app.models.account import Account
    from app.models.voucher import Voucher
    from app.ledger.cashflow import CASH_ACCOUNTS, _flows
    from app.ledger.exceptions import BookError

    book = db.get(Book, book_id)
    if book is None:
        raise BookError("账套不存在")
    year, month = map(int, period.split("-"))
    previous = f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"
    prior = income_statement(db, book_id=book_id, period=previous) if previous >= book.start_period else None
    template = _load_template(db, book_id, "is")
    accounts = set(db.scalars(select(Account.code).where(Account.book_id == book_id)))
    warnings = []

    def amount(report, key, field):
        if report is None:
            return None
        if key in DERIVED_METRICS:
            values = [(amount(report, child, field), sign) for child, sign in DERIVED_METRICS[key]]
            return sum((value * sign for value, sign in values), ZERO) if all(value is not None for value, _ in values) else None
        row = next((row for row in report["rows"] if row["key"] == key), None)
        return Decimal(row[field]) if row else None

    metrics = []
    for key, label in PROFIT_LABELS.items():
        try:
            terms = _metric_terms(template, key)
            valid = not (set(terms) - accounts)
        except BookError:
            valid = False
        values = {field: amount(income, key, field) if valid else None for field in ("month", "year_to_date")}
        prior_value = amount(prior, key, "month") if valid else None
        if any(value is None for value in values.values()):
            warnings.append(f"{label}映射待检查")
        metrics.append({"key": key, "label": label,
                        **{field: fmt_amount(value) if value is not None else None for field, value in values.items()},
                        "previous_month": fmt_amount(prior_value) if prior_value is not None else None,
                        "month_change": fmt_amount(values["month"] - prior_value) if values["month"] is not None and prior_value is not None else None})
    by_key = {row["key"]: row for row in metrics}
    margins = {}
    for field in ("month", "year_to_date"):
        revenue, gross = by_key["operating_revenue"][field], by_key["gross_profit"][field]
        margins[field] = fmt_amount(Decimal(gross) / Decimal(revenue) * 100) if revenue is not None and gross is not None and Decimal(revenue) > 0 else None

    closing = opening_nets(db, book_id, next_period(period))
    def cash(nets, code=None):
        return sum((value for account, value in nets.items() if account[:4] in CASH_ACCOUNTS and (code is None or account[:4] == code)), ZERO)
    funds = {"closing": fmt_amount(cash(closing)), "components": [
        {"code": code, "label": label, "value": fmt_amount(cash(closing, code))}
        for code, label in (("1001", "库存现金"), ("1002", "银行存款"), ("1012", "其他货币资金"))]}
    for field, start in (("month", period), ("year_to_date", f"{year}-01")):
        flows = _flows(db, book_id, start, period)
        funds[field] = {"opening": fmt_amount(cash(opening_nets(db, book_id, start))),
                        "change": fmt_amount(cash(closing) - cash(opening_nets(db, book_id, start))),
                        "operating_net": fmt_amount(sum((flows.get(key, ZERO) for key in ("sales", "other_in", "purchase", "staff", "taxes", "other_out")), ZERO))}
    unposted = int(db.scalar(select(func.count(Voucher.id)).where(
        Voucher.book_id == book_id, Voucher.period >= f"{year}-01", Voucher.period <= period,
        Voucher.status.in_(("draft", "submitted", "audited")))) or 0)
    if unposted:
        warnings.append(f"本年截至本期还有 {unposted} 张未过账凭证，尚未计入经营与资金数据")
    b_rows = {row["key"]: row for row in balance["rows"]}
    b_template = _load_template(db, book_id, "bs")
    working_capital = []
    for key, label in FUND_LABELS.items():
        if key == "monetary_funds":
            continue
        try:
            valid = key in b_rows and not (set(_metric_terms(b_template, key)) - accounts)
        except BookError:
            valid = False
        working_capital.append({"key": key, "label": label, "value": b_rows[key]["closing"] if valid else None})
        if not valid:
            warnings.append(f"{label}映射待检查")
    return {"profit_metrics": metrics, "gross_margin": margins, "funds": funds,
            "working_capital": working_capital, "warnings": warnings,
            "unposted_ytd": unposted, "previous_period": previous,
            "accounting_standard": book.accounting_standard, "basis": "已过账（含已冲销原凭证及红冲分录）",
            "completeness_note": "请核对工资、折旧、摊销和所得税等是否已完整计提；未过账数据不计入。"}


def summary_detail(db, *, book_id, period, key, scope="month", offset=0, limit=50):
    from app.models.voucher import Voucher, VoucherLine
    from app.models.contact import Contact
    from app.ledger.exceptions import BookError
    report, metric = key.split(":", 1) if ":" in key else ("", "")
    if report not in ("is", "bs") or metric not in (PROFIT_LABELS if report == "is" else FUND_LABELS):
        raise BookError("未知概要项目")
    terms = _metric_terms(_load_template(db, book_id, report), metric)
    start = period if scope == "month" else period[:4] + "-01"
    # 余额下钻覆盖建账后的累计发生额；期初余额单独展示，不冒充逐笔往来。
    if report == "bs":
        start = db.get(Book, book_id).start_period
    filters = [Voucher.book_id == book_id, Voucher.period >= start, Voucher.period <= period,
               Voucher.status.in_(("posted", "voided")),
               or_(*(or_(VoucherLine.account_code == code, VoucherLine.account_code.startswith(code + ".")) for code in terms)) if terms else False]
    if report == "is":
        filters.append(~and_(Voucher.source == "carryover", Voucher.carryover_type == "pnl"))
    query = select(Voucher, VoucherLine, Contact.name).join(VoucherLine, VoucherLine.voucher_id == Voucher.id).outerjoin(
        Contact, and_(Contact.id == VoucherLine.contact_id, Contact.book_id == book_id)).where(*filters)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(Voucher.voucher_date.desc(), Voucher.id.desc(), VoucherLine.line_no).offset(offset).limit(limit)).all()
    seed = _with_parent_rollups_nets(db, book_id, opening_nets(db, book_id, start))
    return {"key": key, "label": (PROFIT_LABELS if report == "is" else FUND_LABELS)[metric],
            "period_from": start, "period_to": period, "total": total, "offset": offset, "limit": limit,
            "opening": fmt_amount(sum((seed.get(code, ZERO) * weight for code, weight in terms.items()), ZERO)) if report == "bs" else None,
            "note": "余额含期初；期初尚未按往来对象分配，以下为已过账发生额，不用于判断逾期。" if report == "bs" else "取数沿用利润表映射，已排除损益结转。",
            "rows": [{"voucher_id": voucher.id, "voucher_no": voucher.voucher_no,
                      "date": str(voucher.voucher_date), "summary": line.summary,
                      "account_code": line.account_code, "contact_name": name,
                      "debit": fmt_amount(line.debit), "credit": fmt_amount(line.credit)} for voucher, line, name in rows]}


def template_check(db: Session, *, book_id: int, period: str) -> dict:
    """映射体检：报表行→科目映射全景、未映射科目清单、公式脏引用、资产负债恒等式校验。"""
    from app.models.account import Account

    templates = {report: _load_template(db, book_id, report) for report in ("bs", "is")}
    all_accounts = {
        a.code: a
        for a in db.scalars(select(Account).where(Account.book_id == book_id)).all()
    }

    # 行视角：每张报表各行引用的科目（带符号与科目名，科目不存在则 name 为 None）
    covered: dict[str, set[str]] = {}
    rows_out: dict[str, list] = {}
    for report, rows in templates.items():
        covered[report] = set()
        out = []
        for row in rows:
            codes = []
            if row["kind"] == "line":
                for code, sign in row["formula"]:
                    covered[report].add(code)
                    acct = all_accounts.get(code)
                    codes.append(
                        {
                            "code": code,
                            "sign": "+" if Decimal(sign) > 0 else "-",
                            "name": acct.name if acct else None,
                        }
                    )
            out.append({"key": row["key"], "name": row["name"], "kind": row["kind"], "codes": codes})
        rows_out[report] = out

    # 科目视角：叶子科目是否被任一报表覆盖、期末是否有余额
    tb_rows = {
        r["account_code"]: r
        for r in trial_balance(db, book_id=book_id, period=period)["rows"]
    }
    accounts_out = []
    for code in sorted(tb_rows):
        r = tb_rows[code]
        # 覆盖判定与报表取数口径一致：明细科目（如 5602.01）经父级卷汇总计入公式科目（5602），
        # 因此公式覆盖其任一上层科目即视为已映射
        mapped_reports = [
            rep for rep in ("bs", "is")
            if any(code == f or code.startswith(f + ".") for f in covered[rep])
        ]
        closing_debit = Decimal(r["closing_debit"])
        closing_credit = Decimal(r["closing_credit"])
        accounts_out.append(
            {
                "code": code,
                "name": r["account_name"],
                "is_active": r["is_active"],
                "has_balance": closing_debit != ZERO or closing_credit != ZERO,
                "closing": fmt_amount(closing_debit if closing_debit != ZERO else closing_credit),
                "mapped": bool(mapped_reports),
                "reports": mapped_reports,
            }
        )

    # 公式引用了账套里不存在的科目（脏映射，通常是科目被删或模板来自其他准则）
    dirty_refs = [
        {"report": report, "code": code}
        for report in ("bs", "is")
        for code in sorted(covered[report] - set(all_accounts))
    ]

    bs = balance_sheet(db, book_id=book_id, period=period)
    return {
        "book_id": book_id,
        "period": period,
        "rows": rows_out,
        "accounts": accounts_out,
        "unmapped": [a for a in accounts_out if not a["mapped"]],
        "dirty_refs": dirty_refs,
        "bs_identity_ok": bs["total_assets"] == bs["total_liabilities_and_equity"],
        "total_assets": bs["total_assets"],
        "total_liabilities_and_equity": bs["total_liabilities_and_equity"],
    }


def update_template_row(db: Session, *, book_id: int, report: str, key: str, formula: list) -> dict:
    """编辑指定报表行的取数科目映射（仅 line 行；calc 行由行间引用计算，不可直接编辑）。"""
    from app.ledger.exceptions import BookError
    from app.models.account import Account

    if report not in ("bs", "is"):
        raise BookError("仅支持编辑 bs（资产负债表）或 is（利润表）模板")
    row = db.scalars(
        select(ReportTemplate).where(
            ReportTemplate.book_id == book_id,
            ReportTemplate.report == report,
            ReportTemplate.key == key,
        )
    ).first()
    if not row:
        raise BookError("报表行不存在")
    if row.kind != "line":
        raise BookError("计算行的数值由其他行汇总而来，不能直接编辑科目映射")

    valid_codes = set(
        db.scalars(select(Account.code).where(Account.book_id == book_id)).all()
    )
    cleaned = []
    for item in formula or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise BookError("公式项格式应为 [科目编码, 符号]")
        code, sign = item
        if code not in valid_codes:
            raise BookError(f"科目 {code} 不存在于当前账套")
        if str(sign) not in ("1", "-1"):
            raise BookError("符号只能是 1 或 -1")
        cleaned.append([code, int(sign)])
    row.formula = json.dumps(cleaned, ensure_ascii=False)
    db.commit()
    return {"ok": True, "report": report, "key": key, "formula": cleaned}


def reset_template(db: Session, *, book_id: int, report: str) -> int:
    """删除指定报表的模板行并用系统默认模板重新播种，返回新模板行数。"""
    import json

    from sqlalchemy import delete

    from app.ledger.exceptions import BookError
    from app.ledger.report_templates import BS_TEMPLATE, IS_TEMPLATE

    mapping = {"bs": BS_TEMPLATE, "is": IS_TEMPLATE}
    if report not in mapping:
        raise BookError("仅支持重置 bs（资产负债表）或 is（利润表）模板")
    db.execute(
        delete(ReportTemplate).where(
            ReportTemplate.book_id == book_id, ReportTemplate.report == report
        )
    )
    for row in mapping[report]:
        db.add(
            ReportTemplate(
                book_id=book_id,
                report=report,
                key=row["key"],
                row_no=row["row_no"],
                name=row["name"],
                kind=row["kind"],
                side=row["side"],
                formula=json.dumps(row["formula"], ensure_ascii=False),
                in_total=row.get("in_total", True),
            )
        )
    db.commit()
    return len(mapping[report])
