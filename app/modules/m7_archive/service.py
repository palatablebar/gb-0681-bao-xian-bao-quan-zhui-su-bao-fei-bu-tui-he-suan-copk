from app.modules.m7_archive.export_out import checksum

def execute(state):
    reconcile=state.get('reconcile',{})
    ready=reconcile.get('balanced',False) and reconcile.get('invoices',{}).get('balanced',True)
    state['archive']={'sha256':checksum(state),'evidence_count':len(state.get('calculations',[]))+len(state['quarantine']),
        'invoices':len(state.get('invoices',[])),'ready':ready}
    return state
