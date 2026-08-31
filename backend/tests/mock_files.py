from io import BytesIO

from fpdf import FPDF
from PIL import Image
import qrcode

CJK_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]


def xml_invoice_bytes() -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<EInvoice>"
        "<InvoiceNo>25317000000123456701</InvoiceNo>"
        "<IssueTime>2026-08-05 10:30:00</IssueTime>"
        "<BuyerInfo><BuyerName>上海某某智能科技有限公司</BuyerName>"
        "<BuyerId>91310000MA1K35X00A</BuyerId></BuyerInfo>"
        "<SellerInfo><SellerName>上海云服务商有限公司</SellerName>"
        "<SellerId>91310000SELLER000X</SellerId></SellerInfo>"
        "<Item><ItemName>*信息技术服务*云服务器租用</ItemName></Item>"
        "<TotalAmount>11300.00</TotalAmount>"
        "<TotalTaxAmount>112.88</TotalTaxAmount>"
        "</EInvoice>"
    ).encode("utf-8")


def _cjk_font():
    from pathlib import Path

    for candidate in CJK_FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def pdf_invoice_bytes() -> bytes:
    font = _cjk_font()
    pdf = FPDF()
    pdf.add_page()
    if font:
        pdf.add_font("cjk", "", font)
        pdf.set_font("cjk", size=11)
    else:
        pdf.set_font("helvetica", size=11)
    lines = [
        "电子发票（普通发票）",
        "发票号码：25317000000123456701",
        "开票日期：2026年08月05日",
        "购买方信息 名称：上海某某智能科技有限公司 统一社会信用代码/纳税人识别号：91310000MA1K35X00A",
        "销售方信息 名称：上海云服务商有限公司 统一社会信用代码/纳税人识别号：91310000SELLER000X",
        "*信息技术服务*云服务器租用 11188.12 1% 111.88",
        "价税合计（大写）壹万壹仟叁佰元整 （小写）¥11300.00",
    ]
    for line in lines:
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def qr_invoice_png(payload: str = "01,31,25317000000123456701,113.00,05082026,8475") -> bytes:
    image = qrcode.make(payload)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def plain_png_bytes() -> bytes:
    image = Image.new("RGB", (4, 4), (255, 255, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
