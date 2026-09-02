"""G6 映射体检 3 接口：template-check 全景 / template-row 编辑 / template-reset 重置。"""


def _check(client, headers, book_id):
    resp = client.get(
        "/api/reports/template-check", headers=headers,
        params={"book_id": book_id, "period": "2026-08"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _row(client, headers, book_id, key, formula):
    return client.put(
        "/api/reports/template-row", headers=headers,
        params={"book_id": book_id, "report": "is", "key": key},
        json=formula,
    )


def _bs_row(client, headers, book_id, key, formula):
    return client.put(
        "/api/reports/template-row", headers=headers,
        params={"book_id": book_id, "report": "bs", "key": key},
        json=formula,
    )


def test_template_check_defaults_healthy(client, auth_headers, book, mock_month):
    data = _check(client, auth_headers, book.id)
    assert data["bs_identity_ok"] is True
    assert data["dirty_refs"] == []
    # 默认模板下：所有「有余额却未映射」的科目都不应存在
    for account in data["unmapped"]:
        assert account["has_balance"] is False


def test_unmapped_account_with_balance_flagged(client, auth_headers, book, mock_month):
    """清空 BS 货币资金行公式 → 1001/1002/1012 变成「有余额但未映射」。
    注：清空 IS 的费用行不会让科目失映射——表结法下 BS「未分配利润」仍吸收损益科目，
    该行因此同时验证了这一设计。"""
    resp = _bs_row(client, auth_headers, book.id, "monetary_funds", [])
    assert resp.status_code == 200, resp.text

    data = _check(client, auth_headers, book.id)
    flagged = {a["code"]: a for a in data["unmapped"] if a["code"] in ("1001", "1002")}
    assert "1001" in flagged and "1002" in flagged
    assert all(a["has_balance"] is True for a in flagged.values())
    # 资产侧缺货币资金 → 恒等式同时被体检抓到
    assert data["bs_identity_ok"] is False


def test_template_row_edit_and_validate(client, auth_headers, book, mock_month):
    # 合法编辑：管理费用行改为取 5602+5603
    resp = _row(client, auth_headers, book.id, "administrative_expenses", [["5602", 1], ["5603", 1]])
    assert resp.status_code == 200, resp.text
    assert resp.json()["formula"] == [["5602", 1], ["5603", 1]]

    data = _check(client, auth_headers, book.id)
    row = next(r for r in data["rows"]["is"] if r["key"] == "administrative_expenses")
    assert {"5602", "5603"} <= {c["code"] for c in row["codes"]}

    # 非法科目 → 400
    bad = _row(client, auth_headers, book.id, "administrative_expenses", [["9999", 1]])
    assert bad.status_code == 400

    # calc 行不可直接编辑 → 400
    calc = _row(client, auth_headers, book.id, "operating_profit", [["5602", 1]])
    assert calc.status_code == 400


def test_template_reset_reverts(client, auth_headers, book, mock_month):
    # 先改坏：清空 BS 货币资金行 → 现金类科目失映射 + 恒等式破
    resp = _bs_row(client, auth_headers, book.id, "monetary_funds", [])
    assert resp.status_code == 200
    data = _check(client, auth_headers, book.id)
    assert any(a["code"] == "1002" for a in data["unmapped"])
    assert data["bs_identity_ok"] is False

    # 重置恢复默认
    resp = client.post(
        "/api/reports/template-reset", headers=auth_headers,
        params={"book_id": book.id, "report": "bs"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True

    data = _check(client, auth_headers, book.id)
    row = next(r for r in data["rows"]["bs"] if r["key"] == "monetary_funds")
    assert {c["code"] for c in row["codes"]} == {"1001", "1002", "1012"}
    assert all(a["code"] not in ("1001", "1002", "1012") for a in data["unmapped"])
    assert data["bs_identity_ok"] is True

    # 重置幂等：可重复调用
    again = client.post(
        "/api/reports/template-reset", headers=auth_headers,
        params={"book_id": book.id, "report": "bs"},
    )
    assert again.status_code == 200
