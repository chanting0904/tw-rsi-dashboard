"""Continue the issued model from an immutable, verified upgrade snapshot."""
import copy
import json
import subprocess
from pathlib import Path
import pandas as pd
from .jsonio import document_digest, clean_json


def load_checkpoint(root, output_dir, before):
    seed = load_seed(root)
    paths = sorted((Path(output_dir)/'live_checkpoints').glob('*.json'))
    paths = [p for p in paths if p.stem < str(pd.Timestamp(before).date())]
    if not paths:
        return seed
    checkpoint = json.loads(paths[-1].read_text(encoding='utf-8'))
    digest = checkpoint.pop('checkpoint_digest', None)
    if digest != document_digest(checkpoint) or checkpoint['date'] != paths[-1].stem:
        raise ValueError('已發布持股快照損壞，停止產生計畫')
    previous = checkpoint['previous_signal']
    if (checkpoint['previous_signal_digest'] != document_digest(previous)
            or previous['rebalance_date'] != checkpoint['date']):
        raise ValueError('已發布持股快照與訊號不一致')
    return checkpoint


def checkpoint_document(result, signal, seed):
    state = result['closing_state']
    if state['date'] != signal['rebalance_date']:
        raise ValueError('持股快照必須對應調倉當日')
    checkpoint = dict(schema=1, strategy_version='V5', from_strategy='V5',
        from_release=signal['release_version'], effective_date=str((pd.Timestamp(state['date'])+pd.Timedelta(days=1)).date()),
        capital_basis=signal['capital_basis'], previous_signal=signal,
        previous_signal_digest=document_digest(signal), parent_digest=seed['previous_signal_digest'], **state)
    checkpoint['checkpoint_digest'] = document_digest(checkpoint)
    return checkpoint


def check_predecessor(output_dir, seed, before):
    candidates = []
    for name in ['latest_signals.json', 'previous_capital_signal.json']:
        path = Path(output_dir)/name
        if path.exists():
            value = json.loads(path.read_text(encoding='utf-8'))
            if value['rebalance_date'] < str(pd.Timestamp(before).date()):
                candidates.append(value)
    if candidates:
        previous = max(candidates, key=lambda x: x['rebalance_date'])
        if document_digest(previous) != seed['previous_signal_digest']:
            raise ValueError('上週已發布計畫缺少相符持股快照，停止重新建立不同持股')


def validate_published_carry(previous, current):
    quotes = {a['stock_code']:dict(price=a['price'],
        tradable=a.get('trading_status', 'TRADABLE') in ('TRADABLE', 'OUTSIDE_TARGET'),
        buyable=a.get('buy_allowed', True), sellable=a.get('sell_allowed', True)) for a in current['actions']}
    args = dict(previous=previous, current=current, previousCapital=100000, capital=100000, quotes=quotes)
    process = subprocess.run(['node', str(Path(__file__).with_name('guarded_bridge.cjs'))],
        input=json.dumps(clean_json(args))+'\n', capture_output=True, text=True, encoding='utf-8', check=True, timeout=30)
    reply = json.loads(process.stdout)
    if not reply['ok']:
        raise ValueError('已發布持股承接驗證失敗：'+reply['error'])


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
