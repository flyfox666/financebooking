from io import BytesIO

from openpyxl import Workbook

HEADERS = [
    "序号", "发票种类", "发票代码", "发票号码", "开票日期",
    "购买方名称", "购买方税号", "销售方名称", "销售方税号",
    "货物或应税劳务、服务名称", "金额", "税率", "税额", "价税合计", "发票状态",
]


def _row(no_suffix, date_str, total, inv_type, status):
    return [
        None, inv_type, "", f"2531700000012345{no_suffix}", date_str,
        "上海某某智能科技有限公司", "91310000MA1K35X00A", "上海客户端有限公司", "91310000TEST00000X",
        "*信息技术服务*平台开发服务", "", "1%", "", total, status,
    ]


def build_sales_xlsx(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["国家税务总局全国增值税发票查验平台导出"])
    ws.append([])
    ws.append(HEADERS)
    for row in rows:
        ws.append(row)
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def quarter3_under_threshold() -> bytes:
    rows = [
        _row("6701", "2026-07-05", 50000, "普通发票", "正常"),
        _row("6702", "2026-07-20", 50000, "普通发票", "正常"),
        _row("6703", "2026-08-10", 50000, "普通发票", "正常"),
        _row("6704", "2026-09-01", 50000, "普通发票", "正常"),
        _row("6705", "2026-08-15", 50000, "专用发票", "正常"),
        _row("6706", "2026-09-20", 10000, "普通发票", "红冲"),
    ]
    return build_sales_xlsx(rows)


def quarter3_over_threshold() -> bytes:
    rows = [
        _row("6801", "2026-07-05", 360000, "普通发票", "正常"),
    ]
    return build_sales_xlsx(rows)


def purchase_file() -> bytes:
    rows = [
        _row("6901", "2026-08-05", 3200, "普通发票", "正常"),
    ]
    return build_sales_xlsx(rows)
