from decimal import Decimal

from app.ledger import invoice_service
from tests.mock_invoices import purchase_file, quarter3_under_threshold


def test_import_creates_and_recomputes_tax(client, auth_headers, book):
    resp = client.post(
        "/api/invoices/import",
        headers=auth_headers,
        params={"book_id": book.id, "kind": "sales"},
        files={"file": ("sales.xlsx", quarter3_under_threshold(), "application/vnd.ms-excel")},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["created"] == 6
    assert result["updated"] == 0

    invoices = client.get(
        "/api/invoices", headers=auth_headers, params={"book_id": book.id, "kind": "sales"}
    ).json()
    assert len(invoices) == 6
    special = [i for i in invoices if i["invoice_type"] == "special"]
    assert len(special) == 1
    assert special[0]["amount_excl"] == "49504.95"
    assert special[0]["tax_amount"] == "495.05"

    red = [i for i in invoices if i["status"] == "red"]
    assert len(red) == 1
    assert red[0]["amount_total"] == "-10000.00"
    assert red[0]["amount_excl"] == "-9900.99"


def test_reimport_updates_without_duplicates(client, auth_headers, book):
    payload = {
        "params": {"book_id": book.id, "kind": "sales"},
        "files": {"file": ("sales.xlsx", quarter3_under_threshold(), "application/vnd.ms-excel")},
    }
    first = client.post("/api/invoices/import", headers=auth_headers, **payload)
    assert first.json()["created"] == 6
    second = client.post("/api/invoices/import", headers=auth_headers, **payload)
    assert second.json()["created"] == 0
    assert second.json()["updated"] == 6
    invoices = client.get(
        "/api/invoices", headers=auth_headers, params={"book_id": book.id, "kind": "sales"}
    ).json()
    assert len(invoices) == 6


def test_purchase_kind_is_separate(client, db_session, auth_headers, book):
    client.post(
        "/api/invoices/import",
        headers=auth_headers,
        params={"book_id": book.id, "kind": "sales"},
        files={"file": ("sales.xlsx", quarter3_under_threshold(), "application/vnd.ms-excel")},
    )
    resp = client.post(
        "/api/invoices/import",
        headers=auth_headers,
        params={"book_id": book.id, "kind": "purchase"},
        files={"file": ("purchase.xlsx", purchase_file(), "application/vnd.ms-excel")},
    )
    assert resp.json()["created"] == 1
    purchases = invoice_service.list_invoices(db_session, book_id=book.id, kind="purchase")
    assert len(purchases) == 1
    assert purchases[0].amount_total == Decimal("3200.00")


def test_invalid_excel_rejected(client, auth_headers, book):
    resp = client.post(
        "/api/invoices/import",
        headers=auth_headers,
        params={"book_id": book.id, "kind": "sales"},
        files={"file": ("bad.xlsx", b"not-an-excel", "application/vnd.ms-excel")},
    )
    assert resp.status_code == 400
