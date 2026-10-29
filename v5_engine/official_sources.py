"""Parse official exchange and MOPS responses at the data-store boundary."""
import datetime as dt
from io import StringIO
import json
import math
import re

import pandas as pd
import requests


def number(value):
    try:
        result = float(str(value).replace(',', '').strip())
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def date_string(value):
    if value is None or pd.isna(value) or str(value).strip() in ('', '--', 'nan', 'NaT'):
        return None
    parts = re.findall(r'\d+', str(value))
    if len(parts) == 1:
        text = parts[0]
        parts = [text[:-4], text[-4:-2], text[-2:]]
    if len(parts) != 3:
        raise ValueError('官方日期格式無法辨識：' + str(value))
    year, month, day = map(int, parts)
    return dt.date(year + 1911 if year < 1911 else year, month, day).isoformat()


def fetch(url, params=None, *, post=False):
    last = None
    for _ in range(2):
        try:
            response = (requests.post(url, data=params, timeout=20) if post
                        else requests.get(url, params=params, timeout=20))
            response.raise_for_status()
            return response.content
        except requests.RequestException as error:
            last = error
    raise last


def tables(document):
    if not isinstance(document, dict):
        raise ValueError('官方回應格式錯誤')
    if document.get('stat', document.get('status', '')).lower() not in ('ok', 'success'):
        if any(text in str(document) for text in ('沒有符合條件', '查無資料', '沒有相關資料')):
            return []
        raise ValueError('官方資料查詢未完成')
    return document.get('tables') or [document]


def quote_rows(document, market, day):
    available = tables(document)
    if not available:
        return []
    actual = date_string(document.get('date') or available[0].get('date'))
    if actual != day:
        raise ValueError(f'{market}行情日期不符：{actual} != {day}')
    selected = next((t for t in available if any(x in t.get('fields', []) for x in ('證券代號', '代號'))), None)
    if selected is None:
        raise ValueError('官方行情缺少股票欄位')
    if not selected.get('data') and selected.get('totalCount') == 0:
        return []
    fields = selected['fields']
    aliases = [('證券代號', '代號'), ('開盤價', '開盤'), ('最高價', '最高'),
               ('最低價', '最低'), ('收盤價', '收盤'), ('成交金額', '成交金額(元)')]
    indices = [next((fields.index(x) for x in choices if x in fields), None) for choices in aliases]
    if None in indices:
        raise ValueError('官方行情缺少必要欄位')
    result = []
    for row in selected.get('data', []):
        code = str(row[indices[0]]).strip()
        if not re.fullmatch(r'[1-9]\d{3}', code):
            continue
        values = [number(row[i]) for i in indices[1:]]
        if values[3] is None:
            continue
        if any(value is None or value <= 0 for value in values[:4]) or values[4] is None or values[4] < 0:
            raise ValueError('官方行情有不完整價格：' + code)
        if values[1] < max(values[0], values[2], values[3]) or values[2] > min(values[0], values[1], values[3]):
            raise ValueError('官方開高低收關係錯誤：' + code)
        result.append((day, code, *values))
    if len(result) < (700 if market == 'TWSE' else 400):
        raise ValueError('官方行情筆數不足：' + market)
    return result


def financial_tables(body, minimum=1):
    parsed = []
    for frame in pd.read_html(StringIO(body.decode('utf-8')), displayed_only=False):
        frame.columns = [re.sub(r'\s+', '', str(c[-1] if isinstance(c, tuple) else c)) for c in frame.columns]
        key = next((c for c in frame.columns if c in ('公司代號', '公司代码')), None)
        if key is None:
            continue
        for item in frame.to_dict('records'):
            code = str(item[key]).removesuffix('.0')
            if re.fullmatch(r'[1-9]\d{3}', code):
                parsed.append(dict(item, code=code))
    if len(parsed) < minimum:
        raise ValueError('官方財報不完整或被擋住')
    return parsed


def financial_value(row, kind):
    choices = {
        'profit': ['本期淨利（淨損）', '本期稅後淨利（淨損）'],
        'equity': ['權益總計', '權益總額'],
        'capital': ['股本', '普通股股本']}
    for key in choices[kind]:
        if key in row and number(row[key]) is not None:
            return number(row[key])
    return None


def quarter_roe(current_income, previous_income, current_equity, previous_equity, quarter):
    """MOPS income is cumulative; V5's ROE input is a single quarter."""
    if current_income is None or current_equity is None or previous_equity is None:
        return None
    if quarter != 1 and previous_income is None:
        return None
    profit = current_income if quarter == 1 else current_income - previous_income
    average = (current_equity + previous_equity) / 2
    return profit / average * 100 if average != 0 else None


def announcement_rows(body, cutoff):
    result = []
    for frame in pd.read_html(StringIO(body.decode('utf-8')), displayed_only=False):
        if '公司代號' not in str(frame.columns) or '除息交易日' not in str(frame.columns):
            continue
        frame.columns = [str(c[-1] if isinstance(c, tuple) else c) for c in frame.columns]
        for row in frame.to_dict('records'):
            code = str(row.get('公司代號')).removesuffix('.0')
            if not re.fullmatch(r'[1-9]\d{3}', code):
                continue
            ann = date_string(row.get('公告日期'))
            if not ann or ann > cutoff:
                continue
            clean = {k: None if pd.isna(v) else v for k, v in row.items()}
            for key in ('公告日期', '除息交易日', '除權交易日', '現金股利發放日', '權利分派基準日'):
                clean[key] = date_string(row.get(key))
            clean['symbol'] = code
            result.append(clean)
    if len(result) < 200:
        raise ValueError('官方除權息公告筆數不足')
    return result
