"""CLI tests for league-venue-audit.

Covers the report envelope, byte-exact digests, exit-code semantics, path
protection, atomic publication, strict JSON handling and the shipped fixtures.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
SOURCE = ROOT / 'fixtures' / 'venue-audit-source.json'
SCHEDULE = ROOT / 'fixtures' / 'venue-audit-schedule.json'


def invoke(source, schedule, output):
    env = dict(os.environ)
    env['PYTHONPATH'] = str(SRC) + (
        os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    return subprocess.run(
        [sys.executable, '-m', 'league_venue_audit.cli',
         '--source', str(source), '--schedule', str(schedule),
         '--output', str(output)],
        capture_output=True, text=True, env=env)


class VenueAuditCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, payload):
        path = self.root / name
        path.write_text(json.dumps(payload))
        return path

    def test_acceptance_report_envelope(self):
        out = self.root / 'report.json'
        result = invoke(SOURCE, SCHEDULE, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(out.read_text())
        self.assertEqual(set(report), {
            'schema_version', 'source_sha256', 'schedule_sha256',
            'excess_unit_minutes', 'overloads', 'booking_relief',
            'unscheduled_fixtures'})
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(report['excess_unit_minutes'], 10)
        self.assertEqual(report['booking_relief'], [
            {'fixture': 'm2', 'relief_unit_minutes': 9},
            {'fixture': 'm3', 'relief_unit_minutes': 7}])
        self.assertEqual(report['overloads'], [
            {'venue': 'v1', 'start': 2, 'end': 3, 'capacity': 3, 'load': 4},
            {'venue': 'v1', 'start': 3, 'end': 5, 'capacity': 2, 'load': 4},
            {'venue': 'v1', 'start': 5, 'end': 6, 'capacity': 1, 'load': 4},
            {'venue': 'v1', 'start': 6, 'end': 8, 'capacity': 1, 'load': 2}])
        self.assertEqual(report['unscheduled_fixtures'], ['m4'])

    def test_digests_are_byte_exact(self):
        out = self.root / 'report.json'
        invoke(SOURCE, SCHEDULE, out)
        report = json.loads(out.read_text())
        self.assertEqual(report['source_sha256'],
                         hashlib.sha256(SOURCE.read_bytes()).hexdigest())
        self.assertEqual(report['schedule_sha256'],
                         hashlib.sha256(SCHEDULE.read_bytes()).hexdigest())

    def test_digest_survives_whitespace_reformat(self):
        # Identical business content, different bytes -> same business output
        # but a faithfully different schedule digest.
        raw = json.loads(SCHEDULE.read_text())
        compact = self.write('compact.json', raw)
        spaced = self.root / 'spaced.json'
        spaced.write_text(json.dumps(raw, indent=4))
        first = self.root / 'r1.json'
        second = self.root / 'r2.json'
        self.assertEqual(invoke(SOURCE, compact, first).returncode, 0)
        self.assertEqual(invoke(SOURCE, spaced, second).returncode, 0)
        r1 = json.loads(first.read_text())
        r2 = json.loads(second.read_text())
        self.assertEqual(r1['schedule_sha256'],
                         hashlib.sha256(compact.read_bytes()).hexdigest())
        self.assertEqual(r2['schedule_sha256'],
                         hashlib.sha256(spaced.read_bytes()).hexdigest())
        self.assertNotEqual(r1['schedule_sha256'], r2['schedule_sha256'])
        for key in ('overloads', 'booking_relief', 'excess_unit_minutes',
                    'unscheduled_fixtures'):
            self.assertEqual(r1[key], r2[key])

    def test_overload_is_business_fact_exit_zero(self):
        out = self.root / 'report.json'
        result = invoke(SOURCE, SCHEDULE, out)
        self.assertEqual(result.returncode, 0)
        self.assertIn('excess_unit_minutes', result.stdout)

    def test_empty_schedule_is_valid(self):
        schedule = self.write('empty.json',
                              {'venues': [], 'limits': [], 'bookings': []})
        out = self.root / 'report.json'
        result = invoke(SOURCE, schedule, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(out.read_text())
        self.assertEqual(report['excess_unit_minutes'], 0)
        self.assertEqual(report['overloads'], [])
        self.assertEqual(report['booking_relief'], [])
        # Every fixture without a formal result is unscheduled.
        self.assertEqual(report['unscheduled_fixtures'], ['m2', 'm3', 'm4'])

    def test_output_cannot_equal_either_input(self):
        out = self.root / 'report.json'
        self.assertEqual(invoke(SOURCE, SCHEDULE, SOURCE).returncode, 2)
        self.assertEqual(invoke(SOURCE, SCHEDULE, SCHEDULE).returncode, 2)
        self.assertFalse(out.exists())

    def test_invalid_reference_preserves_old_report(self):
        bad = self.write('bad.json', {
            'venues': [{'id': 'v', 'capacity': 1}],
            'limits': [],
            'bookings': [{'fixture': 'missing', 'venue': 'v',
                          'start': 0, 'end': 1, 'units': 1}]})
        out = self.root / 'report.json'
        out.write_text('old-report')
        result = invoke(SOURCE, bad, out)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_boolean_value_preserves_old_report(self):
        bad = self.write('bad.json', {
            'venues': [{'id': 'v', 'capacity': True}],
            'limits': [], 'bookings': []})
        out = self.root / 'report.json'
        out.write_text('old-report')
        self.assertEqual(invoke(SOURCE, bad, out).returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_missing_input_preserves_old_report(self):
        out = self.root / 'report.json'
        out.write_text('old-report')
        result = invoke(SOURCE, self.root / 'absent.json', out)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_output_symlink_rejected(self):
        schedule = self.write('empty.json',
                              {'venues': [], 'limits': [], 'bookings': []})
        link = self.root / 'link.json'
        link.symlink_to(SOURCE)
        self.assertEqual(invoke(SOURCE, schedule, link).returncode, 2)

    def test_duplicate_json_key_fails(self):
        path = self.root / 'dup.json'
        path.write_text('{"venues":[],"venues":[],"limits":[],"bookings":[]}')
        out = self.root / 'report.json'
        out.write_text('old-report')
        self.assertEqual(invoke(SOURCE, path, out).returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_unknown_field_fails(self):
        path = self.write('extra.json', {
            'venues': [], 'limits': [], 'bookings': [], 'surprise': []})
        out = self.root / 'report.json'
        out.write_text('old-report')
        self.assertEqual(invoke(SOURCE, path, out).returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_file_size_limit_enforced(self):
        path = self.root / 'big.json'
        # > 8 MB while staying syntactically valid JSON.
        path.write_text('{"venues":[],"limits":[],"bookings":'
                        + '[]' + ' ' * (8_000_001) + '}')
        out = self.root / 'report.json'
        out.write_text('old-report')
        self.assertEqual(invoke(SOURCE, path, out).returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_array_record_limit_enforced(self):
        path = self.write('many.json', {
            'venues': [{'id': f'v{i}', 'capacity': 1}
                       for i in range(20_001)],
            'limits': [], 'bookings': []})
        out = self.root / 'report.json'
        out.write_text('old-report')
        self.assertEqual(invoke(SOURCE, path, out).returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_input_permutation_stability(self):
        raw = json.loads(SCHEDULE.read_text())
        reversed_payload = {
            'venues': list(reversed(raw['venues'])),
            'limits': list(reversed(raw['limits'])),
            'bookings': list(reversed(raw['bookings']))}
        reordered = self.write('reversed.json', reversed_payload)
        first = self.root / 'r1.json'
        second = self.root / 'r2.json'
        self.assertEqual(invoke(SOURCE, SCHEDULE, first).returncode, 0)
        self.assertEqual(invoke(SOURCE, reordered, second).returncode, 0)
        r1 = json.loads(first.read_text())
        r2 = json.loads(second.read_text())
        self.assertEqual(r1['overloads'], r2['overloads'])
        self.assertEqual(r1['booking_relief'], r2['booking_relief'])
        self.assertEqual(r1['excess_unit_minutes'], r2['excess_unit_minutes'])
        self.assertEqual(r1['unscheduled_fixtures'], r2['unscheduled_fixtures'])

    def test_completed_fixture_booking_rejected(self):
        # m1 carries a formal result in the source snapshot.
        bad = self.write('done.json', {
            'venues': [{'id': 'v', 'capacity': 1}],
            'limits': [],
            'bookings': [{'fixture': 'm1', 'venue': 'v',
                          'start': 0, 'end': 1, 'units': 1}]})
        out = self.root / 'report.json'
        out.write_text('old-report')
        self.assertEqual(invoke(SOURCE, bad, out).returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')


if __name__ == '__main__':
    unittest.main()
