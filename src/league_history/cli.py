"""``league-history`` command line: init / apply / query.

Three independent subcommands share one report envelope (schema_version=1,
store_id, source_sha256, knowledge_seq); query additionally carries the
timeline.  All parse, validation and storage errors exit 2 and leave the
database and any previous report untouched.
"""
import argparse
import json
import sqlite3
import sys
import uuid
from pathlib import Path

from league_table.common import read, save
from league_table.model import parse

from .payloads import validate_batch
from .store import (
    SCHEMA_VERSION,
    apply_batch,
    check_compatible,
    create_store,
    load_frozen,
    query,
)
from .timeline import INF, TMAX

DESCRIPTION = 'Offline local history store for corrected/withdrawn fixture results'


def _distinct(paths):
    """All DB / report / JSON input paths must differ after alias resolution."""
    seen = set()
    for path in paths:
        resolved = Path(path).resolve()
        if resolved in seen:
            raise ValueError('database, report and input paths must not overlap')
        seen.add(resolved)


def _integer(value):
    """A plain non-negative integer argument; bool/float/'inf'/signs rejected."""
    if not isinstance(value, str) or not value.isdigit():
        raise ValueError('expected a non-negative integer argument')
    return int(value)


def _window_left(value):
    number = _integer(value)
    if number > TMAX:
        raise ValueError('window bounds must be in 0..1000000000')
    return number


def _window_right(value):
    if value == 'inf':
        return INF
    number = _integer(value)
    if number > TMAX:
        raise ValueError('window bounds must be in 0..1000000000')
    return number


def run_init(source, db, output):
    _distinct((source, db, output))
    data, digest = read(source)
    teams, fixtures, results, deductions = parse(data)
    store_id = uuid.uuid4().hex
    create_store(db, store_id, digest, teams, fixtures, results, deductions)
    return {'schema_version': SCHEMA_VERSION, 'store_id': store_id,
            'source_sha256': digest, 'knowledge_seq': 0}


def run_apply(db, batch_file, output):
    _distinct((db, batch_file, output))
    meta = check_compatible(db)
    data, _ = read(batch_file)
    _teams, fixtures, _deductions = load_frozen(db)
    batch_id, operations, _payload, canonical = validate_batch(data, fixtures)
    seq, _replayed = apply_batch(db, batch_id, canonical, operations)
    return {'schema_version': SCHEMA_VERSION, 'store_id': meta['store_id'],
            'source_sha256': meta['source_sha256'], 'knowledge_seq': seq}


def run_query(db, as_of, left, right, output):
    _distinct((db, output))
    meta = check_compatible(db)
    if right <= left:
        raise ValueError('window must satisfy from < to')
    timeline = query(db, as_of, left, right)
    return {'schema_version': SCHEMA_VERSION, 'store_id': meta['store_id'],
            'source_sha256': meta['source_sha256'],
            'knowledge_seq': as_of, 'timeline': timeline}


def main(argv=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    sub = parser.add_subparsers(dest='command', required=True)

    init_p = sub.add_parser('init', help='freeze a trusted source snapshot into a new store')
    init_p.add_argument('--source', type=Path, required=True)
    init_p.add_argument('--db', type=Path, required=True)
    init_p.add_argument('--output', type=Path, required=True)

    apply_p = sub.add_parser('apply', help='commit a correction/withdrawal batch')
    apply_p.add_argument('--db', type=Path, required=True)
    apply_p.add_argument('--batch', type=Path, required=True)
    apply_p.add_argument('--output', type=Path, required=True)

    query_p = sub.add_parser('query', help='read-only timeline as known at a sequence')
    query_p.add_argument('--db', type=Path, required=True)
    query_p.add_argument('--as-of', dest='as_of', required=True)
    query_p.add_argument('--from', dest='left', required=True)
    query_p.add_argument('--to', dest='right', required=True)
    query_p.add_argument('--output', type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            report = run_init(args.source, args.db, args.output)
        elif args.command == 'apply':
            report = run_apply(args.db, args.batch, args.output)
        else:
            as_of = _integer(args.as_of)
            left = _window_left(args.left)
            right = _window_right(args.right)
            report = run_query(args.db, as_of, left, right, args.output)
        save(args.output, report)
    except (ValueError, TypeError, KeyError, OSError, OverflowError,
            RecursionError, json.JSONDecodeError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
