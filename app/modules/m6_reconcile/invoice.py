from collections import defaultdict
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
import hashlib
from app.errors import DomainError
from app.modules.m5_calculate.rules import allocate

CENTS=Decimal('.01')
SENTINELS={'','-','N/A','NULL','未知'}

def tax_rate(state):
    raw=state.get('parameters',{}).get('tax_rate','0.06')
    try:rate=Decimal(str(raw))
    except InvalidOperation:raise DomainError('PARAMETERS','税率参数无效')
    if not rate.is_finite() or not Decimal(0)<=rate<1:raise DomainError('PARAMETERS','税率参数无效')
    return rate

def payment_month(row):
    # 原缴费的开票月份：paid_month列优先，缺省取缴费记录effective所在月
    raw=str(row.get('paid_month','')).strip()
    return raw if raw.upper() not in SENTINELS else row['effective'][:7]

def tax_of(amount,rate):
    # 价税合计口径：税额=金额×rate/(1+rate)，逐笔到分
    return (amount*rate/(Decimal(1)+rate)).quantize(CENTS,rounding=ROUND_HALF_UP)

def build(state):
    # 裁决：逐笔冲减明细为唯一依据，按原开票月份分组合并出红字票据，跨月不得并入一票
    rate=tax_rate(state);invoices=[]
    for calc in state.get('calculations',[]):
        if calc['code']!='REFUND':continue
        pid=calc['id'];refund=-Decimal(str(calc['difference']))
        records=[r for r in state['rows'] if r['policy_id']==pid and Decimal(str(r['paid']))>0]
        records.sort(key=lambda r:(payment_month(r),str(r['id']),r['_line']))
        total=sum((Decimal(str(r['paid'])) for r in records),Decimal(0))
        shares=allocate([refund*Decimal(str(r['paid']))/total for r in records]) if records else []
        groups=defaultdict(list)
        for r,share in zip(records,shares):
            month=payment_month(r);amount=-share
            groups[month].append({'line_id':hashlib.sha256(f'{pid}|{r["id"]}|{month}|OFFSET'.encode()).hexdigest()[:12],
                'payment_id':r['id'],'payment_line':r['_line'],'month':month,
                'amount':str(amount),'tax':str(tax_of(amount,rate))})
        for month in sorted(groups):
            lines=sorted(groups[month],key=lambda l:(l['payment_id'],l['line_id']))
            amount=sum((Decimal(l['amount']) for l in lines),Decimal(0))
            tax=sum((Decimal(l['tax']) for l in lines),Decimal(0))
            invoices.append({'invoice_id':hashlib.sha256(f'{pid}|{month}|RED'.encode()).hexdigest()[:16],
                'policy_id':pid,'month':month,'kind':'RED',
                'amount':str(amount),'tax':str(tax),'lines':lines})
    return sorted(invoices,key=lambda i:(i['policy_id'],i['month']))
