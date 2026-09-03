import json

import pytest

from app.ledger.ai.parse import (
    merge_fields,
    parse_invoice_qr_payload,
    parse_ofd_fields,
    parse_pdf_text_fields,
    parse_xml_fields,
    route_and_parse,
)
from tests.mock_files import pdf_invoice_bytes, plain_png_bytes, qr_invoice_png, xml_invoice_bytes


def _patch_gateway(monkeypatch, **field_overrides):
    """mock 最外层 gateway.chat_vision（而非 vlm_fields），让真实 vlm_fields→_to_data_url 链路被执行。

    背景：曾因 parse.py 漏 import base64 导致线上 500，而 mock vlm_fields 的用例全绿——
    教训：只 mock IO 层，业务函数本身必须跑真代码。
    """

    def fake_chat_vision(db, *, text, image_data_url, json_mode, **kwargs):
        assert image_data_url.startswith("data:"), "真实 _to_data_url 应产出 data URL"
        payload = {key: "" for key in (
            "invoice_type", "invoice_no", "invoice_code", "invoice_date",
            "seller_name", "seller_tax_no", "buyer_name", "buyer_tax_no",
            "goods_name", "amount_excl", "tax_rate", "tax_amount", "amount_total", "status",
        )}
        payload.update(field_overrides)
        return {"content": json.dumps(payload, ensure_ascii=False)}

    monkeypatch.setattr("app.ledger.llm.gateway.chat_vision", fake_chat_vision)


def test_qr_payload_parse():
    fields = parse_invoice_qr_payload("01,31,25317000000123456701,113.00,20260805,8475")
    assert fields["invoice_no"] == "25317000000123456701"
    assert fields["invoice_type"] == "digital"
    assert fields["amount_total"] == "113.00"
    assert fields["invoice_date"] == "2026-08-05"
    assert fields["status"] == "normal"


def test_qr_legacy_code_and_number():
    fields = parse_invoice_qr_payload("01,10,144032200011,06737263,113.00,05082026,8475")
    assert fields["invoice_code"] == "144032200011"
    assert fields["invoice_no"] == "06737263"
    assert fields["amount_total"] == "113.00"
    assert fields["invoice_date"] == "2026-08-05"


def test_xml_route(db_session):
    result = route_and_parse(
        db_session, filename="invoice.xml", content=xml_invoice_bytes(), allow_vlm=False
    )
    assert result["source_kind"] == "xml"
    fields = result["fields"]
    assert fields["invoice_no"] == "25317000000123456701"
    assert fields["invoice_date"] == "2026-08-05"
    assert fields["amount_total"] == "11300.00"
    assert fields["tax_amount"] == "112.88"
    assert fields["buyer_name"] == "上海某某智能科技有限公司"
    assert fields["seller_tax_no"] == "91310000SELLER000X"
    assert fields["goods_name"] == "*信息技术服务*云服务器租用"
    assert not result["warnings"]


def test_ofd_route_wrapped_xml(db_session):
    import zipfile
    from io import BytesIO

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Doc_0/Attachs/original_invoice.xml", xml_invoice_bytes())
    result = route_and_parse(
        db_session, filename="invoice.ofd", content=buffer.getvalue(), allow_vlm=False
    )
    assert result["source_kind"] == "ofd"
    assert result["fields"]["invoice_no"] == "25317000000123456701"


def test_pdf_text_layer_route(db_session):
    try:
        result = route_and_parse(
            db_session, filename="invoice.pdf", content=pdf_invoice_bytes(), allow_vlm=False
        )
    except Exception:
        import pytest

        pytest.skip("本机缺少 CJK 字体，跳过 PDF 文字层用例")
    assert result["source_kind"] == "pdf_text"
    fields = result["fields"]
    assert fields["invoice_no"] == "25317000000123456701"
    assert fields["invoice_date"] == "2026-08-05"
    assert fields["amount_total"] == "11300.00"
    assert fields["buyer_name"] == "上海某某智能科技有限公司"
    assert fields["buyer_tax_no"] == "91310000MA1K35X00A"
    assert fields["seller_tax_no"] == "91310000SELLER000X"
    assert fields["status"] == "normal"


def test_vlm_fallback_for_image_pdf(db_session, monkeypatch):
    _patch_gateway(monkeypatch, invoice_no="25317000000123456701", amount_total="11300.00")
    result = route_and_parse(
        db_session,
        filename="scan.pdf",
        content=b"%PDF-1.4 fake-no-text-layer",
        allow_vlm=True,
    )
    assert result["source_kind"] == "pdf_image"
    assert "vlm" in result["layers"]
    assert result["fields"]["invoice_no"] == "25317000000123456701"


def test_vlm_for_photo_with_qr_priority(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.ledger.ai.parse.decode_qr_fields",
        lambda content: parse_invoice_qr_payload("01,31,25317000000123456701,113.00,05082026,8475"),
    )
    _patch_gateway(monkeypatch, amount_total="119.00", seller_name="视觉模型识别的店")
    result = route_and_parse(
        db_session, filename="photo.png", content=qr_invoice_png(), allow_vlm=True
    )
    assert result["source_kind"] == "image"
    assert "qr" in result["layers"] and "vlm" in result["layers"]
    assert result["fields"]["amount_total"] == "113.00"
    assert result["fields"]["seller_name"] == "视觉模型识别的店"
    assert any("不一致" in warning for warning in result["warnings"])


def test_image_real_chain_smoke(db_session, monkeypatch):
    """真实链路冒烟：图片走 QR 解码（真 pyzbar）+ 真实 vlm_fields（含 base64 编码），只 mock gateway。"""
    _patch_gateway(monkeypatch, invoice_no="25317000000123456701", amount_total="113.00")
    result = route_and_parse(
        db_session, filename="photo.png", content=plain_png_bytes(), allow_vlm=True
    )
    assert result["source_kind"] == "image"
    assert "vlm" in result["layers"]
    assert result["fields"]["invoice_no"] == "25317000000123456701"
    assert result["fields"]["amount_total"] == "113.00"


def test_qr_real_decode(db_session):
    """真实二维码解码冒烟（pyzbar + PIL 全真链路）。"""
    try:
        from pyzbar import pyzbar  # noqa: F401
    except (ImportError, OSError):
        pytest.skip("zbar 库不可用，跳过真实二维码解码用例")
    result = route_and_parse(
        db_session, filename="qr.png", content=qr_invoice_png(), allow_vlm=False
    )
    assert "qr" in result["layers"]
    assert result["fields"]["invoice_no"] == "25317000000123456701"
    assert result["fields"]["amount_total"] == "113.00"


def test_heic_route_without_vlm(db_session):
    """HEIC（iPhone 拍照默认格式）进入图片白名单且 PIL 能解码，不开 VLM 也走通管道。"""
    pytest.importorskip("pillow_heif")
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (4, 4), (255, 255, 255)).save(buffer, format="HEIF")
    result = route_and_parse(
        db_session, filename="photo.heic", content=buffer.getvalue(), allow_vlm=False
    )
    assert result["source_kind"] == "image"
    assert any("关键字段缺失" in warning for warning in result["warnings"])


def test_parse_degrades_on_internal_error(client, auth_headers, db_session, book, attachments_dir, monkeypatch):
    """内部解析异常（如漏 import 的 NameError）必须降级为 200+parse_failed，绝不 500。"""
    from app.models.ai import AIDoc

    def boom(db, *, filename, content, allow_vlm):
        raise RuntimeError("模拟内部解析异常")

    monkeypatch.setattr("app.ledger.ai.parse.route_and_parse", boom)
    resp = client.post(
        f"/api/ai/parse?book_id={book.id}",
        headers=auth_headers,
        files={"file": ("photo.png", b"\x89PNG-fake", "image/png")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["degraded"] is True
    assert data["doc_type"] == "unknown"
    assert any("自动识别失败" in warning for warning in data["warnings"])
    doc = db_session.query(AIDoc).order_by(AIDoc.id.desc()).first()
    assert doc is not None and doc.staging_path, "原件应已存入 staging"


def test_parse_degrades_on_llm_error(client, auth_headers, db_session, book, attachments_dir, monkeypatch):
    """视觉模型不可用（LLMError）同样降级，用户文件不丢。"""
    from app.ledger.exceptions import LLMError
    from app.models.ai import AIDoc

    def no_vision(db, *, filename, content, allow_vlm):
        raise LLMError("视觉模型未配置")

    monkeypatch.setattr("app.ledger.ai.parse.route_and_parse", no_vision)
    resp = client.post(
        f"/api/ai/parse?book_id={book.id}",
        headers=auth_headers,
        files={"file": ("photo.jpg", b"\xff\xd8\xff-fake", "image/jpeg")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["degraded"] is True
    assert any("视觉模型未配置" in warning for warning in data["warnings"])
    doc = db_session.query(AIDoc).order_by(AIDoc.id.desc()).first()
    assert doc is not None and doc.staging_path


def test_merge_fields_conflict_warning():
    base = {key: "" for key in (
        "invoice_type", "invoice_no", "invoice_code", "invoice_date",
        "seller_name", "seller_tax_no", "buyer_name", "buyer_tax_no",
        "goods_name", "amount_excl", "tax_rate", "tax_amount", "amount_total", "status",
    )}
    high = dict(base, invoice_no="123", amount_total="100.00")
    low = dict(base, amount_total="101.00")
    merged, warnings, used = merge_fields(("qr", high), ("vlm", low))
    assert merged["amount_total"] == "100.00"
    assert any("amount_total" in warning for warning in warnings)
    assert used == ["qr"]
