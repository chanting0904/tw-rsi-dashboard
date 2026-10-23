"""Date-aware official exchange status checks; historical snapshots never filter earlier years."""
from concurrent.futures import ThreadPoolExecutor
import datetime
from functools import lru_cache
import json
import hashlib
from pathlib import Path
import re
import urllib.request
from zoneinfo import ZoneInfo
import pandas as pd

SOURCES = {
    'twse_listed': 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L',
    'tpex_listed': 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O',
    'twse_delisted': 'https://openapi.twse.com.tw/v1/company/suspendListingCsvAndHtml',
    'twse_stopped': 'https://www.twse.com.tw/rwd/zh/violation/stop?response=json',
    'twse_halts': 'https://openapi.twse.com.tw/v1/exchangeReport/TWTAWU',
    'tpex_stopped': 'https://www.tpex.org.tw/openapi/v1/tpex_cmode',
    'tpex_halts': 'https://www.tpex.org.tw/openapi/v1/tpex_spendi_history',
}


def date_string(value):
    if isinstance(value, (datetime.date, datetime.datetime, pd.Timestamp)):
        return pd.Timestamp(value).strftime('%Y-%m-%d')
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    parts = re.findall(r'\d+', text)
    if len(parts) == 1:
        number = parts[0]
        if len(number) not in (7, 8):
            raise ValueError('Unrecognized exchange date: '+text)
        parts = [number[:-4], number[-4:-2], number[-2:]]
    if len(parts) != 3:
        raise ValueError('Unrecognized exchange date: '+text)
    year, month, day = map(int, parts)
    if year < 1911:
        year += 1911
    return datetime.date(year, month, day).isoformat()


def fetch_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'V5-security-status', 'Accept': 'application/json',
                                                  'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if not isinstance(result, (dict, list)):
        raise ValueError('Invalid exchange response')
    return result


def table_rows(document):
    if isinstance(document, list):
        return document
    if document.get('stat', document.get('status', '')).lower() != 'ok':
        raise ValueError('Exchange table response is not successful')
    if 'tables' in document:
        table = document['tables'][0]
        rows = table.get('data', [])
        if int(table.get('totalCount', len(rows))) != len(rows):
            raise ValueError('Exchange response is paginated or incomplete')
        return rows
    rows = document.get('data', [])
    if int(document.get('totalCount',len(rows))) != len(rows):
        raise ValueError('Exchange response is paginated or incomplete')
    return rows


def build_snapshot(payloads, checked_for, quote_day, sources=None):
    sources = sources or SOURCES
    checked_for, quote_day = date_string(checked_for), date_string(quote_day)
    listed, events, source_dates = {}, [], {}

    def add(code, day, status, name, source, label='', observed=None, time='000000'):
        if day and re.fullmatch(r'\d{4}', str(code)):
            events.append({'code': str(code), 'effective_date': day, 'status': status, 'name': name,
                           'source': source, 'label': label or status, 'observed_on': observed,
                           'effective_time': str(time or '000000').replace(':', '').zfill(6)})

    for exchange, key, code_field, name_field, date_field, listing_field, minimum in [
        ('TWSE', 'twse_listed', '公司代號', '公司簡稱', '出表日期', '上市日期', 500),
        ('TPEX', 'tpex_listed', 'SecuritiesCompanyCode', 'CompanyAbbreviation', 'Date', 'DateOfListing', 300),
    ]:
        rows = payloads[key]
        if not isinstance(rows, list) or len(rows) < minimum:
            raise ValueError('Incomplete official listed company universe: '+key)
        if len({str(row[code_field]) for row in rows})<minimum:
            raise ValueError('Duplicate or incomplete official listed company universe: '+key)
        dates = {date_string(row[date_field]) for row in rows}
        if len(dates) != 1 or min(dates) < quote_day:
            raise ValueError('Stale or mixed-date official company universe: '+key)
        source_dates[key] = min(dates)
        for row in rows:
            code = str(row[code_field])
            if not re.fullmatch(r'\d{4}', code):
                continue
            listed[code] = {'name': row[name_field], 'exchange': exchange}
            add(code, date_string(row.get(listing_field)), 'TRADABLE', row[name_field], sources[key], '上市櫃')

    for row in payloads['twse_delisted']:
        add(row['Code'], date_string(row['DelistingDate']), 'DELISTED', row['Company'], sources['twse_delisted'], '終止上市')
    for key, document in payloads.items():
        if key.startswith('tpex_delisted_'):
            for row in table_rows(document):
                reason = re.sub(r'\s+', '', str(row[3]))
                # Transfer from TPEx to TWSE is an exchange change, not a loss.
                transfer = '第12條之2第1項第1款' in reason
                add(row[0], date_string(row[2]), 'TRADABLE' if transfer else 'DELISTED', row[1],
                    sources[key], '轉上市' if transfer else '終止上櫃')

    for row in table_rows(payloads['twse_stopped']):
        add(row[0], date_string(row[-1]), 'SUSPENDED', row[1], sources['twse_stopped'], '停止買賣')
    for row in payloads['tpex_stopped']:
        if str(row.get('SuspensionOfTrading', '')).strip() in ('Y', 'Ｙ'):
            add(row['SecuritiesCompanyCode'], checked_for, 'SUSPENDED', row['CompanyName'],
                sources['tpex_stopped'], '停止交易（本次官方狀態）', observed=checked_for)

    for key, start_field, end_field, code_field, name_field in [
        ('twse_halts', 'TradingHaltDate', 'TradingResumptionDate', 'Code', 'Name'),
        ('tpex_halts', 'DateOfSuspendedTrading', 'DateOfResumedTrading', 'SecuritiesCompanyCode', 'CompanyName'),
    ]:
        rows = payloads[key]
        if not isinstance(rows, list):
            raise ValueError('Invalid official suspension history')
        for row in rows:
            start_time = 'TradingHaltTime' if key == 'twse_halts' else 'TimeOfSuspendedTrading'
            end_time = 'TradingResumptionTime' if key == 'twse_halts' else 'TimeOfResumedTrading'
            add(row[code_field], date_string(row.get(start_field)), 'SUSPENDED', row[name_field], sources[key], '暫停交易', time=row.get(start_time))
            add(row[code_field], date_string(row.get(end_field)), 'TRADABLE', row[name_field], sources[key], '恢復交易', time=row.get(end_time))

    unique = {tuple(e.get(k) for k in ['code', 'effective_date', 'effective_time', 'status', 'source']): e for e in events}
    result = {'checked_for': checked_for, 'quote_day': quote_day, 'listed': listed,
              'source_dates': source_dates, 'events': sorted(unique.values(), key=lambda e: (e['code'], e['effective_date'], e['status'])),
              'sources': sources, 'observed_at': datetime.datetime.now(ZoneInfo('Asia/Taipei')).isoformat()}
    return result


def refresh_security_status(path, checked_for, quote_day, *, getter=fetch_json):
    year = int(date_string(checked_for)[:4])
    sources = dict(SOURCES)
    for y in range(2015, year+1):
        sources['tpex_delisted_'+str(y)] = f'https://www.tpex.org.tw/www/zh-tw/company/deListed?date={y}&paging-table=0&paging-size=10000&paging-offset=0&response=json'
    with ThreadPoolExecutor(max_workers=6) as pool:
        values = list(pool.map(getter, sources.values()))
    snapshot = build_snapshot(dict(zip(sources, values)), checked_for, quote_day, sources)
    if path is not None:
        from .jsonio import write_json
        write_json(Path(path), snapshot)
    return snapshot


@lru_cache(maxsize=1)
def manual_events():
    result = []
    document = json.loads(Path(__file__).with_name('security_events.json').read_text(encoding='utf-8'))
    for code, events in document.items():
        result.extend(dict(event, code=code) for event in events)
    return result


def events_by_code(ctx):
    snapshot = getattr(ctx, 'security_status', None)
    cached = getattr(ctx, '_security_event_index', None)
    if cached is not None and cached[0] is snapshot:
        return cached[1]
    result = {}
    for event in manual_events()+(snapshot or {}).get('events', []):
        result.setdefault(event['code'], []).append(event)
    for events in result.values():
        events.sort(key=lambda e: (e['effective_date'], e.get('effective_time', '000000'),
                                  {'TRADABLE': 0, 'SUSPENDED': 1, 'DELISTED': 2}.get(e['status'], 0)))
    ctx._security_event_index = (snapshot, result)
    return result


def status_at(ctx, code, day):
    day = pd.Timestamp(day).strftime('%Y-%m-%d')
    snapshot = getattr(ctx, 'security_status', None) or {}
    relevant = [e for e in events_by_code(ctx).get(str(code), [])
                if (e['effective_date'], e.get('effective_time', '000000')) <= (day, '133000')]
    result = relevant[-1] if relevant else {'status': 'TRADABLE', 'label': '', 'source': None, 'name': None}
    if snapshot and day >= snapshot['checked_for'] and str(code) not in snapshot['listed'] and result['status'] == 'TRADABLE':
        result = {'status': 'UNVERIFIED_LISTING', 'label': '不在本次官方上市櫃名單，停止配置',
                  'source': 'official_company_universe', 'name': None}
    return result


def can_trade(ctx, code, day):
    return status_at(ctx, code, day)['status'] == 'TRADABLE'


def snapshot_hash(snapshot):
    return hashlib.sha256(json.dumps(snapshot,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
