import json
from decimal import Decimal

from sqlalchemy import func, select
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

    def pick(rows: list[dict], key: str, field: str) -> str:
        row = next((r for r in rows if r["key"] == key), None)
        return row[field] if row else "0.00"

    status_counts = dict(
        db.execute(
            select(Voucher.status, func.count(Voucher.id))
            .where(Voucher.book_id == book_id, Voucher.period == period)
            .group_by(Voucher.status)
        ).all()
    )

    return {
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
