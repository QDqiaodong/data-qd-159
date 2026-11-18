"""``league-suspension`` command line.

Reads the original trusted league snapshot (the strict source contract
shared with league-table) and an independent discipline JSON, replays
registrations, cases, revocations and formal fixtures in sequence order,
and publishes the suspension accounting report.  A suspension violation is
a business finding and exits 0; any parse or validation failure exits 2 and
preserves the previous report through the shared atomic publisher.
"""
import argparse
import json
import sys
from pathlib import Path

from league_table.common import read, save
from league_table.model import parse

from .engine import replay
from .payloads import validate_discipline

DESCRIPTION = 'Per-player suspension accounting over a fixed league snapshot'
SCHEMA_VERSION = 1


def run(source, discipline_path):
    source_data, source_digest = read(source)
    discipline_data, discipline_digest = read(discipline_path)
    teams, fixtures, results, _deductions = parse(source_data)
    discipline = validate_discipline(
        discipline_data, set(teams), fixtures, set(results))
    events, case_rows = replay(fixtures, set(results), discipline)
    return {
        'schema_version': SCHEMA_VERSION,
        'source_sha256': source_digest,
        'discipline_sha256': discipline_digest,
        'events': events,
        'cases': case_rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--discipline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    report = None
    try:
        output = args.output.resolve()
        for path in (args.source, args.discipline):
            if path.resolve() == output:
                raise ValueError('input and output must differ')
        report = run(args.source, args.discipline)
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
