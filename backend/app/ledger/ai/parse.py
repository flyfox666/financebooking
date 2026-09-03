"""五层单据解析管道：二维码 → XML/OFD → PDF 文字层 → VLM 视觉补全 → 人工确认。

融合规则：按层优先级取值（先到先得）；关键金额字段跨层不一致时生成冲突警告；
关键字段缺失时自动调用视觉模型补全。整个模块不依赖 FastAPI。
"""

import base64
import json
import re
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

import pdfplumber

from app.ledger.exceptions import LLMError, VoucherError
from app.ledger.llm import gateway

TWO = Decimal("0.01")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".heic", ".heif"}
# HEIC/HEIF 是 iPhone 拍照默认格式：装了 pillow-heif 就注册解码器，没装则退化为仅 VLM 识别
try:
    from pillow_heif import register_heif_opener as _register_heif_opener

    _register_heif_opener()
except ImportError:
    pass
IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".bmp": "image/bmp", ".heic": "image/heic", ".heif": "image/heic",
}
FIELD_KEYS = [
    "invoice_type", "invoice_no", "invoice_code", "invoice_date",
    "seller_name", "seller_tax_no", "buyer_name", "buyer_tax_no",
    "goods_name", "amount_excl", "tax_rate", "tax_amount", "amount_total", "status",
]
VLM_PROMPT = (
    "你是发票识别引擎。从图片中提取发票字段，只输出一个 JSON 对象："
    '{"invoice_type":"special|general","invoice_no":"","invoice_code":"","invoice_date":"YYYY-MM-DD",'
    '"seller_name":"","seller_tax_no":"","buyer_name":"","buyer_tax_no":"","goods_name":"",'
    '"amount_excl":"0.00","tax_rate":"0.01","tax_amount":"0.00","amount_total":"0.00","status":"normal|red"}。'
    "金额为两位小数的字符串；数电票发票号码为20位数字；缺失字段填空字符串。"
)


def blank_fields() -> dict:
    return {key: "" for key in FIELD_KEYS}


def _dec(value) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").replace("¥", "").replace("￥", "").strip())
    except (InvalidOperation, AttributeError, ValueError):
        return None


def _norm_date(text: str) -> str:
    if not text:
        return ""
    t = str(text).strip().replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if re.fullmatch(r"\d{8}", t):
        day, month, year = t[:2], t[2:4], t[4:8]
        try:
            date(int(year), int(month), int(day))
            return f"{year}-{month}-{day}"
        except ValueError:
            pass
        year, month, day = t[:4], t[4:6], t[6:8]
        try:
            date(int(year), int(month), int(day))
            return f"{year}-{month}-{day}"
        except ValueError:
            pass
    return ""


def parse_invoice_qr_payload(payload: str) -> dict:
    fields = blank_fields()
    parts = [p.strip() for p in payload.split(",")]
    code_taken = number_taken = amount_taken = date_taken = False
    for part in parts:
        digits = re.sub(r"\D", "", part)
        if len(digits) == 20 and not number_taken:
            fields["invoice_no"] = digits
            fields["invoice_type"] = "digital"
            number_taken = True
            continue
        if re.fullmatch(r"\d{10,12}", digits) and not code_taken and not number_taken:
            fields["invoice_code"] = digits
            code_taken = True
            continue
        if re.fullmatch(r"\d{8}", digits) and not number_taken and code_taken:
            fields["invoice_no"] = digits
            number_taken = True
            continue
        if re.fullmatch(r"\d{8}", digits) and not date_taken:
            normalized = _norm_date(digits)
            if normalized:
                fields["invoice_date"] = normalized
                date_taken = True
                continue
        if not amount_taken:
            value = _dec(part)
            if value is not None and Decimal("0") < value < Decimal("10000000") and "." in part:
                fields["amount_total"] = format(value, "f")
                amount_taken = True
    fields["status"] = "normal"
    return fields


def decode_qr_fields(content: bytes) -> dict:
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode
    except (ImportError, OSError):
        return blank_fields()
    try:
        image = Image.open(BytesIO(content))
        found = decode(image)
    except Exception:
        return blank_fields()
    if not found:
        return blank_fields()
    payload = found[0].data.decode("utf-8", "ignore")
    return parse_invoice_qr_payload(payload)


_XML_TAG_SLOTS = {
    "invoiceno": "invoice_no",
    "invoicecode": "invoice_code",
    "invoicedate": "invoice_date",
    "issuetime": "invoice_date",
    "totalamount": "amount_total",
    "totaltaxamount": "tax_amount",
    "totaltaxamt": "tax_amount",
    "taxamount": "tax_amount",
    "sellername": "seller_name",
    "sellerid": "seller_tax_no",
    "sellertaxid": "seller_tax_no",
    "buyername": "buyer_name",
    "buyerid": "buyer_tax_no",
    "buyertaxid": "buyer_tax_no",
    "itemname": "goods_name",
    "goodsname": "goods_name",
    "taxrate": "tax_rate",
}


def _walk_xml(elem, slots: dict) -> None:
    name = elem.tag.split("}")[-1].lower()
    if name in slots and not slots[name] and (elem.text or "").strip():
        slots[name] = (elem.text or "").strip()
    for child in elem:
        _walk_xml(child, slots)


def parse_xml_fields(content: bytes) -> dict:
    fields = blank_fields()
    text = content.decode("utf-8", "ignore").lstrip()
    if not text.startswith("<"):
        return fields
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return fields
    slots = {key: "" for key in _XML_TAG_SLOTS}
    _walk_xml(root, slots)
    for xml_tag, field_key in _XML_TAG_SLOTS.items():
        value = slots.get(xml_tag, "")
        if not value:
            continue
        if field_key == "invoice_date":
            fields[field_key] = _norm_date(value) or value
        elif field_key in ("amount_total", "tax_amount", "amount_excl"):
            number = _dec(value)
            fields[field_key] = format(number, "f") if number is not None else value
        else:
            fields[field_key] = value
    if fields["invoice_no"]:
        fields.setdefault("invoice_type", "digital")
        if len(fields["invoice_no"]) == 20:
            fields["invoice_type"] = "digital"
    fields.setdefault("status", "normal")
    return fields


def parse_ofd_fields(content: bytes) -> dict:
    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile:
        return blank_fields()
    merged = blank_fields()
    for name in archive.namelist():
        if not name.lower().endswith(".xml"):
            continue
        inner = archive.read(name)
        if b"original_invoice" in inner.lower() or b"invoice" in inner.lower():
            fields = parse_xml_fields(inner)
            for key in FIELD_KEYS:
                if not merged[key] and fields.get(key):
                    merged[key] = fields[key]
    return merged


def extract_pdf_text(content: bytes) -> tuple[str, bool]:
    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            pages = [(page.extract_text() or "") for page in pdf.pages]
    except Exception:
        return "", False
    full = "\n".join(pages)
    return full, bool(full.strip())


def parse_pdf_text_fields(text: str) -> dict:
    fields = blank_fields()

    m = re.search(r"发票号码\s*[：:]?\s*(\d{20})", text)
    if m:
        fields["invoice_no"] = m.group(1)
        fields["invoice_type"] = "digital"
    if not fields["invoice_no"]:
        m = re.search(r"发票号码\s*[：:]?\s*(\d{8})", text)
        if m:
            fields["invoice_no"] = m.group(1)

    m = re.search(r"开票日期\s*[：:]?\s*(\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2})", text)
    if m:
        fields["invoice_date"] = _norm_date(m.group(1))

    m = re.search(r"[（(]小写[）)]\s*[¥￥]?\s*([0-9,]+\.\d{2})", text)
    if m:
        value = _dec(m.group(1))
        if value is not None:
            fields["amount_total"] = format(value, "f")

    names = re.findall(r"名\s*称\s*[：:]\s*([^\n\r]{2,60}?)\s*(?:统一社会信用代码|纳税人识别号|$)", text)
    if names:
        fields["buyer_name"] = names[0].strip()
        if len(names) > 1:
            fields["seller_name"] = names[1].strip()
    tax_nos = re.findall(r"(?:统一社会信用代码|纳税人识别号)\s*[：:/]*\s*([0-9A-Z]{15,20})", text)
    if tax_nos:
        fields["buyer_tax_no"] = tax_nos[0]
        if len(tax_nos) > 1:
            fields["seller_tax_no"] = tax_nos[1]

    m = re.search(r"\*[^*\n]+\*([^\n]+)", text)
    if m:
        fields["goods_name"] = m.group(0).strip()

    if "红" in text:
        fields["status"] = "red"
    else:
        fields["status"] = "normal"
    return fields


def _render_pdf_first_page_png(content: bytes) -> bytes:
    try:
        import pymupdf

        document = pymupdf.open(stream=content, filetype="pdf")
        try:
            page = document[0]
            pixmap = page.get_pixmap(dpi=150)
            return pixmap.tobytes("png")
        finally:
            document.close()
    except Exception:
        return b""


def _to_data_url(content: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(content).decode()


def vlm_fields(db, image_bytes: bytes, mime: str) -> dict:
    result = gateway.chat_vision(
        db,
        text=VLM_PROMPT,
        image_data_url=_to_data_url(image_bytes, mime),
        json_mode=True,
    )
    try:
        data = json.loads(result["content"])
    except json.JSONDecodeError:
        raise LLMError("视觉模型输出不是合法 JSON：" + result["content"][:200])
    fields = blank_fields()
    for key in FIELD_KEYS:
        value = str(data.get(key, "") or "").strip()
        if not value:
            continue
        if key == "invoice_date":
            fields[key] = _norm_date(value) or value
        elif key in ("amount_total", "tax_amount", "amount_excl"):
            number = _dec(value)
            fields[key] = format(number, "f") if number is not None else value
        else:
            fields[key] = value
    return fields


def merge_fields(*layers: tuple[str, dict]) -> tuple[dict, list[str], list[str]]:
    merged = blank_fields()
    warnings: list[str] = []
    used: list[str] = []
    for layer_name, fields in layers:
        contributed = False
        for key in FIELD_KEYS:
            value = (fields.get(key) or "").strip()
            if not value:
                continue
            if key in ("amount_total", "amount_excl", "tax_amount") and merged[key]:
                old, new = _dec(merged[key]), _dec(value)
                if old is not None and new is not None and abs(old - new) > TWO:
                    warnings.append(
                        f"字段 {key} 跨层不一致：{merged[key]}（{used[-1] if used else layer_name}）vs {value}（{layer_name}），以高层为准"
                    )
                continue
            if not merged[key]:
                merged[key] = value
                contributed = True
        if contributed or fields.get("invoice_no"):
            used.append(layer_name)
    return merged, warnings, used


def route_and_parse(
    db,
    *,
    filename: str,
    content: bytes,
    allow_vlm: bool = True,
) -> dict:
    suffix = Path(filename or "").suffix.lower()
    layers: list[tuple[str, dict]] = []
    doc_type = "invoice"
    source_kind = ""

    if suffix == ".xml" or (not suffix and content.lstrip()[:5] == b"<?xml"):
        source_kind = "xml"
        layers.append(("xml", parse_xml_fields(content)))
    elif suffix in (".ofd", ".zip"):
        source_kind = "ofd"
        layers.append(("ofd_xml", parse_ofd_fields(content)))
    elif suffix == ".pdf":
        text, has_text_layer = extract_pdf_text(content)
        if has_text_layer:
            source_kind = "pdf_text"
            layers.append(("pdf_text", parse_pdf_text_fields(text)))
        else:
            source_kind = "pdf_image"
            if allow_vlm:
                png = _render_pdf_first_page_png(content)
                if png:
                    layers.append(("vlm", vlm_fields(db, png, "image/png")))
                else:
                    layers.append(("vlm", vlm_fields(db, content, "application/pdf")))
    elif suffix in IMAGE_EXTS:
        source_kind = "image"
        qr = decode_qr_fields(content)
        if any(value for value in qr.values()):
            layers.append(("qr", qr))
        if allow_vlm:
            layers.append(("vlm", vlm_fields(db, content, IMAGE_MIME.get(suffix, "image/jpeg"))))
    else:
        text = content.decode("utf-8", "ignore").strip()
        if note := text:
            return {
                "doc_type": "text",
                "source_kind": "text",
                "fields": {"note": note},
                "warnings": [],
                "layers": ["text"],
            }
        raise VoucherError("无法识别的文件类型")

    if doc_type == "invoice" and allow_vlm and source_kind in ("pdf_text", "xml", "ofd"):
        merged_probe, _, _ = merge_fields(*layers)
        if not merged_probe["amount_total"] or not merged_probe["invoice_no"]:
            if source_kind == "pdf_text":
                png = _render_pdf_first_page_png(content)
                if png:
                    layers.append(("vlm", vlm_fields(db, png, "image/png")))
                else:
                    layers.append(("vlm", vlm_fields(db, content, "application/pdf")))
            elif source_kind in ("xml", "ofd"):
                raise VoucherError("结构化文件中缺少关键字段（发票号码/金额），请核对文件或改用图片版式")

    fields, warnings, used = merge_fields(*layers)
    if doc_type == "invoice":
        missing = [key for key in ("invoice_no", "amount_total") if not fields[key]]
        if missing:
            warnings.append("关键字段缺失：" + "、".join(missing) + "，请人工确认后入账")
    return {
        "doc_type": doc_type,
        "source_kind": source_kind,
        "fields": fields,
        "warnings": warnings,
        "layers": used or [layer_name for layer_name, _ in layers],
    }
