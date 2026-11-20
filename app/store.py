import sqlite3,json,os
from pathlib import Path
from app.errors import DomainError

def connection():
    path=Path(os.environ.get('DB_PATH','data/records.db'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    # 红字票据台账：版本只增不改，指针原子拨动；中断时指针仍指上一完整版本
    db.execute('CREATE TABLE IF NOT EXISTS invoice_versions (run_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY (run_id, version))')
    db.execute('CREATE TABLE IF NOT EXISTS invoice_current (run_id TEXT PRIMARY KEY, version INTEGER NOT NULL)')
    return db

def save(run_id, body):
    with connection() as db:
        db.execute('INSERT OR IGNORE INTO runs(id,body) VALUES (?,?)',(run_id,json.dumps(body,ensure_ascii=False)))
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(run_id,))
    save_invoice_version(run_id,body.get('invoices',[]))
    return get(run_id)

def save_invoice_version(run_id, invoices):
    # 同一退费重跑内容相同则不新增版本、不新增票据；内容变化只追加新版本，旧版本永不覆盖
    payload=json.dumps(invoices,ensure_ascii=False,sort_keys=True)
    with connection() as db:
        current=db.execute('SELECT v.version,v.body FROM invoice_current c JOIN invoice_versions v ON v.run_id=c.run_id AND v.version=c.version WHERE c.run_id=?',(run_id,)).fetchone()
        if current and current['body']==payload:return current['version']
        version=(current['version'] if current else 0)+1
        db.execute('INSERT INTO invoice_versions(run_id,version,body) VALUES (?,?,?)',(run_id,version,payload))
        db.execute('INSERT INTO invoice_current(run_id,version) VALUES (?,?) ON CONFLICT(run_id) DO UPDATE SET version=excluded.version',(run_id,version))
        return version

def get_invoices(run_id):
    with connection() as db:
        r=db.execute('SELECT v.version,v.body FROM invoice_current c JOIN invoice_versions v ON v.run_id=c.run_id AND v.version=c.version WHERE c.run_id=?',(run_id,)).fetchone()
        if not r:return None
        return {'version':r['version'],'invoices':json.loads(r['body'])}

def get(run_id):
    with connection() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','批次不存在',404)
    body=json.loads(r['body']);body.update(run_id=r['id'],review_version=r['version'],decision=r['decision'])
    ledger=get_invoices(run_id)
    if ledger is not None:body['invoices']=ledger['invoices'];body['invoice_version']=ledger['version']
    return body

def history():
    with connection() as db:rows=db.execute("SELECT id,version,decision FROM runs ORDER BY CASE WHEN id=(SELECT value FROM meta WHERE key='latest') THEN 0 ELSE 1 END,rowid DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

def review(run_id, version, decision):
    if decision not in ('通过','退回'):raise DomainError('DECISION','复核状态无效')
    with connection() as db:
        cur=db.execute('UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?',(decision,run_id,version))
        if cur.rowcount!=1:
            if not db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():raise DomainError('NOT_FOUND','批次不存在',404)
            raise DomainError('STALE','复核版本已更新，请刷新',409)
    return get(run_id)
