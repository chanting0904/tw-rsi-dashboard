"""Release identity comes from the first line of the human-readable changelog."""
from pathlib import Path
import re

STRATEGY_VERSION = 'V6'
# 已發布的舊策略訊號在下次排程重算前仍需可驗證、可承接。
SUPPORTED_STRATEGY_VERSIONS = ('V5', 'V6')
RELEASE_VERSION = (Path(__file__).parent / 'VERSION_HISTORY.txt').read_text(encoding='utf-8-sig').splitlines()[0].strip()
if not re.fullmatch(STRATEGY_VERSION + r'\.\d+\.\d+', RELEASE_VERSION):
    raise ValueError('VERSION_HISTORY.txt 首行必須為 ' + STRATEGY_VERSION + '.次版.修訂版')
