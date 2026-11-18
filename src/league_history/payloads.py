"""Strict parsing, validation and canonicalization of apply batch files.

The canonical business content of a batch is the fully parsed batch_id and
the operations values in their given array order.  Object key order and JSON
whitespace are ignored; the source path and any byte digest are not part of
it.  Replaying a file with the same canonical content returns the original
knowledge sequence without re-running any expected-value check.
"""
import json

from league_table.common import ident, require

from .timeline import INF, TMAX


def business_time(value):
    """Business instants are plain ints in 0..1_000_000_000; bool rejected."""
    if type(value) is not int or not 0 <= value <= TMAX:
        raise ValueError('business time must be an integer in 0..1000000000')
    return value


def score(value):
    """A score is exactly two integers in 0..1000; bool rejected."""
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError('score must be a two-element [home, away] array')
    home, away = value
    if type(home) is not int or type(away) is not int:
        raise ValueError('goals must be integers')
    if not 0 <= home <= 1000 or not 0 <= away <= 1000:
        raise ValueError('goals must be in 0..1000')
    return [home, away]


def validate_batch(data, fixtures):
    """Validate a parsed batch file against the frozen fixture schedule.

    Returns (batch_id, operations, canonical_payload).  Operations keep the
    file order and the original null/score values; engine operations use the
    internal INF sentinel in place of a null right bound.
    """
    require(data, 'batch_id operations')
    batch_id = ident(data['batch_id'])
    raw_ops = data['operations']
    if not isinstance(raw_ops, list) or not 1 <= len(raw_ops) <= 1000:
        raise ValueError('operations must be an array of 1..1000 entries')
    canonical_ops = []
    engine_ops = []
    for entry in raw_ops:
        require(entry, 'fixture left right expected replacement')
        fixture_id = ident(entry['fixture'])
        if fixture_id not in fixtures:
            raise ValueError('unknown fixture: ' + fixture_id)
        left = business_time(entry['left'])
        if entry['right'] is None:
            right = None
        else:
            right = business_time(entry['right'])
            if right <= left:
                raise ValueError('finite right must be greater than left')
        expected = None if entry['expected'] is None else score(entry['expected'])
        replacement = None if entry['replacement'] is None else score(entry['replacement'])
        canonical_ops.append({'fixture': fixture_id, 'left': left, 'right': right,
                              'expected': expected, 'replacement': replacement})
        engine_ops.append({'fixture': fixture_id, 'left': left,
                           'right': INF if right is None else right,
                           'expected': None if expected is None else tuple(expected),
                           'replacement': None if replacement is None else tuple(replacement)})
    payload = {'batch_id': batch_id, 'operations': canonical_ops}
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=False, allow_nan=False)
    return batch_id, engine_ops, payload, canonical
