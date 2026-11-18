"""``league-venue-audit`` command line.

Reads the original trusted league snapshot (strict source contract shared with
league-table) and an independent schedule JSON, then publishes a venue
capacity audit report.  Overload is a business finding and exits 0; any parse
or validation failure exits 2 and preserves the previous report via the
shared atomic publisher.
"""
import argparse
import json
import sys
from pathlib import Path

from league_table.common import read, save
from league_table.model import parse

from .audit import audit
from .payloads import pending_fixtures, validate_schedule

DESCRIPTION = 'Venue capacity audit for scheduled pending fixtures'
SCHEMA_VERSION = 1


def run(source, schedule):
    source_data, source_digest = read(source)
    schedule_data, schedule_digest = read(schedule)
    teams, fixtures, results, deductions = parse(source_data)
    venues, limits, bookings = validate_schedule(
        schedule_data, set(fixtures), set(results))
    overloads, booking_relief, excess_unit_minutes = audit(
        venues, limits, bookings)
    return {
        'schema_version': SCHEMA_VERSION,
        'source_sha256': source_digest,
        'schedule_sha256': schedule_digest,
        'excess_unit_minutes': excess_unit_minutes,
        'overloads': overloads,
        'booking_relief': booking_relief,
        'unscheduled_fixtures': pending_fixtures(
            set(fixtures), set(results), bookings),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--schedule', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    report = None
    try:
        output = args.output.resolve()
        for path in (args.source, args.schedule):
            if path.resolve() == output:
                raise ValueError('input and output must differ')
        report = run(args.source, args.schedule)
        save(args.output, report)
    except (ValueError, TypeError, KeyError, OSError, OverflowError,
            RecursionError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
