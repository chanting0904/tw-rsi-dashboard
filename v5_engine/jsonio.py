import hashlib
import datetime
import json
import math
from pathlib import Path
import numpy as np


def clean_json(value):
    if isinstance(value, dict):
        return {str(key): clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(item) for item in value]
    if isinstance(value, np.generic):
        return clean_json(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(clean_json(data), ensure_ascii=False, indent=1, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def document_digest(document):
    """Bind the complete JSON document using its on-disk normalization."""
    body=json.dumps(clean_json(document),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def validation_matches(signal,validation,performance=None):
    if not isinstance(validation,dict) or validation.get('signal_digest')!=document_digest(signal):
        return False
    return performance is None or validation.get('performance_digest')==document_digest(performance)
