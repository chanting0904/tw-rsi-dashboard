"""Continue the issued model from an immutable, verified upgrade snapshot."""
import copy
import json
from pathlib import Path
import pandas as pd
from .jsonio import document_digest


def load_seed(root):
    path=Path(root)/'live_model_seed.json'
    if not path.exists():
        return None
    seed=json.loads(path.read_text(encoding='utf-8'))
    if seed.get('schema')!=1 or seed.get('strategy_version')!='V5':
        raise ValueError('Unsupported live model seed')
    previous=seed['previous_signal']
    if previous['rebalance_date']!=seed['date'] or document_digest(previous)!=seed['previous_signal_digest']:
        raise ValueError('Live predecessor does not match seed digest')
    if not pd.Timestamp(seed['date'])<pd.Timestamp(seed['effective_date']):
        raise ValueError('Invalid live model activation date')
    return seed


def restore_seed(seed,ledger):
    positions=copy.deepcopy(seed['positions'])
    for pos in positions.values():
        pos['entry_d']=pd.Timestamp(pos['entry_d'])
        if not 0<=pos.get('locked_shares',0)<=pos['shares'] or int(pos['shares'])!=pos['shares']:
            raise ValueError('Invalid opening model shares')
    ledger.receivables=copy.deepcopy(seed['receivables'])
    for row in ledger.receivables:
        for key in ('payment','known_on'):
            if row.get(key) is not None:row[key]=pd.Timestamp(row[key])
    ledger.pending=copy.deepcopy(seed['pending'])
    for row in ledger.pending:row['ex_date']=pd.Timestamp(row['ex_date'])
    for code,pos in positions.items():
        locked=sum(row['shares'] for row in ledger.pending if row['code']==code)
        if locked!=pos.get('locked_shares',0):raise ValueError('Opening locked stock rights do not reconcile')
    records=[dict(date=pd.Timestamp(seed['date']),code=code,pool=pos['pool'],side='SEED',price=pos['avg'],
                  shares=pos['shares'],amount=pos['shares']*pos['avg'],rank=None,reason='MODEL_MIGRATION_SEED',
                  full=False,is_new=False,commission=0.,tax=0.,fee=0.) for code,pos in positions.items() if pos['shares']]
    return positions,float(seed['cash']),records
