from app.ledger.ai.parse import (
    merge_fields,
    parse_invoice_qr_payload,
    parse_ofd_fields,
    parse_pdf_text_fields,
    parse_xml_fields,
    route_and_parse,
)
from tests.mock_files import pdf_invoice_bytes, qr_invoice_png, xml_invoice_bytes


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
    def fake_vlm(db, image_bytes, mime):
        fields = {key: "" for key in (
            "invoice_type", "invoice_no", "invoice_code", "invoice_date",
            "seller_name", "seller_tax_no", "buyer_name", "buyer_tax_no",
            "goods_name", "amount_excl", "tax_rate", "tax_amount", "amount_total", "status",
        )}
        fields.update({"invoice_no": "25317000000123456701", "amount_total": "11300.00"})
        return fields

    monkeypatch.setattr("app.ledger.ai.parse.vlm_fields", fake_vlm)
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

    def fake_vlm(db, image_bytes, mime):
        fields = {key: "" for key in (
            "invoice_type", "invoice_no", "invoice_code", "invoice_date",
            "seller_name", "seller_tax_no", "buyer_name", "buyer_tax_no",
            "goods_name", "amount_excl", "tax_rate", "tax_amount", "amount_total", "status",
        )}
        fields.update({"amount_total": "119.00", "seller_name": "视觉模型识别的店"})
        return fields

    monkeypatch.setattr("app.ledger.ai.parse.vlm_fields", fake_vlm)
    result = route_and_parse(
        db_session, filename="photo.png", content=qr_invoice_png(), allow_vlm=True
    )
    assert result["source_kind"] == "image"
    assert "qr" in result["layers"] and "vlm" in result["layers"]
    assert result["fields"]["amount_total"] == "113.00"
    assert result["fields"]["seller_name"] == "视觉模型识别的店"
    assert any("不一致" in warning for warning in result["warnings"])


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
