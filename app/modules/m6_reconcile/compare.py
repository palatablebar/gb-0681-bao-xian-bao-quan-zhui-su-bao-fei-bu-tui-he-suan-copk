from decimal import Decimal

def balance(state):
    accepted=len(state['rows']);isolated=len(state['quarantine'])
    accounted=accepted+isolated+state['blank_count']+state['header_count']+state['duplicate_count']
    return {'input':state['input_count'],'accepted':accepted,'isolated':isolated,
        'blank':state['blank_count'],'headers':state['header_count'],'duplicates':state['duplicate_count'],
        'accounted':accounted,'balanced':accounted==state['input_count'],
        'business':state.get('business',{})}

def invoice_balance(state):
    # 税务核对链：红字票据合计须等于退费合计（红字为负）
    invoices=state.get('invoices',[])
    amount=sum((Decimal(str(i['amount'])) for i in invoices),Decimal(0))
    tax=sum((Decimal(str(i['tax'])) for i in invoices),Decimal(0))
    refund=sum((Decimal(str(c['difference'])) for c in state.get('calculations',[]) if c.get('code')=='REFUND'),Decimal(0))
    return {'count':len(invoices),'lines':sum(len(i['lines']) for i in invoices),
        'amount':str(amount),'tax':str(tax),'refund':str(refund),'balanced':amount==refund}
