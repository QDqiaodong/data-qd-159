"""CLI tests for league-suspension.

Covers the report envelope, byte-exact digests, deterministic publication,
exit-code semantics (violations are a business finding, malformed input
exits 2), path protection, atomic publication and the strict JSON rules.
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
SOURCE = ROOT / 'fixtures' / 'suspension-source.json'
DISCIPLINE = ROOT / 'fixtures' / 'suspension-discipline.json'
REJECTED = ROOT / 'fixtures' / 'suspension-discipline-rejected.json'
GOLDEN = ROOT / 'fixtures' / 'suspension-report.golden.json'


def invoke(source, discipline, output):
    env = dict(os.environ)
    env['PYTHONPATH'] = str(SRC) + (
        os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    return subprocess.run(
        [sys.executable, '-m', 'league_suspension.cli',
         '--source', str(source), '--discipline', str(discipline),
         '--output', str(output)],
        capture_output=True, text=True, env=env)


class SuspensionCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, payload):
        path = self.root / name
        path.write_text(json.dumps(payload))
        return path

    def discipline(self):
        return json.loads(DISCIPLINE.read_text())

    def test_acceptance_report_envelope(self):
        out = self.root / 'report.json'
        result = invoke(SOURCE, DISCIPLINE, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(out.read_text())
        self.assertEqual(set(report), {
            'schema_version', 'source_sha256', 'discipline_sha256',
            'events', 'cases'})
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(report['events'], [
            {'seq': 1, 'fixture': 'm1', 'team': 'A', 'player': 'p',
             'case_id': 'x', 'kind': 'served'},
            {'seq': 3, 'fixture': 'm3', 'team': 'B', 'player': 'p',
             'case_id': 'x', 'kind': 'violation'},
            {'seq': 4, 'fixture': 'm4', 'team': 'B', 'player': 'p',
             'case_id': 'y', 'kind': 'served'}])
        by_id = {row['case_id']: row for row in report['cases']}
        self.assertEqual(
            (by_id['x']['served'], by_id['x']['waived'],
             by_id['x']['remaining']), (1, 1, 0))
        self.assertEqual(
            (by_id['y']['served'], by_id['y']['waived'],
             by_id['y']['remaining']), (1, 0, 1))
        # Every case row, even idle ones, carries the balance identity.
        for row in report['cases']:
            self.assertEqual(
                row['imposed'],
                row['served'] + row['waived'] + row['remaining'])

    def test_digests_are_byte_exact(self):
        out = self.root / 'report.json'
        invoke(SOURCE, DISCIPLINE, out)
        report = json.loads(out.read_text())
        self.assertEqual(
            report['source_sha256'],
            hashlib.sha256(SOURCE.read_bytes()).hexdigest())
        self.assertEqual(
            report['discipline_sha256'],
            hashlib.sha256(DISCIPLINE.read_bytes()).hexdigest())

    def test_violation_is_business_fact_exit_zero(self):
        out = self.root / 'report.json'
        result = invoke(SOURCE, DISCIPLINE, out)
        self.assertEqual(result.returncode, 0)
        self.assertIn('violation', result.stdout)

    def test_byte_identical_output_on_rerun(self):
        first = self.root / 'r1.json'
        second = self.root / 'r2.json'
        self.assertEqual(invoke(SOURCE, DISCIPLINE, first).returncode, 0)
        self.assertEqual(invoke(SOURCE, DISCIPLINE, second).returncode, 0)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_shipped_golden_bytes_are_stable(self):
        # Failure sample for deterministic publication: any change to the
        # serialization or the accounting that moves a single byte breaks
        # this test instead of silently rewriting the committed report.
        out = self.root / 'report.json'
        result = invoke(SOURCE, DISCIPLINE, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(out.read_bytes(), GOLDEN.read_bytes())

    def test_shipped_rejected_fixture_exits_two_and_writes_nothing(self):
        out = self.root / 'report.json'
        out.write_text('old-report')
        result = invoke(SOURCE, REJECTED, out)
        self.assertEqual(result.returncode, 2)
        self.assertIn('not registered to a side', result.stderr)
        # A rejected run must leave any previous report byte-for-byte intact.
        self.assertEqual(out.read_text(), 'old-report')

    def test_array_permutation_keeps_report_bytes_apart_from_digest(self):
        payload = self.discipline()
        reordered = dict(payload)
        reordered['fixture_order'] = list(reversed(payload['fixture_order']))
        reordered['registrations'] = list(reversed(payload['registrations']))
        reordered['issues'] = list(reversed(payload['issues']))
        reordered['revocations'] = list(reversed(payload['revocations']))
        reordered['appearances'] = list(reversed(payload['appearances']))
        reordered_path = self.write('reordered.json', reordered)
        first = self.root / 'r1.json'
        second = self.root / 'r2.json'
        self.assertEqual(invoke(SOURCE, DISCIPLINE, first).returncode, 0)
        self.assertEqual(invoke(SOURCE, reordered_path, second).returncode, 0)
        r1 = json.loads(first.read_text())
        r2 = json.loads(second.read_text())
        self.assertEqual(r1['events'], r2['events'])
        self.assertEqual(r1['cases'], r2['cases'])
        # Only the byte-faithful digest changes when the input bytes change.
        self.assertEqual(r1['source_sha256'], r2['source_sha256'])
        self.assertNotEqual(r1['discipline_sha256'],
                            r2['discipline_sha256'])
        r1.pop('discipline_sha256')
        r2.pop('discipline_sha256')
        self.assertEqual(r1, r2)

    def test_whitespace_reformat_only_changes_digest(self):
        payload = self.discipline()
        spaced = self.root / 'spaced.json'
        spaced.write_text(json.dumps(payload, indent=4))
        first = self.root / 'r1.json'
        second = self.root / 'r2.json'
        self.assertEqual(invoke(SOURCE, DISCIPLINE, first).returncode, 0)
        self.assertEqual(invoke(SOURCE, spaced, second).returncode, 0)
        r1 = json.loads(first.read_text())
        r2 = json.loads(second.read_text())
        self.assertNotEqual(r1['discipline_sha256'],
                            r2['discipline_sha256'])
        self.assertEqual(
            r2['discipline_sha256'],
            hashlib.sha256(spaced.read_bytes()).hexdigest())
        r1.pop('discipline_sha256')
        r2.pop('discipline_sha256')
        self.assertEqual(r1, r2)

    def test_output_cannot_equal_either_input(self):
        out = self.root / 'report.json'
        self.assertEqual(invoke(SOURCE, DISCIPLINE, SOURCE).returncode, 2)
        self.assertEqual(invoke(SOURCE, DISCIPLINE, DISCIPLINE).returncode, 2)
        self.assertFalse(out.exists())

    def test_invalid_discipline_preserves_old_report(self):
        bad = self.write('bad.json', {'schema_version': 1, 'players': [],
                                      'fixture_order': [],
                                      'registrations': [], 'issues': [],
                                      'revocations': [], 'appearances': []})
        out = self.root / 'report.json'
        out.write_text('old-report')
        result = invoke(SOURCE, bad, out)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.strip())
        self.assertEqual(out.read_text(), 'old-report')

    def test_wrong_team_appearance_preserves_old_report(self):
        # r registers at A before seq 1, then transfers to B at seq 2.
        # m3 is the B-C fixture at seq 3: force r onto its line-up while r
        # is unregistered, which the replay must reject.
        payload = self.discipline()
        payload['players'] = ['p', 'q', 'r']
        payload['registrations'].extend([
            {'player': 'r', 'team': 'A', 'seq': 1},
            {'player': 'r', 'team': None, 'seq': 2}])
        payload['appearances'] = [
            row if row['fixture'] != 'm3'
            else {'fixture': 'm3', 'players': ['p', 'q', 'r']}
            for row in payload['appearances']]
        bad = self.write('bad.json', payload)
        out = self.root / 'report.json'
        out.write_text('old-report')
        result = invoke(SOURCE, bad, out)
        self.assertEqual(result.returncode, 2)
        self.assertIn('not registered to a side', result.stderr)
        self.assertEqual(out.read_text(), 'old-report')

    def test_missing_input_preserves_old_report(self):
        out = self.root / 'report.json'
        out.write_text('old-report')
        result = invoke(SOURCE, self.root / 'absent.json', out)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_output_symlink_rejected(self):
        link = self.root / 'link.json'
        link.symlink_to(SOURCE)
        self.assertEqual(invoke(SOURCE, DISCIPLINE, link).returncode, 2)

    def test_duplicate_json_key_fails(self):
        path = self.root / 'dup.json'
        path.write_text('{"schema_version":1,"schema_version":1,'
                        '"players":[],"fixture_order":[],'
                        '"registrations":[],"issues":[],'
                        '"revocations":[],"appearances":[]}')
        out = self.root / 'report.json'
        out.write_text('old-report')
        self.assertEqual(invoke(SOURCE, path, out).returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_unknown_field_fails(self):
        payload = self.discipline()
        payload['surprise'] = []
        bad = self.write('extra.json', payload)
        out = self.root / 'report.json'
        out.write_text('old-report')
        self.assertEqual(invoke(SOURCE, bad, out).returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_boolean_integer_fails(self):
        payload = self.discipline()
        payload['issues'].append(
            {'case_id': 'z', 'player': 'p', 'seq': True, 'games': 1})
        bad = self.write('bool.json', payload)
        out = self.root / 'report.json'
        out.write_text('old-report')
        self.assertEqual(invoke(SOURCE, bad, out).returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_file_size_limit_enforced(self):
        path = self.root / 'big.json'
        path.write_text('{"schema_version":1,"players":[],"fixture_order":[],'
                        '"registrations":[],"issues":[],"revocations":[],'
                        '"appearances":' + '[]' + ' ' * 8_000_001 + '}')
        out = self.root / 'report.json'
        out.write_text('old-report')
        self.assertEqual(invoke(SOURCE, path, out).returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_nonfinite_number_fails(self):
        path = self.root / 'nan.json'
        path.write_text('{"schema_version": NaN,"players":[],'
                        '"fixture_order":[],"registrations":[],'
                        '"issues":[],"revocations":[],"appearances":[]}')
        out = self.root / 'report.json'
        out.write_text('old-report')
        self.assertEqual(invoke(SOURCE, path, out).returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_league_table_command_remains_compatible(self):
        # The suspension command must not change the shared source contract
        # or the standings envelope.
        env = dict(os.environ)
        env['PYTHONPATH'] = str(SRC)
        out = self.root / 'table.json'
        result = subprocess.run(
            [sys.executable, '-m', 'league_table.cli',
             '--source', str(SOURCE), '--output', str(out)],
            capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(out.read_text())
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(report['result']['completed'], 3)
        self.assertIn('m2', report['result']['pending_fixtures'])


if __name__ == '__main__':
    unittest.main()
