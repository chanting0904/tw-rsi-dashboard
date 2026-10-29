"""Immutable historical snapshots and transactional official-market increments."""
import hashlib
import io
import json
import sqlite3
import zlib
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd

PRICE_NAMES = ('收盤價', '開盤價', '最高價', '最低價', '成交金額')
CORE = {'price:收盤價', 'price:成交金額', 'etl:adj_close'}
DATASETS = [*('price:' + x for x in PRICE_NAMES), 'etl:adj_close',
    'fundamental_features:ROE稅後', 'financial_statement:股本', 'company_basic_info',
    'etl:financial_statements_deadline',
    'dividend_announcement', 'dividend_tse:權息', 'dividend_tse:除權息前收盤價',
    'dividend_tse:除權息參考價', 'dividend_otc:現金股利', 'dividend_otc:每千股無償配股',
    'capital_reduction_tse:減資原因', 'capital_reduction_tse:停止買賣前收盤價格',
    'capital_reduction_tse:恢復買賣參考價', 'capital_reduction_otc:減資原因',
    'capital_reduction_otc:最後交易之收盤價格', 'capital_reduction_otc:減資恢復買賣開始日參考價格']


def bootstrap(cache, directory):
    """Import owned snapshots once, without authentication or remote downloads."""
    cache, directory = Path(cache), Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    expected = [directory / 'history_prices.sqlite3', directory / 'history_reference.sqlite3']
    if any(p.exists() for p in expected):
        if not all(p.exists() for p in expected):
            raise ValueError('歷史資料庫不完整，請從備份還原')
        return
    for name in DATASETS:
        if not (cache / (name.replace(':', '#') + '.feather')).is_file():
            raise ValueError('缺少歷史快照：' + name)
    connections = []
    try:
        for path in expected:
            db = sqlite3.connect(str(path) + '.tmp')
            db.execute('CREATE TABLE frames(name TEXT PRIMARY KEY, sha TEXT NOT NULL, body BLOB NOT NULL)')
            connections.append(db)
        for name in DATASETS:
            raw = (cache / (name.replace(':', '#') + '.feather')).read_bytes()
            connections[0 if name in CORE else 1].execute('INSERT INTO frames VALUES(?,?,?)',
                (name, hashlib.sha256(raw).hexdigest(), zlib.compress(raw, 9)))
        for db in connections:
            db.commit()
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('歷史資料庫完整性檢查失敗')
            db.close()
        for path in expected:
            Path(str(path) + '.tmp').replace(path)
    finally:
        for db in connections:
            try:
                db.close()
            except sqlite3.Error:
                pass


class MarketStore:
    def __init__(self, root):
        self.directory = Path(root) / 'local_db'
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / 'official.sqlite3'
        initialize = not path.exists()
        self.db = sqlite3.connect(path)
        if initialize:
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS bars(day TEXT,code TEXT,open REAL,high REAL,low REAL,close REAL,amount REAL,source TEXT,PRIMARY KEY(day,code));
            CREATE TABLE IF NOT EXISTS values_by_date(dataset TEXT,day TEXT,code TEXT,value,source TEXT,PRIMARY KEY(dataset,day,code));
            CREATE TABLE IF NOT EXISTS references_by_date(day TEXT,code TEXT,before REAL,reference REAL,source TEXT,PRIMARY KEY(day,code));
            CREATE TABLE IF NOT EXISTS announcements(identity TEXT PRIMARY KEY,body TEXT,source TEXT);
            CREATE TABLE IF NOT EXISTS documents(identity TEXT PRIMARY KEY,checked_at TEXT,source TEXT,sha TEXT,body BLOB);
            CREATE TABLE IF NOT EXISTS receipts(day TEXT PRIMARY KEY,body TEXT);
            ''')
        self._frames = {}

    def close(self):
        self.db.close()

    def base(self, name):
        if name not in self._frames:
            filename = 'history_prices.sqlite3' if name in CORE else 'history_reference.sqlite3'
            path = self.directory / filename
            if not path.is_file():
                raise ValueError('尚未建立歷史資料庫：' + str(path))
            with closing(sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)) as db:
                row = db.execute('SELECT sha,body FROM frames WHERE name=?', (name,)).fetchone()
            if row is None:
                raise ValueError('歷史資料庫缺欄位：' + name)
            raw = zlib.decompress(row[1])
            if hashlib.sha256(raw).hexdigest() != row[0]:
                raise ValueError('歷史快照雜湊不符：' + name)
            frame = pd.read_feather(io.BytesIO(raw))
            if ':' in name:
                if name != 'etl:financial_statements_deadline':
                    frame['date'] = pd.to_datetime(frame['date'])
                frame = frame.set_index('date').sort_index()
                if not frame.index.is_unique:
                    raise ValueError('歷史資料日期重複：' + name)
                from finlab.data.get import _normalize_stock_columns
                frame = _normalize_stock_columns(frame)
            self._frames[name] = frame
        return self._frames[name].copy()

    def financial(self, name):
        # The saved raw index is a period label, not its per-company availability.
        # Reuse the pinned SDK's pure normalization, with our own saved date keys.
        from finlab.data.get import process_data
        values = pd.DataFrame(process_data(name, self.base(name).reset_index()))
        keys = self.base('etl:financial_statements_deadline')
        columns = values.columns.intersection(keys.columns)
        values = values[columns]
        dates = keys.reindex(values.index)[columns].unstack()
        flat = values.unstack()
        joined = pd.DataFrame({'value': flat.values, 'available': pd.to_datetime(dates.values)}, index=flat.index).reset_index()
        joined.columns = ['code', 'period', 'value', 'available']
        joined = joined[joined['available'].notna()]
        frame = joined.pivot(index='available', columns='code', values='value').ffill()
        sessions = self.base('price:收盤價').index
        mapped = []
        for day in frame.index:
            position = sessions.searchsorted(day)
            mapped.append(sessions[position] if position < len(sessions) else pd.offsets.BDay().rollforward(day))
        frame.index = pd.DatetimeIndex(mapped, name='date')
        return frame.groupby(level=0).last().loc[:self.baseline_day]

    @property
    def baseline_day(self):
        return self.base('price:收盤價').index.max().date().isoformat()

    def get(self, name):
        """Return the existing engine's frame contract, without any FinLab I/O."""
        from finlab.dataframe import FinlabDataFrame
        if name == 'etl:adj_close':
            frame = self.adjusted()
        else:
            frame = (self.financial(name) if name in ('fundamental_features:ROE稅後', 'financial_statement:股本') else self.base(name))
            if name.startswith('price:'):
                field = dict(zip(PRICE_NAMES, ('close', 'open', 'high', 'low', 'amount')))[name[6:]]
                rows = self.db.execute(f'SELECT day,code,{field} FROM bars ORDER BY day,code').fetchall()
                frame = overlay(frame, rows)
            elif name == 'dividend_announcement':
                frame['symbol'] = frame['stock_id'].astype(str)
                additions = [json.loads(row[0]) for row in self.db.execute('SELECT body FROM announcements')]
                if additions:
                    frame = pd.concat([frame, pd.DataFrame(additions)], ignore_index=True)
            elif ':' in name:
                rows = self.db.execute('SELECT day,code,value FROM values_by_date WHERE dataset=? ORDER BY day,code', (name,)).fetchall()
                frame = overlay(frame, rows)
        return FinlabDataFrame(frame)

    def adjusted(self):
        base = self.base('etl:adj_close')
        raw_base = self.base('price:收盤價')
        raw = pd.DataFrame(self.get('price:收盤價'))
        cutoff = raw_base.index.max()
        ratio = (base / raw_base).ffill().loc[cutoff].reindex(raw.columns)
        ratio = ratio.fillna(1.)
        refs = {}
        for day, code, before, reference in self.db.execute('SELECT day,code,before,reference FROM references_by_date ORDER BY day'):
            refs.setdefault(pd.Timestamp(day), {})[code] = before / reference
        additions = []
        pending = iter(sorted(refs))
        next_reference = next(pending, None)
        for day in raw.index[raw.index > cutoff]:
            while next_reference is not None and next_reference <= day:
                for code, change in refs[next_reference].items():
                    if code in ratio.index:
                        ratio[code] *= change
                next_reference = next(pending, None)
            additions.append(pd.DataFrame([raw.loc[day] * ratio], index=[day]))
        return pd.concat([base, *additions]).reindex(columns=raw.columns) if additions else base

    def save_document(self, identity, checked_at, source, body):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?)',
            (identity, checked_at, source, hashlib.sha256(raw).hexdigest(), zlib.compress(raw)))


def overlay(frame, rows):
    if not rows:
        return frame
    extra = pd.DataFrame(rows, columns=['date', 'code', 'value'])
    extra['date'] = pd.to_datetime(extra['date'])
    extra = extra.pivot(index='date', columns='code', values='value')
    result = frame.reindex(index=frame.index.union(extra.index), columns=frame.columns.union(extra.columns))
    if any(isinstance(row[2], str) for row in rows):
        result = result.astype(object)
    for day in extra.index:
        valid = extra.loc[day].notna()
        result.loc[day, extra.columns[valid]] = extra.loc[day, valid]
    return result.sort_index()
