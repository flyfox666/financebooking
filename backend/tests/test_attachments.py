import hashlib

PNG_BYTES = b"fake-png-binary-content-for-test"
LINES = [
    {"summary": "购买办公用品", "account_code": "1002", "debit": "100.00", "credit": "0"},
    {"summary": "购买办公用品", "account_code": "5603", "debit": "0", "credit": "100.00"},
]


def _create_voucher(db_session, book, mama_user):
    from app.ledger import voucher_service

    return voucher_service.create_voucher(
        db_session,
        book_id=book.id,
        voucher_date="2026-08-06",
        lines=LINES,
        operator_id=mama_user.id,
        attachment_count=1,
    )


def test_upload_syncs_count_and_stores_file(
    client, db_session, book, mama_user, auth_headers, attachments_dir
):
    voucher = _create_voucher(db_session, book, mama_user)
    resp = client.post(
        f"/api/vouchers/{voucher.id}/attachments",
        headers=auth_headers,
        files={"file": ("发票.png", PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["original_filename"] == "发票.png"
    assert data["file_size"] == len(PNG_BYTES)
    assert data["sha256"] == hashlib.sha256(PNG_BYTES).hexdigest()

    stored = [f for f in attachments_dir.rglob("*") if f.is_file()]
    assert len(stored) == 1
    assert stored[0].read_bytes() == PNG_BYTES

    db_session.expire_all()
    assert voucher.attachment_count == 1

    listed = client.get(f"/api/vouchers/{voucher.id}/attachments", headers=auth_headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_upload_multiple_and_delete_syncs_count(
    client, db_session, book, mama_user, auth_headers, attachments_dir
):
    voucher = _create_voucher(db_session, book, mama_user)
    for name in ("a.png", "b.pdf"):
        resp = client.post(
            f"/api/vouchers/{voucher.id}/attachments",
            headers=auth_headers,
            files={"file": (name, PNG_BYTES, "application/octet-stream")},
        )
        assert resp.status_code == 201

    db_session.expire_all()
    assert voucher.attachment_count == 2

    attachment_id = client.get(
        f"/api/vouchers/{voucher.id}/attachments", headers=auth_headers
    ).json()[0]["id"]
    resp = client.delete(
        f"/api/vouchers/{voucher.id}/attachments/{attachment_id}", headers=auth_headers
    )
    assert resp.status_code == 204
    db_session.expire_all()
    assert voucher.attachment_count == 1
    assert len([f for f in attachments_dir.rglob("*") if f.is_file()]) == 1


def test_download_roundtrip(client, db_session, book, mama_user, auth_headers, attachments_dir):
    voucher = _create_voucher(db_session, book, mama_user)
    upload = client.post(
        f"/api/vouchers/{voucher.id}/attachments",
        headers=auth_headers,
        files={"file": ("报销单.pdf", PNG_BYTES, "application/pdf")},
    )
    attachment_id = upload.json()["id"]
    resp = client.get(
        f"/api/vouchers/{voucher.id}/attachments/{attachment_id}/download", headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.content == PNG_BYTES
    assert "filename*=UTF-8''" in resp.headers["content-disposition"]


def test_posted_voucher_locks_attachments(
    client, db_session, book, mama_user, auditor_user, post_flow, auth_headers, attachments_dir
):
    voucher = _create_voucher(db_session, book, mama_user)
    post_flow(voucher, mama_user, auditor_user)

    resp = client.post(
        f"/api/vouchers/{voucher.id}/attachments",
        headers=auth_headers,
        files={"file": ("late.png", PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 400
    assert "锁定" in resp.json()["detail"]


def test_oversize_file_rejected(
    client, db_session, book, mama_user, auth_headers, attachments_dir
):
    voucher = _create_voucher(db_session, book, mama_user)
    big = b"x" * (10 * 1024 * 1024 + 1)
    resp = client.post(
        f"/api/vouchers/{voucher.id}/attachments",
        headers=auth_headers,
        files={"file": ("big.bin", big, "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "10MB" in resp.json()["detail"]


def test_upload_to_missing_voucher_404(client, auth_headers, attachments_dir):
    resp = client.post(
        "/api/vouchers/999/attachments",
        headers=auth_headers,
        files={"file": ("a.png", PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 404


def test_delete_draft_voucher_purges_files(
    client, db_session, book, mama_user, auth_headers, attachments_dir
):
    voucher = _create_voucher(db_session, book, mama_user)
    client.post(
        f"/api/vouchers/{voucher.id}/attachments",
        headers=auth_headers,
        files={"file": ("a.png", PNG_BYTES, "image/png")},
    )
    assert len([f for f in attachments_dir.rglob("*") if f.is_file()]) == 1

    resp = client.delete(f"/api/vouchers/{voucher.id}", headers=auth_headers)
    assert resp.status_code == 204
    assert len([f for f in attachments_dir.rglob("*") if f.is_file()]) == 0
