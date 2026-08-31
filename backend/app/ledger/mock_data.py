"""仅供开发与测试演示用的模拟业务数据（2026-08，上海小规模纳税人 AI 公司）。"""

from decimal import Decimal

from app.ledger import account_service

DETAIL_ACCOUNTS = [
    ("5602", "5602.01", "办公费"),
    ("5602", "5602.02", "差旅费"),
    ("5602", "5602.03", "业务招待费"),
    ("5602", "5602.04", "职工薪酬"),
    ("5602", "5602.05", "折旧费"),
    ("5602", "5602.06", "研究费用"),
    ("5602", "5602.07", "租赁费"),
    ("4301", "4301.01", "费用化支出"),
    ("4301", "4301.02", "资本化支出"),
]


def setup_detail_accounts(db, book_id: int) -> None:
    for parent_code, code, name in DETAIL_ACCOUNTS:
        account_service.create_detail_account(
            db, book_id=book_id, parent_code=parent_code, code=code, name=name
        )


def _line(summary: str, account_code: str, *, debit: str = "0", credit: str = "0") -> dict:
    return {"summary": summary, "account_code": account_code, "debit": debit, "credit": credit}


def voucher_payloads() -> list[dict]:
    return [
        {"voucher_date": "2026-08-03", "attachment_count": 1, "source": "manual",
         "lines": [_line("股东实缴出资款存入银行", "1002", debit="500000.00"),
                   _line("股东实缴出资", "3001", credit="500000.00")]},
        {"voucher_date": "2026-08-04", "attachment_count": 1, "source": "manual",
         "lines": [_line("购入办公电脑一台", "1601", debit="12000.00"),
                   _line("支付办公电脑款", "1002", credit="12000.00")]},
        {"voucher_date": "2026-08-04", "attachment_count": 1, "source": "manual",
         "lines": [_line("购入深度学习服务器", "1601", debit="8500.00"),
                   _line("支付服务器款", "1002", credit="8500.00")]},
        {"voucher_date": "2026-08-05", "attachment_count": 1, "source": "manual",
         "lines": [_line("支付7月云资源费用", "5401", debit="3200.00"),
                   _line("支付云资源费用", "1002", credit="3200.00")]},
        {"voucher_date": "2026-08-06", "attachment_count": 1, "source": "manual",
         "lines": [_line("开票应收技术服务费", "1122", debit="11300.00"),
                   _line("确认技术服务收入", "5001", credit="11188.12"),
                   _line("计提增值税(1%)", "2221", credit="111.88")]},
        {"voucher_date": "2026-08-10", "attachment_count": 1, "source": "manual",
         "lines": [_line("收回技术服务款", "1002", debit="11300.00"),
                   _line("收回应收账款", "1122", credit="11300.00")]},
        {"voucher_date": "2026-08-12", "attachment_count": 1, "source": "manual",
         "lines": [_line("开票应收数据平台开发费", "1122", debit="21200.00"),
                   _line("确认平台开发收入", "5001", credit="20990.10"),
                   _line("计提增值税(1%)", "2221", credit="209.90")]},
        {"voucher_date": "2026-08-05", "attachment_count": 1, "source": "manual",
         "lines": [_line("支付8月办公室租金", "5602.07", debit="8000.00"),
                   _line("支付办公室租金", "1002", credit="8000.00")]},
        {"voucher_date": "2026-08-31", "attachment_count": 1, "source": "manual",
         "lines": [_line("计提研发人员工资", "4301.01", debit="25000.00"),
                   _line("计提管理人员工资", "5602.04", debit="20000.00"),
                   _line("计提本月工资", "2211", credit="45000.00")]},
        {"voucher_date": "2026-08-10", "attachment_count": 1, "source": "manual",
         "lines": [_line("发放上月工资", "2211", debit="45000.00"),
                   _line("银行支付工资", "1002", credit="44550.00"),
                   _line("代扣个人所得税", "2221", credit="450.00")]},
        {"voucher_date": "2026-08-15", "attachment_count": 1, "source": "manual",
         "lines": [_line("缴纳研发人员社保(公司部分)", "4301.01", debit="5000.00"),
                   _line("缴纳管理人员社保(公司部分)", "5602.04", debit="4600.00"),
                   _line("代垫个人社保部分", "1221", debit="4200.00"),
                   _line("缴纳社保费用", "1002", credit="13800.00")]},
        {"voucher_date": "2026-08-18", "attachment_count": 1, "source": "manual",
         "lines": [_line("报销员工出差高铁费", "5602.02", debit="1350.00"),
                   _line("现金支付差旅报销", "1001", credit="1350.00")]},
        {"voucher_date": "2026-08-19", "attachment_count": 1, "source": "manual",
         "lines": [_line("招待客户餐费", "5602.03", debit="2000.00"),
                   _line("支付业务招待费", "1002", credit="2000.00")]},
        {"voucher_date": "2026-08-20", "attachment_count": 1, "source": "manual",
         "lines": [_line("购买办公用品", "5602.01", debit="600.00"),
                   _line("支付办公用品款", "1002", credit="600.00")]},
        {"voucher_date": "2026-08-25", "attachment_count": 1, "source": "manual",
         "lines": [_line("银行账户手续费", "5603", debit="120.00"),
                   _line("支付银行手续费", "1002", credit="120.00")]},
        {"voucher_date": "2026-08-22", "attachment_count": 1, "source": "manual",
         "lines": [_line("收到客户预付开发款", "1002", debit="50000.00"),
                   _line("客户预付款项", "2203", credit="50000.00")]},
        {"voucher_date": "2026-08-20", "attachment_count": 1, "source": "manual",
         "lines": [_line("暂估SaaS工具月费(发票未到)", "5602.01", debit="998.00"),
                   _line("暂估应付SaaS费用", "2202", credit="998.00")]},
        {"voucher_date": "2026-08-28", "attachment_count": 1, "source": "manual",
         "lines": [_line("红冲误开发票", "1122", debit="-5000.00"),
                   _line("冲减技术服务收入", "5001", credit="-4950.50"),
                   _line("冲减增值税(1%)", "2221", credit="-49.50")]},
        {"voucher_date": "2026-08-31", "attachment_count": 1, "source": "manual",
         "lines": [_line("计提设备折旧", "5602.05", debit="333.33"),
                   _line("计提本月折旧", "1602", credit="333.33")]},
        {"voucher_date": "2026-08-24", "attachment_count": 1, "source": "manual",
         "lines": [_line("购买设计素材版权", "5602.01", debit="1500.00"),
                   _line("支付素材版权费", "1002", credit="1500.00")]},
        {"voucher_date": "2026-08-21", "attachment_count": 1, "source": "manual",
         "lines": [_line("股东垫付软件订阅费", "5602.01", debit="660.00"),
                   _line("股东垫付款项", "2241", credit="660.00")]},
        {"voucher_date": "2026-08-26", "attachment_count": 1, "source": "manual",
         "lines": [_line("归还股东垫付款", "2241", debit="660.00"),
                   _line("银行支付股东垫款", "1002", credit="660.00")]},
        {"voucher_date": "2026-08-08", "attachment_count": 1, "source": "manual",
         "lines": [_line("提取现金备用", "1001", debit="2000.00"),
                   _line("银行提现", "1002", credit="2000.00")]},
    ]


def expected_posted_net() -> dict[str, Decimal]:
    return {
        "1001": Decimal("650.00"),
        "1002": Decimal("464370.00"),
        "1122": Decimal("16200.00"),
        "1221": Decimal("4200.00"),
        "1601": Decimal("20500.00"),
        "1602": Decimal("-333.33"),
        "2202": Decimal("-998.00"),
        "2203": Decimal("-50000.00"),
        "2221": Decimal("-722.28"),
        "3001": Decimal("-500000.00"),
        "5001": Decimal("-27227.72"),
        "5401": Decimal("3200.00"),
        "5602.01": Decimal("3758.00"),
        "5602.02": Decimal("1350.00"),
        "5602.03": Decimal("2000.00"),
        "5602.04": Decimal("24600.00"),
        "5602.05": Decimal("333.33"),
        "5602.07": Decimal("8000.00"),
        "5603": Decimal("120.00"),
        "4301.01": Decimal("30000.00"),
    }
