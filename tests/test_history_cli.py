import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'


def source():
    return {'teams': ['A', 'B'],
            'fixtures': [{'id': 'm', 'home': 'A', 'away': 'B'}],
            'results': [{'fixture': 'm', 'home_goals': 2, 'away_goals': 0}],
            'deductions': []}


def batch1():
    return {'batch_id': 'b1', 'operations': [
        {'fixture': 'm', 'left': 10, 'right': 30, 'expected': [2, 0],
         'replacement': [0, 1]},
        {'fixture': 'm', 'left': 20, 'right': 25, 'expected': [0, 1],
         'replacement': None},
    ]}


class HistoryCLI(unittest.TestCase):
    def run_cli(self, *arguments):
        env = dict(__import__('os').environ)
        env['PYTHONPATH'] = str(SRC) + (
            __import__('os').pathsep + env['PYTHONPATH']
            if env.get('PYTHONPATH') else '')
        return subprocess.run(
            [sys.executable, '-m', 'league_history.cli', *map(str, arguments)],
            capture_output=True, text=True, env=env)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.json'
        self.db = self.root / 'store.db'
        self.init_report = self.root / 'init.json'
        self.source.write_text(json.dumps(source()))

    def tearDown(self):
        self.tmp.cleanup()

    def init(self):
        result = self.run_cli('init', '--source', self.source,
                              '--db', self.db, '--output', self.init_report)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(self.init_report.read_text())

    def write_batch(self, name, payload):
        path = self.root / name
        path.write_text(json.dumps(payload))
        return path

    def test_init_report_envelope_and_byte_digest(self):
        report = self.init()
        self.assertEqual(set(report),
                         {'schema_version', 'store_id', 'source_sha256',
                          'knowledge_seq'})
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(report['knowledge_seq'], 0)
        self.assertEqual(len(report['store_id']), 32)
        self.assertEqual(report['source_sha256'],
                         hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertTrue(self.db.exists())

    def test_store_id_is_stable_across_commands(self):
        first = self.init()
        batch = self.write_batch('b.json', batch1())
        apply_report = self.root / 'apply.json'
        result = self.run_cli('apply', '--db', self.db, '--batch', batch,
                              '--output', apply_report)
        self.assertEqual(result.returncode, 0, result.stderr)
        applied = json.loads(apply_report.read_text())
        self.assertEqual(applied['store_id'], first['store_id'])
        self.assertEqual(applied['knowledge_seq'], 1)
        query_report = self.root / 'query.json'
        result = self.run_cli('query', '--db', self.db, '--as-of', '1',
                              '--from', '0', '--to', '40',
                              '--output', query_report)
        self.assertEqual(result.returncode, 0, result.stderr)
        queried = json.loads(query_report.read_text())
        self.assertEqual(queried['store_id'], first['store_id'])
        self.assertEqual(queried['knowledge_seq'], 1)
        self.assertEqual([(s['left'], s['right']) for s in queried['timeline']],
                         [(0, 10), (10, 20), (20, 25), (25, 30), (30, 40)])

    def test_query_timeline_segment_keys_exact(self):
        self.init()
        batch = self.write_batch('b.json', batch1())
        apply_out = self.root / 'apply.json'
        self.run_cli('apply', '--db', self.db, '--batch', batch,
                     '--output', apply_out)
        query_report = self.root / 'query.json'
        result = self.run_cli('query', '--db', self.db, '--as-of', '1',
                              '--from', '0', '--to', '40',
                              '--output', query_report)
        self.assertEqual(result.returncode, 0, result.stderr)
        for segment in json.loads(query_report.read_text())['timeline']:
            self.assertEqual(set(segment), {'left', 'right', 'result'})

    def test_infinite_window_emits_null_right(self):
        self.init()
        batch = self.write_batch('inf.json', {'batch_id': 'inf', 'operations': [
            {'fixture': 'm', 'left': 100, 'right': None,
             'expected': [2, 0], 'replacement': [5, 5]}]})
        self.assertEqual(self.run_cli(
            'apply', '--db', self.db, '--batch', batch,
            '--output', self.root / 'a.json').returncode, 0)
        out = self.root / 'q.json'
        result = self.run_cli('query', '--db', self.db, '--as-of', '1',
                              '--from', '0', '--to', 'inf', '--output', out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(out.read_text())['timeline'][-1]['right'])
        # null is not accepted as a CLI window bound
        bad = self.run_cli('query', '--db', self.db, '--as-of', '1',
                           '--from', '0', '--to', 'null', '--output',
                           self.root / 'bad.json')
        self.assertEqual(bad.returncode, 2)

    def test_knowledge_sequence_bounds_and_empty_window(self):
        self.init()
        out = self.root / 'q.json'
        self.assertEqual(self.run_cli(
            'query', '--db', self.db, '--as-of', '1', '--from', '0',
            '--to', '5', '--output', out).returncode, 2)
        self.assertEqual(self.run_cli(
            'query', '--db', self.db, '--as-of', '0', '--from', '5',
            '--to', '5', '--output', out).returncode, 2)
        self.assertFalse(out.exists())

    def test_bool_arguments_rejected(self):
        self.init()
        out = self.root / 'q.json'
        for value in ('true', 'false'):
            result = self.run_cli('query', '--db', self.db, '--as-of', value,
                                  '--from', '0', '--to', '5', '--output', out)
            self.assertEqual(result.returncode, 2)

    def test_apply_rejection_preserves_everything(self):
        self.init()
        good = self.write_batch('b1.json', batch1())
        self.run_cli('apply', '--db', self.db, '--batch', good,
                     '--output', self.root / 'a1.json')
        bad = self.write_batch('bad.json', {'batch_id': 'b2', 'operations': [
            {'fixture': 'm', 'left': 15, 'right': 28, 'expected': [0, 1],
             'replacement': [1, 1]}]})
        out = self.root / 'a2.json'
        out.write_text('old')
        result = self.run_cli('apply', '--db', self.db, '--batch', bad,
                              '--output', out)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(out.read_text(), 'old')
        # the rejected batch id remains usable with corrected first-time content
        fixed = self.write_batch('fixed.json', {'batch_id': 'b2', 'operations': [
            {'fixture': 'm', 'left': 40, 'right': 50, 'expected': [2, 0],
             'replacement': [3, 3]}]})
        result = self.run_cli('apply', '--db', self.db, '--batch', fixed,
                              '--output', self.root / 'a3.json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(
            (self.root / 'a3.json').read_text())['knowledge_seq'], 2)

    def test_replay_after_state_drift_returns_first_sequence(self):
        self.init()
        batch = self.write_batch('b1.json', batch1())
        self.run_cli('apply', '--db', self.db, '--batch', batch,
                     '--output', self.root / 'a1.json')
        drift = self.write_batch('drift.json', {'batch_id': 'drift', 'operations': [
            {'fixture': 'm', 'left': 40, 'right': 60, 'expected': [2, 0],
             'replacement': [4, 4]}]})
        self.run_cli('apply', '--db', self.db, '--batch', drift,
                     '--output', self.root / 'drift.json')
        replay = self.root / 'replay.json'
        result = self.run_cli('apply', '--db', self.db, '--batch', batch,
                              '--output', replay)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(replay.read_text())['knowledge_seq'], 1)

    def test_same_batch_id_different_content_fails(self):
        self.init()
        first = self.write_batch('b.json', {'batch_id': 'same', 'operations': [
            {'fixture': 'm', 'left': 0, 'right': 5, 'expected': [2, 0],
             'replacement': [1, 1]}]})
        self.assertEqual(self.run_cli(
            'apply', '--db', self.db, '--batch', first,
            '--output', self.root / 'a.json').returncode, 0)
        second = self.write_batch('b2.json', {'batch_id': 'same', 'operations': [
            {'fixture': 'm', 'left': 0, 'right': 5, 'expected': [2, 0],
             'replacement': [2, 2]}]})
        result = self.run_cli('apply', '--db', self.db, '--batch', second,
                              '--output', self.root / 'b.json')
        self.assertEqual(result.returncode, 2)

    def test_missing_db_is_never_created(self):
        missing = self.root / 'absent.db'
        batch = self.write_batch('b.json', batch1())
        result = self.run_cli('apply', '--db', missing, '--batch', batch,
                              '--output', self.root / 'a.json')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(missing.exists())
        result = self.run_cli('query', '--db', missing, '--as-of', '0',
                              '--from', '0', '--to', '5',
                              '--output', self.root / 'q.json')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(missing.exists())

    def test_path_overlap_including_aliases_rejected(self):
        self.init()
        batch = self.write_batch('b.json', batch1())
        # report aliasing the database
        link = self.root / 'dlink'
        link.symlink_to(self.db)
        result = self.run_cli('query', '--db', self.db, '--as-of', '0',
                              '--from', '0', '--to', '5', '--output', link)
        self.assertEqual(result.returncode, 2)
        # batch input aliasing the report path
        result = self.run_cli('apply', '--db', self.db, '--batch', batch,
                              '--output', batch)
        self.assertEqual(result.returncode, 2)
        # source aliasing the db on init
        result = self.run_cli('init', '--source', self.source,
                              '--db', self.source,
                              '--output', self.root / 'x.json')
        self.assertEqual(result.returncode, 2)

    def test_query_report_cannot_overwrite_db_and_keeps_db_bytes(self):
        self.init()
        before = self.db.read_bytes()
        result = self.run_cli('query', '--db', self.db, '--as-of', '0',
                              '--from', '0', '--to', '5',
                              '--output', self.db)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.db.read_bytes(), before)

    def test_symlink_report_rejected(self):
        self.init()
        target = self.root / 'target.json'
        link = self.root / 'link.json'
        link.symlink_to(target)
        result = self.run_cli('query', '--db', self.db, '--as-of', '0',
                              '--from', '0', '--to', '5', '--output', link)
        self.assertEqual(result.returncode, 2)

    def test_failed_init_leaves_no_store_and_no_report(self):
        bad = self.root / 'bad.json'
        bad.write_text('{}')
        fresh_db = self.root / 'fresh.db'
        out = self.root / 'out.json'
        result = self.run_cli('init', '--source', bad, '--db', fresh_db,
                              '--output', out)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(fresh_db.exists())
        self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
