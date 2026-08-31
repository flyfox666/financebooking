import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"
XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<EInvoice>"
    "<InvoiceNo>25317000000198765012</InvoiceNo>"
    "<IssueTime>2026-08-20 14:00:00</IssueTime>"
    "<BuyerInfo><BuyerName>容器验收公司</BuyerName><BuyerId>91310000MA1K35X00A</BuyerId></BuyerInfo>"
    "<SellerInfo><SellerName>上海云服务商有限公司</SellerName><SellerId>91310000SELLER000X</SellerId></SellerInfo>"
    "<Item><ItemName>*信息技术服务*云服务器租用费</ItemName></Item>"
    "<TotalAmount>8480.00</TotalAmount>"
    "<TotalTaxAmount>84.00</TotalTaxAmount>"
    "</EInvoice>"
).encode("utf-8")

BOUNDARY = "----ledgerai-demo"


def http(method, path, body=None, headers=None, raw=False):
    data = body if raw else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(BASE + path, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if data is not None and not raw:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=120) as resp:
        payload = resp.read()
    return json.loads(payload) if payload else None


def multipart(fields, files):
    lines = []
    for key, value in fields.items():
        lines.append(f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    for key, filename, content, ctype in files:
        lines.append(
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n".encode() + content + b"\r\n"
        )
    lines.append(f"--{BOUNDARY}--\r\n".encode())
    return b"".join(lines), {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}


token = http("POST", "/api/auth/login", {"username": "admin", "password": "admin123456"})["access_token"]
auth = {"Authorization": f"Bearer {token}"}
print("① 登录成功")

body, headers = multipart({}, [("file", "invoice.xml", XML, "application/xml")])
parsed = http("POST", "/api/ai/parse?book_id=1", body, {**headers, **auth}, raw=True)
doc_id = parsed["doc_id"]
print(f"② 解析完成（{parsed['source_kind']}）：发票号 {parsed['fields'].get('invoice_no')}，价税合计 {parsed['fields'].get('amount_total')}")
print(f"   警告: {parsed['warnings'] or '无'}")

suggestion = http("POST", "/api/ai/suggest", {"book_id": 1, "doc_id": doc_id}, auth)
print("③ 千问生成的候选凭证：")
for line in suggestion["voucher"]["lines"]:
    print(f"   {line['summary']} | {line['account_code']} | 借 {line['debit']} / 贷 {line['credit']}")
print(f"   置信度 {suggestion['confidence']}  警告 {suggestion['warnings'] or '无'}")

confirmed = http(
    "POST",
    "/api/ai/confirm",
    {"doc_id": doc_id, "voucher_date": "2026-08-20", "lines": suggestion["voucher"]["lines"]},
    auth,
)
print(f"④ 已确认落账：凭证 {confirmed['voucher_no_display']}（{confirmed['status']}），附件 {confirmed['attachment_count']} 张，source={confirmed['source']}")
