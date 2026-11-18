"""``league-ticket-close`` command line.

Reads the original trusted league snapshot (strict source contract shared
with league-table) and the complete sales+refunds ticketing snapshot, then
publishes the clearing report for one half-open business-day window
``[from_day, to_day)``.  Any parse, validation or I/O failure exits 2 and
preserves the previous report via the shared atomic publisher; success
exits 0.
"""
import argparse
import json
import sys
from pathlib import Path

from league_table.common import read, save
from league_table.model import parse

from .close import close
from .payloads import DAY_MAX, validate_ticketing

DESCRIPTION = 'Offline clearing of cross-fixture league ticket orders'
SCHEMA_VERSION = 1


def _day_argument(value):
    """A plain integer day argument in 0..1000000000; signs/floats rejected."""
    if not isinstance(value, str) or not value.isdigit():
        raise ValueError('expected a non-negative integer day argument')
    number = int(value)
    if number > DAY_MAX:
        raise ValueError('day arguments must be in 0..1000000000')
    return number


def run(source, ticketing, from_day, to_day):
    source_data, source_digest = read(source)
    ticketing_data, ticketing_digest = read(ticketing)
    _teams, fixtures, _results, _deductions = parse(source_data)
    sales, refunds = validate_ticketing(ticketing_data, set(fixtures))
    fixture_rows, entries = close(
        set(fixtures), sales, refunds, from_day, to_day)
    return {
        'schema_version': SCHEMA_VERSION,
        'source_sha256': source_digest,
        'ticketing_sha256': ticketing_digest,
        'from_day': from_day,
        'to_day': to_day,
        'fixtures': fixture_rows,
        'entries': entries,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--ticketing', type=Path, required=True)
    parser.add_argument('--from-day', dest='from_day', required=True)
    parser.add_argument('--to-day', dest='to_day', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    report = None
    try:
        from_day = _day_argument(args.from_day)
        to_day = _day_argument(args.to_day)
        if not from_day < to_day:
            raise ValueError('window must satisfy from-day < to-day')
        output = args.output.resolve()
        for path in (args.source, args.ticketing):
            if path.resolve() == output:
                raise ValueError('input and output must differ')
        report = run(args.source, args.ticketing, from_day, to_day)
        save(args.output, report)
    except (ValueError, TypeError, KeyError, OSError, OverflowError,
            RecursionError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, ensure_ascii=False,
                     allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
