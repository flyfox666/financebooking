"""Regression scenarios from the September review; all data is synthetic."""
import json
import sqlite3
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text, func

from app.ledger import voucher_service as vs
from app.ledger.ai.confirmation import confirm, CandidateRisk
from app.ledger.ai.suggest import validate_candidate, discard_document
from app.ledger.exceptions import VoucherError
from app.models.ai import AIDoc
from app.models.tax import Invoice
from app.models.voucher import Voucher
from tests.conftest import attach_original
from tests.test_voucher_api import _headers


def lines(amount='100.00'):
    return [dict(summary='Synthetic expense', account_code='5602', debit=amount, credit='0'),
            dict(summary='Synthetic payment', account_code='1002', debit='0', credit=amount, cf_item='other_out')]


def document(db, book, fields=None, path=None):
    doc = AIDoc(book_id=book.id, doc_type='invoice' if fields else 'text',
        source_kind='pdf' if path else 'text', fields_json=json.dumps(fields or {}),
        staging_path=str(path) if path else '', file_name='synthetic.pdf', status='parsed')
    db.add(doc)
    db.commit()
    return doc


def confirm_doc(db, doc, user, **kw):
    return confirm(db, doc_id=doc.id, voucher_date='2026-08-05',
                   lines=kw.pop('lines', lines()), operator_id=user.id, **kw)


def draft(db, book, user):
    return vs.create_voucher(db, book_id=book.id, voucher_date='2026-08-05',
        lines=lines(), attachment_count=1, operator_id=user.id)


def test_count_cannot_replace_original(db_session, book, mama_user):
    voucher = draft(db_session, book, mama_user)
    with pytest.raises(VoucherError, match='原始单据'):
        vs.submit_voucher(db_session, voucher_id=voucher.id, operator_id=mama_user.id)
    attach_original(db_session, voucher, mama_user.id)
    assert vs.submit_voucher(db_session, voucher_id=voucher.id, operator_id=mama_user.id).status == 'submitted'


def test_original_tampering_blocks_audit(db_session, book, mama_user, auditor_user, attachments_dir):
    voucher = draft(db_session, book, mama_user)
    attach_original(db_session, voucher, mama_user.id)
    vs.submit_voucher(db_session, voucher_id=voucher.id, operator_id=mama_user.id)
    next(attachments_dir.rglob('*.txt')).write_bytes(b'tampered')
    with pytest.raises(VoucherError):
        vs.audit_voucher(db_session, voucher_id=voucher.id, operator=auditor_user)


def test_editor_cannot_approve(db_session, book, mama_user, admin_user):
    voucher = draft(db_session, book, mama_user)
    vs.update_voucher(db_session, voucher_id=voucher.id, lines=lines('123'), operator_id=admin_user.id)
    attach_original(db_session, voucher, mama_user.id)
    vs.submit_voucher(db_session, voucher_id=voucher.id, operator_id=mama_user.id)
    with pytest.raises(VoucherError, match='同一人'):
        vs.audit_voucher(db_session, voucher_id=voucher.id, operator=admin_user)


def test_auditor_cannot_edit_and_source_cannot_be_forged(client, db_session, book, mama_user, auditor_user, auth_headers):
    voucher = draft(db_session, book, mama_user)
    h = _headers(client, 'papa', 'papa123456')
    assert client.patch(f'/api/vouchers/{voucher.id}', headers=h, json={'lines':lines('123')}).status_code == 403
    response = client.post('/api/vouchers', params={'book_id':book.id}, headers=auth_headers,
        json={'voucher_date':'2026-08-05','source':'ai','lines':lines()})
    assert response.status_code == 400


def test_batch_checks_each_entity_book(client, db_session, book, mama_user, admin_user, auth_headers):
    from app.ledger.book_service import create_book
    other = create_book(db_session, name='Synthetic other book', tax_no='', start_period='2026-08')
    foreign = draft(db_session, other, admin_user)
    response = client.post('/api/vouchers/batch', headers=auth_headers, params={'book_id':book.id},
        json={'ids':[foreign.id], 'action':'delete'})
    assert response.status_code == 200
    assert not response.json()['ok'] and response.json()['failed'][0]['id'] == foreign.id
    assert db_session.get(Voucher, foreign.id) is not None


def test_cannot_move_before_book_start(db_session, book, mama_user):
    voucher = draft(db_session, book, mama_user)
    with pytest.raises(VoucherError):
        vs.update_voucher(db_session, voucher_id=voucher.id, voucher_date='2026-07-31', operator_id=mama_user.id)


def test_reversal_unique_and_unpost_restores_original(db_session, book, mama_user, auditor_user, admin_user, post_flow):
    voucher = draft(db_session, book, mama_user)
    post_flow(voucher, mama_user, auditor_user)
    red = vs.reverse_voucher(db_session, voucher_id=voucher.id, operator=auditor_user)
    with pytest.raises(VoucherError):
        vs.reverse_voucher(db_session, voucher_id=voucher.id, operator=auditor_user)
    post_flow(red, mama_user, admin_user)
    vs.unpost_voucher(db_session, voucher_id=red.id)
    db_session.refresh(voucher)
    assert voucher.status == 'posted' and voucher.voided_by_voucher_id is None


def test_shared_original_survives_first_confirmation(db_session, book, mama_user, tmp_path):
    path = tmp_path/'shared.pdf'
    path.write_bytes(b'Synthetic shared original')
    first = document(db_session, book, path=path)
    second = document(db_session, book, path=path)
    a = confirm_doc(db_session, first, mama_user)
    assert path.exists() and a.attachment_count == 1
    b = confirm_doc(db_session, second, mama_user, lines=lines('200'))
    assert b.attachment_count == 1 and not path.exists()


def test_discard_does_not_remove_shared_original(db_session, book, tmp_path):
    path = tmp_path/'shared.pdf'
    path.write_bytes(b'original')
    a = document(db_session, book, path=path)
    b = document(db_session, book, path=path)
    discard_document(db_session, a.id)
    assert path.exists()
    discard_document(db_session, b.id)
    assert not path.exists()


def test_attachment_failure_rolls_back_confirmation(db_session, book, mama_user, tmp_path, monkeypatch):
    path = tmp_path/'original.pdf'
    path.write_bytes(b'original')
    doc = document(db_session, book, path=path)
    def fail(*a, **kw):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr('app.ledger.ai.confirmation.save_attachment', fail)
    with pytest.raises(OSError):
        confirm_doc(db_session, doc, mama_user)
    assert db_session.scalar(select(func.count()).select_from(Voucher)) == 0
    db_session.refresh(doc)
    assert doc.status == 'parsed' and doc.voucher_id is None and path.exists()


def test_duplicate_requires_specific_reason_and_rechecks_changes(db_session, book, mama_user, auditor_user, post_flow):
    voucher = draft(db_session, book, mama_user)
    post_flow(voucher, mama_user, auditor_user)
    doc = document(db_session, book)
    with pytest.raises(CandidateRisk) as risk:
        confirm_doc(db_session, doc, mama_user)
    fingerprint = risk.value.detail['risk_fingerprint']
    with pytest.raises(CandidateRisk):
        confirm_doc(db_session, doc, mama_user, risk_fingerprint=fingerprint, risk_reason='不')
    changed=lines(); changed[0]['summary']='Changed candidate'
    with pytest.raises(CandidateRisk):
        confirm_doc(db_session, doc, mama_user, lines=changed, risk_fingerprint=fingerprint, risk_reason='这是另一笔费用')
    saved = confirm_doc(db_session, doc, mama_user, risk_fingerprint=fingerprint, risk_reason='这是另一笔费用')
    assert confirm_doc(db_session, doc, mama_user).id == saved.id
    assert json.loads(doc.confirmation_json)['reason'] == '这是另一笔费用'


@pytest.mark.parametrize('amount', ['NaN', 'Infinity', 'not a number'])
def test_invalid_ai_amount_returns_validation_error(db_session, book, amount):
    errors, _ = validate_candidate(db_session, book.id, {'voucher_date':'2026-08-05','lines':lines(amount)}, None)
    assert any('金额' in error for error in errors)


@pytest.mark.parametrize('account', ['5602', '1601', '5401'])
def test_small_scale_purchase_tax_cannot_pass_confirmation(db_session, book, mama_user, account):
    doc = document(db_session, book, {'buyer_tax_no':book.tax_no})
    candidate = [dict(summary='expense',account_code=account,debit='100',credit='0'),
        dict(summary='input tax',account_code='2221',debit='13',credit='0'),
        dict(summary='bank',account_code='1002',debit='0',credit='113')]
    with pytest.raises(VoucherError, match='不可拆税'):
        confirm_doc(db_session, doc, mama_user, lines=candidate)


def test_invoice_link_respects_direction(db_session, book, mama_user):
    purchase = Invoice(book_id=book.id, kind='purchase', invoice_no='12345678', invoice_date=date(2026,8,1), amount_total=Decimal(100))
    sales = Invoice(book_id=book.id, kind='sales', invoice_no='12345678', invoice_date=date(2026,8,1), amount_total=Decimal(100))
    db_session.add_all([sales,purchase]); db_session.commit()
    doc=document(db_session, book, {'invoice_no':'12345678','buyer_tax_no':book.tax_no})
    voucher=confirm_doc(db_session,doc,mama_user)
    db_session.refresh(purchase); db_session.refresh(sales)
    assert purchase.voucher_id==voucher.id and sales.voucher_id is None


def test_tagged_external_flows_with_zero_cash_net(db_session, book, mama_user, auditor_user, post_flow):
    from app.ledger.cashflow import _flows
    candidate=[dict(summary='receipt',account_code='1002',debit='100',credit='0',cf_item='sales'),
        dict(summary='income',account_code='5001',debit='0',credit='100'),
        dict(summary='expense',account_code='5602',debit='100',credit='0'),
        dict(summary='payment',account_code='1002',debit='0',credit='100',cf_item='other_out')]
    voucher=vs.create_voucher(db_session,book_id=book.id,voucher_date='2026-08-05',lines=candidate,attachment_count=1,operator_id=mama_user.id)
    post_flow(voucher,mama_user,auditor_user)
    flows=_flows(db_session,book.id,'2026-08','2026-08')
    assert flows['sales']==100 and flows['other_out']==-100


def test_provider_credentials_encrypted_at_rest(db_session):
    from app.models.llm import LLMProvider
    provider=LLMProvider(name='synthetic',base_url='https://example.invalid',api_key='synthetic-secret',model='test')
    db_session.add(provider); db_session.commit()
    raw=db_session.scalar(text('SELECT api_key FROM llm_provider WHERE id=:id'),{'id':provider.id})
    assert raw.startswith('enc:v1:') and 'synthetic-secret' not in raw
    db_session.expire_all()
    assert db_session.get(LLMProvider,provider.id).api_key=='synthetic-secret'


def test_full_backup_restores_original_and_key(tmp_path, monkeypatch):
    import hashlib
    from app.core.config import get_settings
    from app.core.backup import run_full_backup, restore_full_backup
    root=tmp_path/'source'; attachments=root/'attachments'; attachments.mkdir(parents=True)
    content=b'Synthetic source'; (attachments/'original.txt').write_bytes(content)
    (root/'.model_key').write_bytes(b'Synthetic key')
    with sqlite3.connect(root/'ledger.db') as db:
        db.execute('CREATE TABLE attachment(file_path TEXT,sha256 TEXT)')
        db.execute('INSERT INTO attachment VALUES (?,?)',('original.txt',hashlib.sha256(content).hexdigest()))
    settings=get_settings()
    monkeypatch.setattr(settings,'DATABASE_URL','sqlite:///'+str(root/'ledger.db'))
    monkeypatch.setattr(settings,'ATTACHMENTS_DIR',str(attachments))
    monkeypatch.setattr(settings,'BACKUP_DIR',str(root/'backups'))
    archive=run_full_backup()
    restore_full_backup(archive,tmp_path/'restored')
    assert (tmp_path/'restored'/'attachments'/'original.txt').read_bytes()==content
    assert (tmp_path/'restored'/'.model_key').read_bytes()==b'Synthetic key'
    with pytest.raises(ValueError):
        restore_full_backup(archive,tmp_path/'restored')
    (attachments/'original.txt').write_bytes(b'corrupt')
    with pytest.raises(RuntimeError):
        run_full_backup()
    assert json.loads((root/'backups'/'status.json').read_text())['ok'] is False


@pytest.mark.parametrize('same_document', [True, False])
def test_concurrent_confirmations_create_only_one_voucher(db_session, book, mama_user, tmp_path, same_document):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    path=tmp_path/'concurrent.pdf'; path.write_bytes(b'Synthetic invoice original')
    fields={'invoice_no':'12349999','invoice_date':'2026-08-05','buyer_tax_no':book.tax_no,'amount_total':'100.00'}
    a=document(db_session,book,fields,path)
    b=a if same_document else document(db_session,book,fields,path)
    filename=tmp_path/'concurrent.db'
    with sqlite3.connect(filename) as destination:
        db_session.connection().connection.driver_connection.backup(destination)
    concurrent_engine=create_engine('sqlite:///'+str(filename),connect_args={'timeout':10})
    gate=Barrier(2)
    def worker(doc_id):
        with Session(concurrent_engine) as db:
            gate.wait(timeout=5)
            try:
                return confirm(db,doc_id=doc_id,voucher_date='2026-08-05',lines=lines(),operator_id=mama_user.id).id
            except VoucherError:
                return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(worker, ident) for ident in (a.id,b.id)]
        results=[future.result(timeout=15) for future in futures]
    with Session(concurrent_engine) as db:
        assert db.scalar(select(func.count()).select_from(Voucher)) == 1
        assert db.scalar(select(func.count()).select_from(Invoice)) == 1
    assert results[0] == results[1] if same_document else results.count(None) == 1
    concurrent_engine.dispose()


def test_ai_contact_tool_cannot_create_data(db_session, book):
    from app.ledger.ai.agent import execute_tool
    from app.models.contact import Contact
    result=json.loads(execute_tool(db_session,book.id,'find_or_create_contact',{'name':'Synthetic new company','ctype':'supplier'}))
    assert result['requires_user_action'] is True
    assert db_session.scalar(select(func.count()).select_from(Contact)) == 0


def test_bank_fee_model_account_is_corrected_with_explanation(db_session, book, monkeypatch):
    from app.ledger.ai.agent import run_agent
    candidate={'voucher_date':'2026-08-05','lines':lines('17')}
    candidate['lines'][0]['summary']='支付银行账户管理费'
    monkeypatch.setattr('app.ledger.ai.agent.chat_with_tools',lambda *a,**kw:
        {'content':json.dumps({'reply':'财务费用','voucher':candidate}),'tool_calls':[]})
    result=run_agent(db_session,book_id=book.id,history=[])
    assert result['voucher']['lines'][0]['account_code']=='5603'
    assert '调整至' in result['reply']


def test_original_edit_reopens_review_and_tracks_all_editors(client, db_session, book, mama_user, auditor_user, admin_user):
    voucher=draft(db_session,book,mama_user)
    attach_original(db_session,voucher,mama_user.id)
    vs.submit_voucher(db_session,voucher_id=voucher.id,operator_id=mama_user.id)
    vs.audit_voucher(db_session,voucher_id=voucher.id,operator=auditor_user)
    h=_headers(client,'papa','papa123456')
    assert client.post(f'/api/vouchers/{voucher.id}/attachments',headers=h,
        files={'file':('synthetic.txt',b'new','text/plain')}).status_code==403
    from app.ledger.attachment_service import save_attachment
    save_attachment(db_session,voucher=voucher,content=b'new',original_filename='new.txt',content_type='text/plain',operator_id=admin_user.id)
    assert voucher.status=='draft' and voucher.audited_by is None
    vs.update_voucher(db_session,voucher_id=voucher.id,lines=lines('123'),operator_id=mama_user.id)
    vs.submit_voucher(db_session,voucher_id=voucher.id,operator_id=mama_user.id)
    with pytest.raises(VoucherError,match='同一人'):
        vs.audit_voucher(db_session,voucher_id=voucher.id,operator=admin_user)
    assert vs.audit_voucher(db_session,voucher_id=voucher.id,operator=auditor_user).status=='audited'


@pytest.mark.parametrize('same_instance', [True, False])
def test_launcher_distinguishes_occupied_port(tmp_path, monkeypatch, same_instance):
    from io import BytesIO
    from app import launcher
    monkeypatch.setattr('app.core.config.DATA_DIR',tmp_path)
    (tmp_path/'.instance_id').write_text('synthetic-instance')
    monkeypatch.setenv('LEDGER_INSTANCE_ID','initial')
    monkeypatch.setattr(launcher,'_port_in_use',lambda host,port:port==8000)
    response={'app':'LedgerAI','instance':'synthetic-instance' if same_instance else 'different-instance'}
    monkeypatch.setattr(launcher.urllib.request,'urlopen',lambda *a,**kw:BytesIO(json.dumps(response).encode()))
    opened=[]; launched=[]
    monkeypatch.setattr(launcher.webbrowser,'open',opened.append)
    monkeypatch.setattr('uvicorn.run',lambda app,**kw:launched.append(kw['port']))
    class QuietThread:
        def __init__(self,*a,**kw): pass
        def start(self): pass
    monkeypatch.setattr(launcher.threading,'Thread',QuietThread)
    launcher.main()
    assert opened==['http://127.0.0.1:8000/app'] if same_instance else not opened
    assert launched==[] if same_instance else launched==[8001]


def test_general_taxpayer_can_split_input_tax(db_session, book, mama_user):
    book.taxpayer_type='general'; db_session.commit()
    doc=document(db_session,book,{'buyer_tax_no':book.tax_no,'amount_total':'113'})
    candidate=[dict(summary='asset',account_code='1601',debit='100',credit='0'),
        dict(summary='input tax',account_code='2221',debit='13',credit='0'),
        dict(summary='bank',account_code='1002',debit='0',credit='113')]
    assert confirm_doc(db_session,doc,mama_user,lines=candidate).total_debit==113


def test_resume_has_explicit_request_and_plain_reply(db_session, book, monkeypatch):
    from app.ledger.ai.agent import run_agent
    requests=[]
    def model(*a,**kw):
        requests.extend(kw['messages'])
        return {'content':json.dumps({'reply':'请确认业务用途','voucher':None}), 'tool_calls':[]}
    monkeypatch.setattr('app.ledger.ai.agent.chat_with_tools',model)
    result=run_agent(db_session,book_id=book.id,history=[],doc_context='{}')
    assert requests[-1]['role']=='user'
    assert result['reply']=='请确认业务用途' and result['voucher'] is None
