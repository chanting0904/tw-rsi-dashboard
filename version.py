"""Release identity comes from the first line of the human-readable changelog."""
from pathlib import Path
import re

STRATEGY_VERSION = 'V5'
RELEASE_VERSION = (Path(__file__).parent / 'VERSION_HISTORY.txt').read_text(encoding='utf-8-sig').splitlines()[0].strip()
if not re.fullmatch(r'V5\.\d+\.\d+', RELEASE_VERSION):
    raise ValueError('VERSION_HISTORY.txt 首行必須為 V5.次版.修訂版')
