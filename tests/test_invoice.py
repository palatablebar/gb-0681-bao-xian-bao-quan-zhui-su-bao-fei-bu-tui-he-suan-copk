import csv,io,json
from copy import deepcopy
from decimal import Decimal
import pytest
from app import store
from app.errors import DomainError
from app.workflow import run,load
from app.modules.m6_reconcile.compare import invoice_balance
from app.modules.m7_archive.export_out import bundle

@pytest.fixture
def rstate():return run(load('refund.csv'),{})

def test_refund_detected(rstate):
    codes={c['id']:c['code'] for c in rstate['calculations']}
    assert codes['PR01']=='REFUND' and codes['PR02']=='REFUND' and codes['PR03']=='SUPPLEMENT'

def test_single_month_one_invoice(rstate):
    inv=[i for i in rstate['invoices'] if i['policy_id']=='PR01']
    assert len(inv)==1 and inv[0]['month']=='2026-01' and inv[0]['amount']=='-932.60'
    assert [l['payment_id'] for l in inv[0]['lines']]==['90001']

def test_cross_month_split(rstate):
    # 裁决：原缴费跨两个开票月份必须分票，冲减期间各归原缴费月份
    inv=[i for i in rstate['invoices'] if i['policy_id']=='PR02']
    assert [i['month'] for i in inv]==['2026-01','2026-03']
    assert [i['amount'] for i in inv]==['-511.01','-421.59']
    assert inv[0]['lines'][0]['payment_id']=='90003' and inv[1]['lines'][0]['payment_id']=='90004'

def test_no_merged_current_month(rstate):
    # 不得并入“本月一张”：票据期间只能来自原缴费开票月份
    assert {i['month'] for i in rstate['invoices']}=={'2026-01','2026-03'}

def test_tax_per_line(rstate):
    inv=[i for i in rstate['invoices'] if i['policy_id']=='PR02']
    assert [i['tax'] for i in inv]==['-28.93','-23.86']

def test_invoice_conservation(rstate):
    for inv in rstate['invoices']:
        assert sum(Decimal(l['amount']) for l in inv['lines'])==Decimal(inv['amount'])
        assert sum(Decimal(l['tax']) for l in inv['lines'])==Decimal(inv['tax'])
    r=rstate['reconcile']['invoices']
    assert r['balanced'] and Decimal(r['amount'])==Decimal(r['refund'])

def test_ids_deterministic(rstate):
    again=run(load('refund.csv'),{})
    assert [i['invoice_id'] for i in again['invoices']]==[i['invoice_id'] for i in rstate['invoices']]
    assert again['run_id']==rstate['run_id']

def test_month_falls_back_to_effective():
    text='id,policy_id,version,kind,effective,start,end,annual_premium,paid,ref,cover,note\nA1,PX1,1,issue,2026-01-01,2026-01-01,2027-01-01,3650,3650,,基础险,原件\nA2,PX1,2,change,2026-07-01,2026-01-01,2027-01-01,1800,0,,基础险,减保\n'
    s=run(text.encode(),{})
    assert [i['month'] for i in s['invoices']]==['2026-01']

def test_bad_paid_month_quarantined():
    text='id,policy_id,version,kind,effective,start,end,annual_premium,paid,ref,cover,note,paid_month\nB1,PX2,1,issue,2026-01-01,2026-01-01,2027-01-01,3650,3650,,基础险,原件,2026-13\nB2,PX2,2,change,2026-07-01,2026-01-01,2027-01-01,1800,0,,基础险,减保,\n'
    s=run(text.encode(),{})
    assert any(q['code']=='DATE' for q in s['quarantine'])

def test_tax_rate_parameter():
    s=run(load('refund.csv'),{'tax_rate':'0'})
    assert all(i['tax']=='0.00' for i in s['invoices'])
    with pytest.raises(DomainError) as e:run(load('refund.csv'),{'tax_rate':'abc'})
    assert e.value.code=='PARAMETERS'

def test_invoice_balance_detects_tamper(rstate):
    s=deepcopy(rstate);s['invoices'][0]['amount']='-1.00'
    assert invoice_balance(s)['balanced'] is False

def test_rerun_adds_no_invoice(rstate,tmp_path,monkeypatch):
    # 同一退费重跑：不新增版本、不新增票据
    monkeypatch.setenv('DB_PATH',str(tmp_path/'r.db'))
    store.save(rstate['run_id'],rstate);store.save(rstate['run_id'],rstate)
    with store.connection() as db:
        n=db.execute('SELECT COUNT(*) c FROM invoice_versions').fetchone()['c']
        m=db.execute('SELECT COUNT(*) c FROM invoice_current').fetchone()['c']
    assert n==1 and m==1 and store.get_invoices(rstate['run_id'])['version']==1

def test_interrupted_keeps_previous(rstate,tmp_path,monkeypatch):
    # 生成中断：新版本行已落库但指针未拨动，读取恢复上一完整版本
    monkeypatch.setenv('DB_PATH',str(tmp_path/'r.db'))
    store.save(rstate['run_id'],rstate)
    before=store.get_invoices(rstate['run_id'])
    with store.connection() as db:
        db.execute('INSERT INTO invoice_versions(run_id,version,body) VALUES (?,?,?)',(rstate['run_id'],before['version']+1,'[]'))
    after=store.get_invoices(rstate['run_id'])
    assert after['version']==before['version'] and after['invoices']==before['invoices']

def test_failed_transaction_rolls_back(rstate,tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'r.db'))
    store.save(rstate['run_id'],rstate)
    with pytest.raises(RuntimeError):
        with store.connection() as db:
            db.execute('INSERT INTO invoice_versions(run_id,version,body) VALUES (?,?,?)',(rstate['run_id'],2,'[]'))
            db.execute('INSERT INTO invoice_current(run_id,version) VALUES (?,?) ON CONFLICT(run_id) DO UPDATE SET version=excluded.version',(rstate['run_id'],2))
            raise RuntimeError('生成中断')
    assert store.get_invoices(rstate['run_id'])['version']==1

def test_old_version_never_overwritten(rstate,tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'r.db'))
    store.save(rstate['run_id'],rstate)
    changed=[dict(i,amount='-1.00') for i in rstate['invoices']]
    assert store.save_invoice_version(rstate['run_id'],changed)==2
    with store.connection() as db:
        bodies=[r['body'] for r in db.execute('SELECT body FROM invoice_versions ORDER BY version').fetchall()]
    assert len(bodies)==2 and json.loads(bodies[0])==rstate['invoices']
    assert store.get_invoices(rstate['run_id'])['invoices']==changed

def test_export_contains_invoices(rstate):
    text=bundle(rstate)
    assert text.count('红字票据')==len(rstate['invoices'])
    assert text.count('冲减明细')==sum(len(i['lines']) for i in rstate['invoices'])

def test_download_same_basis(client):
    # 结案下载与逐笔票据结果同出一份台账
    s=client.post('/api/runs',json={'sample':'refund.csv'}).get_json()
    api=client.get('/api/runs/'+s['run_id']).get_json()
    e=client.get('/api/archive/'+s['run_id']+'/export.csv')
    assert e.status_code==200
    rows=list(csv.reader(io.StringIO(e.data.decode('utf-8-sig'))))
    heads=[r for r in rows if r[0]=='红字票据']
    lines=[r for r in rows if r[0]=='冲减明细']
    assert [r[3] for r in heads]==[i['invoice_id'] for i in api['invoices']]
    assert [r[3] for r in lines]==[l['payment_id'] for i in api['invoices'] for l in i['lines']]
    assert api['invoice_version']==1
