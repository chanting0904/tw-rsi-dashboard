"""Refresh the local official database before scheduled V5 calculations."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from publish_gate import is_trading, load_holidays, prev_trading_day_str
from v5_engine.jsonio import write_json
from v5_engine.market_store import MarketStore, bootstrap
from v5_engine.official_sources import (announcement_rows, date_string, fetch,
    financial_tables, financial_value, number, quarter_roe, quote_rows, tables)

ROOT = Path(__file__).resolve().parent
MOPS = 'https://mopsov.twse.com.tw/mops/web/ajax_'


def quarter_before(year, quarter):
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def latest_quarter(day):
    year = day.year
    for y, q, month, date in [(year, 3, 10, 31), (year, 2, 8, 14),
                             (year, 1, 4, 30), (year - 1, 4, 3, 31)]:
        if day >= dt.date(year, month, date):
            return y, q
    return year - 1, 3


def financial_effective_date(company, year, quarter, holidays):
    name = str(company.get('公司名稱', ''))
    short = str(company.get('公司簡稱', ''))
    insurance = '保險' in name and '控股' not in name
    financial = '金融' in str(company.get('產業類別', ''))
    month, day = {1: (5, 15), 2: (8, 14), 3: (11, 14), 4: (3, 31)}[quarter]
    if insurance and quarter in (1, 3):
        month, day = (4, 30) if quarter == 1 else (10, 31)
    if quarter == 2 and (financial or insurance or (year >= 2021 and '-KY' in short)):
        month, day = 8, 31
    date = dt.date(year + (quarter == 4), month, day)
    while not is_trading(date, holidays):
        date += dt.timedelta(days=1)
    return date


def refresh(root=ROOT, through=None, *, getter=fetch):
    now = dt.datetime.now(ZoneInfo('Asia/Taipei'))
    today = now.date()
    through = through or (today.isoformat() if now.hour * 60 + now.minute >= 810
        and is_trading(today, load_holidays()) else prev_trading_day_str(today, load_holidays()))
    if through > today.isoformat():
        raise ValueError('不能更新尚未發生的交易日')
    store = MarketStore(root)
    cutoff = store.baseline_day
    checked_at = now.isoformat()
    receipt = dict(through=through, historical_through=cutoff, checked_at=checked_at, sources=[])
    def request(identity, url, params=None, post=False):
        body = getter(url, params, post=post)
        return identity, url, body
    def keep(response):
        identity, source, body = response
        store.save_document(identity, checked_at, source, body)
        receipt['sources'].append(dict(name=identity, source=source, sha256=hashlib.sha256(body).hexdigest()))
        return body
    try:
        latest = store.db.execute('SELECT MAX(day) FROM bars').fetchone()[0] or cutoff
        if latest > through:
            raise ValueError('不得用較早日期覆寫本地資料更新紀錄')
        start = min(latest, through)
        days = [d.date().isoformat() for d in pd.date_range(pd.Timestamp(start) + pd.Timedelta(days=1), through)
                if is_trading(d.date(), load_holidays())]
        if through > cutoff and through not in days:
            days.append(through)
        # Collect everything before the transaction, so a failed source cannot
        # promote a partial market day or a half-updated corporate-action batch.
        jobs = []
        for day in days:
            jobs.extend([(day + ':TWSE', 'https://www.twse.com.tw/exchangeReport/MI_INDEX',
                {'response': 'json', 'type': 'ALLBUT0999', 'date': day.replace('-', '')}, False),
                (day + ':TPEx', 'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes',
                {'date': day.replace('-', '/'), 'response': 'json', 'paging-table': 0,
                 'paging-size': 10000, 'paging-offset': 0}, False)])
        with ThreadPoolExecutor(max_workers=4) as pool:
            quotes = list(pool.map(lambda x: request(*x), jobs))
        batches = {}
        for response in quotes:
            identity = response[0]
            day, market = identity.split(':')
            batches.setdefault(day, {})[market] = quote_rows(json.loads(response[2]), market, day)
        for day, markets in batches.items():
            if bool(markets['TWSE']) != bool(markets['TPEx']):
                raise ValueError('僅有單一市場行情，停止更新：' + day)
        if through > cutoff and through not in batches:
            found = store.db.execute('SELECT COUNT(*) FROM bars WHERE day=?', (through,)).fetchone()[0]
            if found < 1000:
                raise ValueError('缺少訊號日完整行情：' + through)
        if through in batches and not batches[through]['TWSE']:
            raise ValueError('訊號日尚無官方收盤行情：' + through)

        begin = (pd.Timestamp(cutoff) + pd.Timedelta(days=1)).strftime('%Y%m%d')
        end = through.replace('-', '')
        jobs = [
            ('ex:TWSE', 'https://www.twse.com.tw/exchangeReport/TWT49U',
                {'response': 'json', 'startDate': begin, 'endDate': end}, False),
            ('reduction:TWSE', 'https://www.twse.com.tw/exchangeReport/TWTAUU',
                {'response': 'json', 'startDate': begin, 'endDate': end}, False),
            ('ex:TPEx', 'https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ',
                {'startDate': begin[:4] + '/' + begin[4:6] + '/' + begin[6:],
                 'endDate': through.replace('-', '/'), 'response': 'json'}, False),
            ('reduction:TPEx', 'https://www.tpex.org.tw/www/zh-tw/bulletin/decap',
                {'startDate': begin[:4] + '/' + begin[4:6] + '/' + begin[6:],
                 'endDate': through.replace('-', '/'), 'response': 'json'}, False),
        ]
        for market in ['sii', 'otc']:
            jobs.append(('ann:' + market, MOPS + 't108sb27', {'step': '1', 'firstin': 'true',
                'off': '1', 'TYPEK': market, 'year': str(int(through[:4]) - 1911),
                'month': '', 'b_date': '', 'e_date': '', 'type': '1'}, True))
        with ThreadPoolExecutor(max_workers=4) as pool:
            actions = list(pool.map(lambda x: request(*x), jobs))

        with store.db:
            for response in quotes:
                keep(response)
            for day, markets in batches.items():
                for market, rows in markets.items():
                    store.db.executemany('INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?)',
                        [(*row, market) for row in rows])
            for response in actions:
                identity, source, _ = response
                body = keep(response)
                if identity.startswith('ann:'):
                    for row in announcement_rows(body, through):
                        future = max(row.get('除息交易日') or '', row.get('除權交易日') or '')
                        if future <= cutoff:
                            continue
                        key = json.dumps([row['symbol'], row['公告日期'], row.get('股利所屬期間'), row.get('權利分派基準日')], ensure_ascii=False)
                        store.db.execute('INSERT OR REPLACE INTO announcements VALUES(?,?,?)', (key, json.dumps(row, ensure_ascii=False), source))
                else:
                    save_references(store, json.loads(body), identity, source, cutoff, through)
            update_financials(store, root, through, checked_at, getter, receipt)
            receipt.update(status='PASS', latest_price_date=through,
                market_rows=store.db.execute('SELECT COUNT(*) FROM bars WHERE day=?', (through,)).fetchone()[0])
            store.db.execute('INSERT OR REPLACE INTO receipts VALUES(?,?)', (through, json.dumps(receipt, ensure_ascii=False)))
        write_json(Path(root) / 'output/data_status.json', receipt)
        print('本地官方資料更新完成', through, receipt['market_rows'], '檔')
        return receipt
    finally:
        store.close()


def save_references(store, document, identity, source, cutoff, through):
    for table in tables(document):
        fields = table.get('fields', [])
        for values in table.get('data', []):
            row = dict(zip(fields, values))
            day = date_string(row.get('恢復買賣日期') or next((v for k, v in row.items() if '日期' in k or k == '資料日期'), None))
            code = str(next((v for k, v in row.items() if k in ('股票代號', '證券代號', '代號')), '')).strip()
            if not code.isdigit() or len(code) != 4 or code.startswith('0') or not day or not cutoff < day <= through:
                continue
            before = next((number(v) for k, v in row.items() if ('前收盤' in k or '最後交易' in k)), None)
            reference = next((number(v) for k, v in row.items() if '參考價' in k and ('恢復' in k or '除權息' in k)), None)
            if before is None or reference is None or min(before, reference) <= 0:
                raise ValueError('公司行動缺少有效參考價：' + code)
            store.db.execute('INSERT OR REPLACE INTO references_by_date VALUES(?,?,?,?,?)', (day, code, before, reference, source))
            market = 'tse' if identity.endswith('TWSE') else 'otc'
            if identity.startswith('ex:'):
                if market == 'tse':
                    kind = str(row.get('權/息', row.get('權息', '')))
                    updates = [('dividend_tse:權息', kind), ('dividend_tse:除權息前收盤價', before), ('dividend_tse:除權息參考價', reference)]
                else:
                    cash = next((number(v) for k, v in row.items() if k in ('現金股利', '息值')), None)
                    stock = next((number(v) for k, v in row.items() if '無償配股' in k), None)
                    updates = [('dividend_otc:現金股利', cash), ('dividend_otc:每千股無償配股', stock)]
            else:
                reason = next((str(v) for k, v in row.items() if '減資原因' in k), '')
                names = ('停止買賣前收盤價格', '恢復買賣參考價') if market == 'tse' else ('最後交易之收盤價格', '減資恢復買賣開始日參考價格')
                updates = [('capital_reduction_' + market + ':減資原因', reason),
                    ('capital_reduction_' + market + ':' + names[0], before),
                    ('capital_reduction_' + market + ':' + names[1], reference)]
            store.db.executemany('INSERT OR REPLACE INTO values_by_date VALUES(?,?,?,?,?)',
                [(name, day, code, value, source) for name, value in updates if value is not None])


def update_financials(store, root, through, checked_at, getter, receipt):
    year, quarter = latest_quarter(dt.date.fromisoformat(through))
    prior_year, prior_quarter = quarter_before(year, quarter)
    periods = [(year, quarter), (prior_year, prior_quarter)]
    frames = {}
    companies = {str(r['stock_id']): r for r in store.base('company_basic_info').to_dict('records')}
    for market in ['sii', 'otc']:
        for y, q in periods:
            for kind in ['income', 'balance']:
                key = f'financial:{market}:{y}Q{q}:{kind}'
                saved = store.db.execute('SELECT body FROM documents WHERE identity=?', (key,)).fetchone()
                if saved:
                    import zlib
                    body = zlib.decompress(saved[0])
                else:
                    source = MOPS + ('t163sb04' if kind == 'income' else 't163sb05')
                    body = getter(source, {'step': '1', 'firstin': '1', 'off': '1', 'isQuery': 'Y',
                        'TYPEK': market, 'year': str(y - 1911), 'season': f'{q:02d}'}, post=True)
                    financial_tables(body)
                    store.save_document(key, checked_at, source, body)
                frames[market, y, q, kind] = {r['code']: r for r in financial_tables(body)}
        # Never replace a frozen historical ROE with a newly fetched/revised report.
        current = frames[market, year, quarter, 'income']
        old = frames[market, prior_year, prior_quarter, 'income']
        equity = frames[market, year, quarter, 'balance']
        prior_equity = frames[market, prior_year, prior_quarter, 'balance']
        for code, income in current.items():
            effective = financial_effective_date(companies.get(code, {}), year, quarter, load_holidays())
            if not store.baseline_day < effective.isoformat() <= through:
                continue
            cap = financial_value(equity.get(code, {}), 'capital')
            roe = quarter_roe(financial_value(income, 'profit'), financial_value(old.get(code, {}), 'profit'),
                financial_value(equity.get(code, {}), 'equity'), financial_value(prior_equity.get(code, {}), 'equity'), quarter)
            if roe is None or cap is None:
                continue
            for name, value in [('fundamental_features:ROE稅後', roe), ('financial_statement:股本', cap)]:
                store.db.execute('INSERT OR REPLACE INTO values_by_date VALUES(?,?,?,?,?)',
                    (name, effective.isoformat(), code, value, MOPS))
    receipt['financial_period'] = f'{year}Q{quarter}'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--through')
    parser.add_argument('--bootstrap', type=Path)
    args = parser.parse_args()
    if args.bootstrap:
        bootstrap(args.bootstrap, ROOT / 'local_db')
    refresh(through=args.through)
