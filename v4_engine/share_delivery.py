"""Source-verified delivery discovery; missing/ambiguous dates never unlock stock."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import datetime as dt
import json
import math
import re
from urllib.parse import urlencode
import pandas as pd
from .corporate_actions import delivery_registry

TWSE = 'https://www.twse.com.tw/rwd/zh/announcement/LT185'
TPEX = 'https://www.tpex.org.tw/www/zh-tw/bulletin/tdr'


def day(value):
    if value is None:return None
    parts=re.findall(r'\d+',str(value))
    if len(parts)!=3:return None
    y,m,d=map(int,parts)
    try:return dt.date(y+1911 if y<1911 else y,m,d).isoformat()
    except ValueError:return None


def quantity(value):
    try:
        n=float(str(value).replace(',','').strip())
        return n if math.isfinite(n) else 0
    except ValueError:return 0


def parse_announcements(payload, market, source, cutoff):
    if str(payload.get('stat','')).lower()!='ok':raise ValueError('Announcement response failed')
    if market=='twse':
        if not isinstance(payload.get('data'),list):raise ValueError('Missing TWSE announcement table')
        rows=payload['data']
    else:
        tables=payload.get('tables')
        if not tables or not isinstance(tables[0].get('data'),list):raise ValueError('Missing TPEx announcement table')
        rows=tables[0]['data']
        if tables[0].get('totalCount',len(rows))!=len(rows):raise ValueError('Incomplete announcement pagination')
    found=[]
    for row in rows:
        if not isinstance(row,list) or len(row)<(10 if market=='twse' else 11):raise ValueError('Malformed announcement row')
        if market=='twse':code,kind,subject,ann,trade,new,total=row[0],row[2],row[4],row[5],row[6],row[7],row[8]
        else:code,kind,subject,ann,trade,new,total=row[1],row[3],row[5],row[6],row[7],row[8],row[9]
        code=str(code).strip();ann=day(ann);trade=day(trade)
        certificate='新股權利證書' in kind and ('上市開始買賣' in subject or '上櫃開始買賣' in subject)
        if not re.fullmatch(r'[1-9]\d{3}',code) or not ('除權配股' in subject or certificate):continue
        if not ann or not trade:raise ValueError('Missing delivery date in dividend announcement')
        if ann>cutoff:continue
        issued,after=quantity(new),quantity(total)
        if issued<=0 or after<=issued:continue
        # Employee remuneration may be included in the headline issuance.
        employees=0
        if market=='twse':
            match=re.search(r'員工酬勞轉增資\s*([\d,]+)\s*股',str(row[9]))
            employees=quantity(match[1]) if match else 0
        if employees>=issued:continue
        found.append(dict(code=code,announced_on=ann,tradable_date=trade,issued=issued,
            ratio=1+(issued-employees)/(after-issued),
            security_form='RIGHT_CERTIFICATE' if certificate else 'ORDINARY_SHARE',
            subject=str(subject).strip(),source=source))
    return found


def merge_registered(*groups):
    merged={}
    for group in groups:
        delivery_registry(group)
        for row in group:
            key=(str(row['code']),str(pd.Timestamp(row['ex_date']).date()))
            if key in merged:
                a,b=delivery_registry([merged[key]])[key[0],pd.Timestamp(key[1])],delivery_registry([row])[key[0],pd.Timestamp(key[1])]
                if a['tradable_date']!=b['tradable_date'] or a['announced_on']!=b['announced_on']:
                    raise ValueError('Conflicting saved delivery records')
            else:merged[key]=row
    return list(merged.values())


def match_deliveries(events, announcements, registered, cutoff, start='2015-01-01'):
    result={(r['code'],str(pd.Timestamp(r['ex_date']).date())):r for r in registered}
    candidates={}
    for a in announcements:candidates.setdefault(a['code'],[]).append(a)
    conflicts=[];added=[]
    for ex,codes in sorted(events.items()):
        ex=str(pd.Timestamp(ex).date())
        if not start<=ex<=cutoff:continue
        for code,event in codes.items():
            if event.get('kind')!='STOCK_DIVIDEND' or not event.get('ratio',1)>1:continue
            key=(str(code),ex)
            matches=[a for a in candidates.get(str(code),[]) if 0<=(dt.date.fromisoformat(a['tradable_date'])-dt.date.fromisoformat(ex)).days<=180
                and abs(a['ratio']-event['ratio'])<=.005]
            if not matches:continue
            # Certificates and their later ordinary-share replacement may share
            # issuance size. Different sizes or conflicting schedules need review.
            amounts={a['issued'] for a in matches}
            forms={}
            for a in matches:forms.setdefault(a['security_form'],[]).append(a)
            ambiguous=len(amounts)>1
            for rows in forms.values():
                if len({a['tradable_date'] for a in rows})>1:ambiguous=True
            if ambiguous:
                conflicts.append(dict(code=code,ex_date=ex,reason='AMBIGUOUS_ANNOUNCEMENTS'));result.pop(key,None);continue
            chosen=min(matches,key=lambda a:(a['tradable_date'],a['announced_on']))
            row={k:v for k,v in chosen.items() if k not in ['issued','ratio']};row['ex_date']=ex
            if key in result:
                if str(pd.Timestamp(result[key]['tradable_date']).date())!=row['tradable_date']:
                    conflicts.append(dict(code=code,ex_date=ex,reason='SAVED_DATE_CONFLICT'));result.pop(key,None)
            else:result[key]=row;added.append(key)
    records=list(result.values());delivery_registry(records)
    return records,added,conflicts


def resolve_deliveries(events, registered, cutoff, *, start='2015-01-01', cache_dir=None, getter=None):
    cutoff=str(pd.Timestamp(cutoff).date());now=dt.datetime.now(dt.timezone.utc)
    years=sorted({pd.Timestamp(ex).year for ex,codes in events.items() if start<=str(pd.Timestamp(ex).date())<=cutoff
        and any(e.get('kind')=='STOCK_DIVIDEND' for e in codes.values())})
    def fetch(job):
        market,year=job;end=min(f'{year}-12-31',cutoff)
        params=(dict(startDate1=f'{year}0101',endDate1=end.replace('-',''),selectType='',stockNo='',response='json') if market=='twse'
            else dict(startDate='2015/12/01' if year==2015 else f'{year}/01/01',endDate=end.replace('-','/'),sect='',mkt='',code='',response='json',**{'paging-table':0,'paging-size':10000,'paging-offset':0}))
        url=(TWSE if market=='twse' else TPEX)+'?'+urlencode(params)
        cache=Path(cache_dir)/f'{market}_{year}.json' if cache_dir else None
        try:
            payload=None;cached=False
            if cache and cache.exists():
                try:
                    saved=json.loads(cache.read_text(encoding='utf-8'))
                    age=(now-dt.datetime.fromisoformat(saved['fetched_at'])).total_seconds()
                    if saved['source']==url and 0<=age<(21600 if year==int(cutoff[:4]) else 30*86400):payload=saved['payload'];cached=True
                except (OSError,ValueError,KeyError,TypeError):pass
            if payload is None:
                if getter:payload=getter(url)
                else:
                    import requests
                    response=requests.get(url,timeout=20);response.raise_for_status();payload=response.json()
            parsed=parse_announcements(payload,market,url,cutoff)
            if cache and not cached:
                from .jsonio import write_json
                write_json(cache,dict(source=url,fetched_at=now.isoformat(),payload=payload))
            return parsed,dict(market=market,year=year,source=url,ok=True,cached=cached,records=len(parsed))
        except Exception as error:
            return [],dict(market=market,year=year,source=url,ok=False,error=type(error).__name__)
    with ThreadPoolExecutor(max_workers=4) as pool:responses=list(pool.map(fetch,[(m,y) for y in years for m in ['twse','tpex']]))
    sources=[s for _,s in responses];announcements=[a for rows,_ in responses for a in rows]
    records,added,conflicts=match_deliveries(events,announcements,registered,cutoff,start)
    status=dict(as_of=cutoff,status='PASS' if all(s['ok'] for s in sources) and not conflicts else 'PARTIAL',
        sources=sources,discovered=len(added),registered=len(records),conflicts=conflicts,
        policy='Only verified dates unlock shares; unavailable or ambiguous dates remain locked.')
    return records,status


def save_delivery_snapshot(ctx, result, output_dir):
    from .jsonio import write_json
    wanted={(a['code'],a['date']) for a in result.get('corporate_action_audit',[]) if a['kind']=='STOCK_DIVIDEND'}
    records=[r for r in (getattr(ctx,'share_delivery_events',None) or []) if (r['code'],str(pd.Timestamp(r['ex_date']).date())) in wanted]
    write_json(Path(output_dir)/'share_delivery_events.json',dict(events=records))
    write_json(Path(output_dir)/'share_delivery_status.json',getattr(ctx,'share_delivery_status',None) or {})
