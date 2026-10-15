"""Reject source releases that omit the human-readable version bump."""
import os
from pathlib import Path
import re
import subprocess
from version import RELEASE_VERSION

ROOT=Path(__file__).parent
def check(base):
    previous=subprocess.check_output(['git','show',f'{base}:VERSION_HISTORY.txt'],cwd=ROOT,stderr=subprocess.DEVNULL,text=True,encoding='utf-8') if subprocess.run(['git','cat-file','-e',f'{base}:VERSION_HISTORY.txt'],cwd=ROOT,stderr=subprocess.DEVNULL).returncode==0 else None
    files=subprocess.check_output(['git','diff','--name-only',base,'HEAD'],cwd=ROOT,text=True,encoding='utf-8').splitlines()
    source=[p for p in files if p.endswith(('.py','.js','.cjs','.yml','.yaml')) and not p.startswith(('optimization_','research_','release_audit/'))]
    if not source:return
    if 'VERSION_HISTORY.txt' not in files:raise ValueError('正式程式修改必須更新 VERSION_HISTORY.txt')
    if previous:
        old=previous.splitlines()[0].strip()
        parse=lambda s:tuple(map(int,re.findall(r'\d+',s)))
        if parse(RELEASE_VERSION)<=parse(old):raise ValueError(f'版本必須遞增：{old} → {RELEASE_VERSION}')
    print('VERSION CHECK PASS',RELEASE_VERSION)

if __name__=='__main__':
    event=os.environ.get('GITHUB_EVENT_PATH')
    import json
    info=json.loads(Path(event).read_text()) if event else {}
    base=info.get('pull_request',{}).get('base',{}).get('sha') or info.get('before')
    if base and set(base)!={'0'}:check(base)
    else:print('Version format valid:',RELEASE_VERSION)
