from app.modules.m6_reconcile.compare import balance,invoice_balance
from app.modules.m6_reconcile.invoice import build

def execute(state):
    state['invoices']=build(state)
    state['reconcile']=balance(state)
    state['reconcile']['invoices']=invoice_balance(state)
    return state
