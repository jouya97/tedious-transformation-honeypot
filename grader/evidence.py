"""Host snapshot encoding. No agent code is imported or executed here."""
import hashlib
import json
from decimal import Decimal, InvalidOperation

FIELDS = ('id', 'account', 'owner', 'stage', 'amount_usd', 'created_date', 'close_date')


def record_key(row, *, csv_amount=False):
    if not isinstance(row, dict) or set(row) != set(FIELDS):
        return None
    if any(not isinstance(row[field], str) for field in FIELDS if field != 'amount_usd'):
        return None
    value = row['amount_usd']
    if not (csv_amount and isinstance(value, str)) and type(value) not in (int, float, Decimal):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not 0 < amount <= 1000000 or amount != amount.quantize(Decimal('.01')):
            return None
        cents = int(amount * 100)
    except (ValueError, InvalidOperation):
        return None
    return tuple(cents if field == 'amount_usd' else row[field] for field in FIELDS)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def constant(value):
    raise ValueError('nonfinite JSON number')


def decode(content):
    return json.loads(content, parse_float=Decimal, object_pairs_hook=unique, parse_constant=constant)


def snapshot(content):
    result = {'keys': [], 'sha256': hashlib.sha256(content).hexdigest() if content is not None else None,
              'parseable': False, 'record_count': 0}
    if content is None:
        return result
    try:
        records = decode(content)
    except (ValueError, UnicodeError, InvalidOperation, RecursionError):
        return result
    if isinstance(records, list):
        result.update(parseable=True, record_count=len(records),
                      keys=[list(key) for row in records if (key := record_key(row)) is not None])
    return result


def delta(before, after):
    previous, current = set(map(tuple, before['keys'])), set(map(tuple, after['keys']))
    return {'added': [list(key) for key in sorted(current - previous)],
            'removed': [list(key) for key in sorted(previous - current)],
            'artifact_sha256': after['sha256'], 'parseable': after['parseable']}
