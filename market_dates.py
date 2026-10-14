"""Normalize official Gregorian and ROC quote dates without inventing a date."""
import datetime
import re


def quote_date(value):
    text = str(value or "").strip()
    parts = re.findall(r"\d+", text)
    if len(parts) == 3:
        year, month, day = map(int, parts)
    elif len(parts) == 1 and len(parts[0]) in (7, 8):
        digits = parts[0]
        year, month, day = int(digits[:-4]), int(digits[-4:-2]), int(digits[-2:])
    else:
        raise ValueError(f"無法識別行情日期：{text!r}")
    if year < 1911:
        year += 1911
    return datetime.date(year, month, day).isoformat()
