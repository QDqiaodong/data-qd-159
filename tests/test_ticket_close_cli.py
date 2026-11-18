"""CLI tests for league-ticket-close.

Covers the report envelope, byte-exact digests, exit-code semantics, path
protection, atomic publication, strict JSON handling, window argument
validation and the shipped fixtures.
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
SOURCE = ROOT / 'fixtures' / 'ticket-close-source.json'
TICKETING = ROOT / 'fixtures' / 'ticket-close-ticketing.json'
REJECTED = ROOT / 'fixtures' / 'ticket-close-ticketing-rejected.json'


def invoke(source, ticketing, output, left='10', right='20'):
    env = dict(os.environ)
    env['PYTHONPATH'] = str(SRC) + (
        os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    return subprocess.run(
        [sys.executable, '-m', 'league_ticket_close.cli',
         '--source', str(source), '--ticketing', str(ticketing),
         '--from-day', str(left), '--to-day', str(right),
         '--output', str(output)],
        capture_output=True, text=True, env=env)


class TicketCloseCLI(unittest.TestCase):
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
        result = invoke(SOURCE, TICKETING, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(out.read_text())
        self.assertEqual(set(report), {
            'schema_version', 'source_sha256', 'ticketing_sha256',
            'from_day', 'to_day', 'fixtures', 'entries'})
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(report['from_day'], 10)
        self.assertEqual(report['to_day'], 20)
        self.assertEqual(report['fixtures'], [
            {'fixture': 'm1', 'opening_cents': 10, 'sales_cents': 0,
             'refunds_cents': 6, 'closing_cents': 4},
            {'fixture': 'm2', 'opening_cents': 7, 'sales_cents': 0,
             'refunds_cents': 4, 'closing_cents': 3},
            {'fixture': 'm3', 'opening_cents': 0, 'sales_cents': 0,
             'refunds_cents': 0, 'closing_cents': 0}])
        self.assertEqual(report['entries'], [
            {'kind': 'refund', 'day': 10, 'fixture': 'm1', 'sale': 's1',
             'line': 'lineA', 'refund': 'r1', 'first': 2, 'stop': 4,
             'amount_cents': 6},
            {'kind': 'refund', 'day': 10, 'fixture': 'm2', 'sale': 's1',
             'line': 'lineB', 'refund': 'r2', 'first': 1, 'stop': 2,
             'amount_cents': 4}])

    def test_digests_are_byte_exact(self):
        out = self.root / 'report.json'
        invoke(SOURCE, TICKETING, out)
        report = json.loads(out.read_text())
        self.assertEqual(report['source_sha256'],
                         hashlib.sha256(SOURCE.read_bytes()).hexdigest())
        self.assertEqual(report['ticketing_sha256'],
                         hashlib.sha256(TICKETING.read_bytes()).hexdigest())

    def test_digest_survives_whitespace_reformat(self):
        # Identical business content, different bytes -> same business
        # output but a faithfully different ticketing digest.
        raw = json.loads(TICKETING.read_text())
        compact = self.write('compact.json', raw)
        spaced = self.root / 'spaced.json'
        spaced.write_text(json.dumps(raw, indent=4))
        first = self.root / 'r1.json'
        second = self.root / 'r2.json'
        self.assertEqual(invoke(SOURCE, compact, first).returncode, 0)
        self.assertEqual(invoke(SOURCE, spaced, second).returncode, 0)
        r1 = json.loads(first.read_text())
        r2 = json.loads(second.read_text())
        self.assertEqual(r1['ticketing_sha256'],
                         hashlib.sha256(compact.read_bytes()).hexdigest())
        self.assertEqual(r2['ticketing_sha256'],
                         hashlib.sha256(spaced.read_bytes()).hexdigest())
        self.assertNotEqual(r1['ticketing_sha256'], r2['ticketing_sha256'])
        for key in ('fixtures', 'entries', 'from_day', 'to_day'):
            self.assertEqual(r1[key], r2[key])

    def test_success_exit_zero_and_stdout(self):
        out = self.root / 'report.json'
        result = invoke(SOURCE, TICKETING, out)
        self.assertEqual(result.returncode, 0)
        self.assertIn('"ticketing_sha256"', result.stdout)

    def test_empty_ticketing_is_valid(self):
        ticketing = self.write('empty.json', {'sales': [], 'refunds': []})
        out = self.root / 'report.json'
        result = invoke(SOURCE, ticketing, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(out.read_text())
        self.assertEqual(report['entries'], [])
        self.assertEqual([row['fixture'] for row in report['fixtures']],
                         ['m1', 'm2', 'm3'])
        self.assertTrue(all(
            row == {'fixture': row['fixture'], 'opening_cents': 0,
                    'sales_cents': 0, 'refunds_cents': 0,
                    'closing_cents': 0}
            for row in report['fixtures']))

    def test_output_cannot_equal_either_input(self):
        out = self.root / 'report.json'
        self.assertEqual(invoke(SOURCE, TICKETING, SOURCE).returncode, 2)
        self.assertEqual(invoke(SOURCE, TICKETING, TICKETING).returncode, 2)
        self.assertFalse(out.exists())

    def test_cross_period_duplicate_refund_rejected(self):
        # The rejected fixture adds r3 refunding lineA ticket 2 on day 7
        # (before the window); r1 refunds tickets 2..3 inside the window.
        out = self.root / 'report.json'
        out.write_text('old-report')
        result = invoke(SOURCE, REJECTED, out)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_invalid_ticketing_preserves_old_report(self):
        bad = self.write('bad.json', {
            'sales': [{'id': 's', 'day': True, 'discount_cents': 0,
                       'lines': [{'id': 'a', 'fixture': 'm1', 'quantity': 1,
                                  'list_price_cents': 1}]}],
            'refunds': []})
        out = self.root / 'report.json'
        out.write_text('old-report')
        result = invoke(SOURCE, bad, out)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_missing_input_preserves_old_report(self):
        out = self.root / 'report.json'
        out.write_text('old-report')
        result = invoke(SOURCE, self.root / 'absent.json', out)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_output_symlink_rejected(self):
        ticketing = self.write('empty.json', {'sales': [], 'refunds': []})
        link = self.root / 'link.json'
        link.symlink_to(SOURCE)
        self.assertEqual(invoke(SOURCE, ticketing, link).returncode, 2)

    def test_duplicate_json_key_fails(self):
        path = self.root / 'dup.json'
        path.write_text('{"sales":[],"sales":[],"refunds":[]}')
        out = self.root / 'report.json'
        out.write_text('old-report')
        self.assertEqual(invoke(SOURCE, path, out).returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_unknown_field_fails(self):
        path = self.write('extra.json', {
            'sales': [], 'refunds': [], 'surprise': []})
        out = self.root / 'report.json'
        out.write_text('old-report')
        self.assertEqual(invoke(SOURCE, path, out).returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_file_size_limit_enforced(self):
        path = self.root / 'big.json'
        # > 8 MB while staying syntactically valid JSON.
        path.write_text('{"sales":[],"refunds":'
                        + '[]' + ' ' * (8_000_001) + '}')
        out = self.root / 'report.json'
        out.write_text('old-report')
        self.assertEqual(invoke(SOURCE, path, out).returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_array_record_limit_enforced(self):
        path = self.write('many.json', {
            'sales': [{'id': 's%d' % i, 'day': 1, 'discount_cents': 0,
                       'lines': [{'id': 'a', 'fixture': 'm1', 'quantity': 1,
                                  'list_price_cents': 1}]}
                      for i in range(20_001)],
            'refunds': []})
        out = self.root / 'report.json'
        out.write_text('old-report')
        self.assertEqual(invoke(SOURCE, path, out).returncode, 2)
        self.assertEqual(out.read_text(), 'old-report')

    def test_window_arguments_validated(self):
        out = self.root / 'report.json'
        out.write_text('old-report')
        for left, right in (('10', '10'), ('20', '10'), ('-1', '10'),
                            ('1.5', '10'), ('x', '10'), ('0', '1000000001'),
                            ('', '10')):
            result = invoke(SOURCE, TICKETING, out, left, right)
            self.assertEqual(result.returncode, 2, (left, right))
            self.assertEqual(out.read_text(), 'old-report')
        # Boundary window spanning the whole day range is legal.
        self.assertEqual(
            invoke(SOURCE, TICKETING, out, '0', '1000000000').returncode, 0)

    def test_input_permutation_stability(self):
        raw = json.loads(TICKETING.read_text())
        reversed_payload = {
            'sales': [dict(sale, lines=list(reversed(sale['lines'])))
                      for sale in reversed(raw['sales'])],
            'refunds': list(reversed(raw['refunds']))}
        reordered = self.write('reordered.json', reversed_payload)
        first = self.root / 'r1.json'
        second = self.root / 'r2.json'
        self.assertEqual(invoke(SOURCE, TICKETING, first).returncode, 0)
        self.assertEqual(invoke(SOURCE, reordered, second).returncode, 0)
        r1 = json.loads(first.read_text())
        r2 = json.loads(second.read_text())
        self.assertEqual(r1['fixtures'], r2['fixtures'])
        self.assertEqual(r1['entries'], r2['entries'])


if __name__ == '__main__':
    unittest.main()
