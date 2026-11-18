import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from league_table.model import parse

from league_history.payloads import validate_batch
from league_history.store import (
    APPLICATION_ID,
    apply_batch,
    check_compatible,
    create_store,
    query,
)
from league_history.timeline import INF


def source():
    return {'teams': ['A', 'B'],
            'fixtures': [{'id': 'm', 'home': 'A', 'away': 'B'}],
            'results': [{'fixture': 'm', 'home_goals': 2, 'away_goals': 0}],
            'deductions': []}


class Persistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'store.db'
        teams, fixtures, results, deductions = parse(source())
        create_store(self.db, 'store-identity', 'sha', teams, fixtures,
                     results, deductions)

    def tearDown(self):
        self.tmp.cleanup()

    def apply_payload(self, payload):
        _, fixtures, _ = __import__(
            'league_history.store', fromlist=['load_frozen']).load_frozen(self.db)
        batch_id, operations, _, canonical = validate_batch(payload, fixtures)
        return apply_batch(self.db, batch_id, canonical, operations)

    def test_meta_identity_and_frozen_snapshot(self):
        meta = check_compatible(self.db)
        self.assertEqual(meta['store_id'], 'store-identity')
        self.assertEqual(meta['source_sha256'], 'sha')
        self.assertEqual(meta['knowledge_seq'], '0')
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute(
                'PRAGMA application_id').fetchone()[0], APPLICATION_ID)
            rows = conn.execute(
                'SELECT sys_from,sys_to,left_time,right_time,home_goals,away_goals '
                'FROM results_rows').fetchall()
            self.assertEqual(rows, [(0, None, 0, None, 2, 0)])
        finally:
            conn.close()

    def test_init_refuses_overwrite_and_leaves_no_half_store(self):
        teams, fixtures, results, deductions = parse(source())
        with self.assertRaises(ValueError):
            create_store(self.db, 'other', 'sha', teams, fixtures,
                         results, deductions)
        meta = check_compatible(self.db)
        self.assertEqual(meta['store_id'], 'store-identity')
        leftovers = [p.name for p in self.root.iterdir() if p.name.endswith('.tmp')]
        self.assertEqual(leftovers, [])

    def test_foreign_file_is_rejected(self):
        foreign = self.root / 'foreign.db'
        foreign.write_text('not a database')
        with self.assertRaises((ValueError, sqlite3.Error)):
            check_compatible(foreign)

    def test_bitemporal_rows_keep_history_queryable(self):
        self.apply_payload({'batch_id': 'b1', 'operations': [
            {'fixture': 'm', 'left': 10, 'right': 30, 'expected': [2, 0],
             'replacement': [0, 1]},
            {'fixture': 'm', 'left': 20, 'right': 25, 'expected': [0, 1],
             'replacement': None},
        ]})
        conn = sqlite3.connect(self.db)
        try:
            current = conn.execute(
                'SELECT left_time,right_time,home_goals,away_goals '
                'FROM results_rows WHERE sys_to IS NULL ORDER BY left_time'
            ).fetchall()
            self.assertEqual(current, [
                (0, 10, 2, 0), (10, 20, 0, 1), (20, 25, None, None),
                (25, 30, 0, 1), (30, None, 2, 0)])
            # historical rows keep their original business interval; they are
            # only closed by knowledge sequence, never reshaped in place
            closed = conn.execute(
                'SELECT sys_from,sys_to,left_time,right_time '
                'FROM results_rows WHERE sys_to IS NOT NULL').fetchall()
            self.assertEqual(closed, [(0, 1, 0, None)])
            current_count = conn.execute(
                'SELECT COUNT(*) FROM results_rows WHERE sys_to IS NULL').fetchone()[0]
            self.assertEqual(current_count, 5)
        finally:
            conn.close()
        # older knowledge still sees the single frozen segment
        self.assertEqual(len(query(self.db, 0, 0, INF)), 1)

    def test_query_is_read_only(self):
        before = self.db.read_bytes()
        query(self.db, 0, 0, INF)
        query(self.db, 0, 0, 5)
        self.assertEqual(self.db.read_bytes(), before)

    def test_failed_batch_commits_no_rows_or_sequence(self):
        with self.assertRaises(ValueError):
            self.apply_payload({'batch_id': 'bad', 'operations': [
                {'fixture': 'm', 'left': 0, 'right': 5, 'expected': [2, 0],
                 'replacement': [9, 9]},
                {'fixture': 'm', 'left': 0, 'right': 5, 'expected': [1, 1],
                 'replacement': [1, 1]},
            ]})
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT value FROM meta WHERE key='knowledge_seq'").fetchone()[0], '0')
            self.assertEqual(conn.execute(
                'SELECT COUNT(*) FROM batches').fetchone()[0], 0)
            self.assertEqual(conn.execute(
                'SELECT COUNT(*) FROM results_rows WHERE sys_from>0').fetchone()[0], 0)
        finally:
            conn.close()

    def test_reshaped_infinite_row_stays_infinite_outside_range(self):
        self.apply_payload({'batch_id': 'b', 'operations': [
            {'fixture': 'm', 'left': 5, 'right': 10, 'expected': [2, 0],
             'replacement': [1, 1]}]})
        conn = sqlite3.connect(self.db)
        try:
            tail = conn.execute(
                'SELECT left_time,right_time FROM results_rows '
                "WHERE sys_to IS NULL ORDER BY left_time DESC LIMIT 1").fetchone()
            self.assertEqual(tail, (10, None))
        finally:
            conn.close()

    def test_repeated_cuts_in_one_batch_never_corrupt_old_knowledge(self):
        # the single frozen [0, INF) row is cut twice inside one batch; the
        # closed historical row must keep its full interval at k=0
        self.apply_payload({'batch_id': 'b1', 'operations': [
            {'fixture': 'm', 'left': 10, 'right': 30, 'expected': [2, 0],
             'replacement': [0, 1]},
            {'fixture': 'm', 'left': 20, 'right': 25, 'expected': [0, 1],
             'replacement': None},
        ]})
        at_zero = query(self.db, 0, 0, INF)
        self.assertEqual(len(at_zero), 1)
        self.assertIsNone(at_zero[0]['right'])
        self.assertEqual(at_zero[0]['result']['completed'], 1)
        self.assertEqual(at_zero[0]['result']['pending_fixtures'], [])
        conn = sqlite3.connect(self.db)
        try:
            closed = conn.execute(
                'SELECT left_time,right_time FROM results_rows '
                'WHERE sys_to IS NOT NULL').fetchall()
            self.assertEqual(closed, [(0, None)])
        finally:
            conn.close()
        # and the current state still exposes the five expected regions
        current = query(self.db, 1, 0, 40)
        self.assertEqual(
            [(s['left'], s['right'], s['result']['completed']) for s in current],
            [(0, 10, 1), (10, 20, 1), (20, 25, 0), (25, 30, 1), (30, 40, 1)])


class Concurrency(unittest.TestCase):
    def test_two_processes_conflicting_on_same_old_value(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        db = root / 'store.db'
        teams, fixtures, results, deductions = parse(source())
        create_store(db, 'sid', 'sha', teams, fixtures, results, deductions)
        batches = {}
        for name, replacement in (('a', [4, 0]), ('b', [0, 4])):
            path = root / (name + '.json')
            path.write_text(json.dumps({'batch_id': name, 'operations': [
                {'fixture': 'm', 'left': 0, 'right': 100,
                 'expected': [2, 0], 'replacement': replacement}]}))
            batches[name] = path
        gate = root / 'gate'
        runner = root / 'runner.py'
        runner.write_text(
            'import os,subprocess,sys,time\n'
            'db,batch,out,gate=sys.argv[1:5]\n'
            'for _ in range(600):\n'
            '    if os.path.exists(gate): break\n'
            '    time.sleep(0.01)\n'
            'p=subprocess.run([sys.executable,"-m","league_history.cli",'
            '"apply","--db",db,"--batch",batch,"--output",out],'
            'capture_output=True,text=True)\n'
            'sys.exit(p.returncode)\n')
        env = dict(os.environ)
        src = str(Path(__file__).resolve().parents[1] / 'src')
        env['PYTHONPATH'] = src + (os.pathsep + env['PYTHONPATH']
                                   if env.get('PYTHONPATH') else '')
        procs = [subprocess.Popen(
            [sys.executable, str(runner), str(db), str(batches[name]),
             str(root / (name + 'out.json')), str(gate)], env=env)
            for name in ('a', 'b')]
        gate.write_text('go')
        codes = sorted(p.wait() for p in procs)
        self.assertEqual(codes, [0, 2])
        conn = sqlite3.connect(db)
        try:
            self.assertEqual(conn.execute(
                "SELECT value FROM meta WHERE key='knowledge_seq'").fetchone()[0], '1')
            winners = conn.execute('SELECT batch_id FROM batches').fetchall()
            self.assertEqual(len(winners), 1)
        finally:
            conn.close()
        outputs = [p for p in root.glob('*out.json')]
        self.assertEqual(len(outputs), 1)
        self.assertEqual(json.loads(outputs[0].read_text())['knowledge_seq'], 1)


if __name__ == '__main__':
    unittest.main()
