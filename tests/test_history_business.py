import json
import tempfile
import unittest
from pathlib import Path

from league_table.model import parse

from league_history.payloads import business_time, score, validate_batch
from league_history.store import apply_batch, create_store, load_frozen, query
from league_history.timeline import (
    INF,
    TMAX,
    check_expected,
    normalize,
    replace_range,
)


def source():
    return {'teams': ['A', 'B'],
            'fixtures': [{'id': 'm', 'home': 'A', 'away': 'B'}],
            'results': [{'fixture': 'm', 'home_goals': 2, 'away_goals': 0}],
            'deductions': []}


def op(fixture='m', left=0, right=10, expected=(2, 0), replacement=(1, 1)):
    return {'fixture': fixture, 'left': left,
            'right': None if right is None else right,
            'expected': None if expected is None else list(expected),
            'replacement': None if replacement is None else list(replacement)}


def batch(batch_id, operations):
    return {'batch_id': batch_id, 'operations': operations}


class TimelineEngine(unittest.TestCase):
    def test_normalize_merges_only_equal_adjacent(self):
        segs = [(0, 5, (1, 0)), (5, 8, (1, 0)), (8, 9, None), (9, INF, (1, 0))]
        self.assertEqual(normalize(segs),
                         [(0, 8, (1, 0)), (8, 9, None), (9, INF, (1, 0))])

    def test_expected_requires_whole_range_inclusive_of_holes(self):
        tl = normalize([(0, 10, (0, 1)), (10, 20, None), (20, INF, (0, 1))])
        self.assertTrue(check_expected(tl, 0, 10, (0, 1)))
        self.assertTrue(check_expected(tl, 10, 20, None))
        # endpoints are 0:1 but the interior pending hole must fail the check
        self.assertFalse(check_expected(tl, 5, 25, (0, 1)))
        # a pending expectation fails as soon as one sub-segment has a score
        self.assertFalse(check_expected(tl, 0, 20, None))
        # single instant and boundary-only probes are not special
        self.assertTrue(check_expected(tl, 10, 11, None))
        self.assertFalse(check_expected(tl, 9, 11, (0, 1)))

    def test_replace_range_keeps_outside_and_supports_withdrawal(self):
        tl = [(0, INF, (2, 0))]
        tl = replace_range(tl, 10, 30, (0, 1))
        tl = replace_range(tl, 20, 25, None)
        self.assertEqual(tl,
                         [(0, 10, (2, 0)), (10, 20, (0, 1)), (20, 25, None),
                          (25, 30, (0, 1)), (30, INF, (2, 0))])

    def test_replace_range_supports_infinite_bounds(self):
        tl = replace_range([(0, INF, (2, 0))], 100, INF, (5, 5))
        self.assertEqual(tl, [(0, 100, (2, 0)), (100, INF, (5, 5))])
        tl = replace_range(tl, 0, 50, None)
        self.assertEqual(tl[0], (0, 50, None))


class PayloadValidation(unittest.TestCase):
    def setUp(self):
        self.fixtures = parse(source())[1]

    def test_canonical_ignores_key_order_and_whitespace(self):
        payload = batch('b1', [op(left=1, right=2)])
        _, _, _, first = validate_batch(payload, self.fixtures)
        reordered = json.loads(json.dumps(payload))
        text = json.dumps(reordered, indent=5, sort_keys=True)
        _, _, _, second = validate_batch(json.loads(text), self.fixtures)
        self.assertEqual(first, second)

    def test_operation_order_is_significant(self):
        o1, o2 = op(left=1, right=2), op(left=3, right=4)
        _, _, _, first = validate_batch(batch('b', [o1, o2]), self.fixtures)
        _, _, _, second = validate_batch(batch('b', [o2, o1]), self.fixtures)
        self.assertNotEqual(first, second)

    def test_unknown_fixture_rejected(self):
        with self.assertRaises(ValueError):
            validate_batch(batch('b', [op(fixture='nope')]), self.fixtures)

    def test_business_time_and_goals_reject_bool_and_floats(self):
        for bad in (True, False, 1.0, -1, TMAX + 1):
            with self.assertRaises(ValueError):
                business_time(bad)
        for bad in (True, False, 1.0, -1, 1001):
            with self.assertRaises(ValueError):
                score([bad, 0])
        with self.assertRaises(ValueError):
            score([0])
        with self.assertRaises(ValueError):
            validate_batch(batch('b', [op(right=5, left=5)]), self.fixtures)

    def test_null_right_becomes_infinite_sentinel(self):
        _, operations, _, _ = validate_batch(
            batch('b', [op(right=None)]), self.fixtures)
        self.assertEqual(operations[0]['right'], INF)

    def test_batch_size_bounds(self):
        with self.assertRaises(ValueError):
            validate_batch(batch('b', []), self.fixtures)
        with self.assertRaises(ValueError):
            validate_batch(batch('b', [op() for _ in range(1001)]),
                           self.fixtures)

    def test_unknown_fields_and_ident_rules(self):
        payload = batch('b', [op()])
        payload['operations'][0]['extra'] = 1
        with self.assertRaises(ValueError):
            validate_batch(payload, self.fixtures)
        with self.assertRaises(ValueError):
            validate_batch({'batch_id': ' ', 'operations': [op()]},
                           self.fixtures)


class StoreBusiness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'store.db'
        teams, fixtures, results, deductions = parse(source())
        create_store(self.db, 'sid', 'digest', teams, fixtures, results, deductions)

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, payload):
        _, fixtures, _ = load_frozen(self.db)
        batch_id, operations, _, canonical = validate_batch(payload, fixtures)
        return apply_batch(self.db, batch_id, canonical, operations)

    def test_acceptance_five_segments_and_k_zero(self):
        self.apply(batch('b1', [
            op(left=10, right=30, expected=(2, 0), replacement=(0, 1)),
            op(left=20, right=25, expected=(0, 1), replacement=None),
        ]))
        at_one = query(self.db, 1, 0, 40)
        shape = [(seg['left'], seg['right'],
                  seg['result']['completed'], seg['result']['pending_fixtures'])
                 for seg in at_one]
        self.assertEqual(shape,
                         [(0, 10, 1, []), (10, 20, 1, []), (20, 25, 0, ['m']),
                          (25, 30, 1, []), (30, 40, 1, [])])
        at_zero = query(self.db, 0, 0, 40)
        self.assertEqual(len(at_zero), 1)
        self.assertEqual(at_zero[0]['left'], 0)
        self.assertEqual(at_zero[0]['right'], 40)
        self.assertEqual(at_zero[0]['result']['completed'], 1)

    def test_batch_over_pending_hole_rejected_even_when_endpoints_match(self):
        self.apply(batch('b1', [
            op(left=10, right=30, expected=(2, 0), replacement=(0, 1)),
            op(left=20, right=25, expected=(0, 1), replacement=None),
        ]))
        # both endpoints are 0:1; the interior [20,25) pending hole rejects it
        with self.assertRaises(ValueError):
            self.apply(batch('b2', [op(left=15, right=28, expected=(0, 1),
                                       replacement=(1, 1))]))
        self.assertEqual(self.apply(batch('b2', [op(
            left=40, right=50, expected=(2, 0), replacement=(3, 3))]))[0], 2)

    def test_later_batches_never_pollute_older_knowledge(self):
        self.apply(batch('b1', [op(left=0, right=5, replacement=(9, 1))]))
        self.apply(batch('b2', [op(left=0, right=5, expected=(9, 1),
                                   replacement=(8, 1))]))
        k0 = query(self.db, 0, 0, 10)[0]['result']
        k1 = query(self.db, 1, 0, 10)[0]['result']
        k2 = query(self.db, 2, 0, 10)[0]['result']
        goals_for = lambda result: next(
            row['goals_for'] for row in result['standings'] if row['team'] == 'A')
        self.assertEqual(goals_for(k0), 2)
        self.assertEqual(goals_for(k1), 9)
        self.assertEqual(goals_for(k2), 8)
        with self.assertRaises(ValueError):
            query(self.db, 3, 0, 10)

    def test_replay_returns_first_seq_without_rewrite_after_drift(self):
        payload = batch('b1', [op(left=0, right=5, replacement=(9, 9))])
        seq, replayed = self.apply(payload)
        self.assertEqual((seq, replayed), (1, False))
        # drift the current state with a different batch
        self.apply(batch('drift', [op(left=0, right=5, expected=(9, 9),
                                      replacement=(7, 7))]))
        seq_again, replayed_again = self.apply(payload)
        self.assertEqual((seq_again, replayed_again), (1, True))

    def test_same_batch_id_with_different_content_fails_and_is_not_registered(self):
        self.apply(batch('b1', [op(left=0, right=5, replacement=(9, 9))]))
        with self.assertRaises(ValueError):
            self.apply(batch('b1', [op(left=0, right=5, replacement=(6, 6))]))
        # the failed divergent content did not consume the sequence
        seq, _ = self.apply(batch('next', [op(left=6, right=7, replacement=(1, 0))]))
        self.assertEqual(seq, 2)

    def test_failed_operation_rolls_back_earlier_operations_in_batch(self):
        payload = batch('atomic', [
            op(left=200, right=210, expected=(2, 0), replacement=(8, 8)),
            op(left=0, right=5, expected=(9, 9), replacement=(1, 1)),
        ])
        with self.assertRaises(ValueError):
            self.apply(payload)
        window = query(self.db, 0, 200, 210)
        self.assertEqual(len(window), 1)
        self.assertEqual(window[0]['result']['completed'], 1)

    def test_withdrawal_is_pending_not_zero_zero(self):
        self.apply(batch('w', [op(left=0, right=5, replacement=None)]))
        seg = query(self.db, 1, 0, 5)[0]
        self.assertEqual(seg['result']['pending_fixtures'], ['m'])
        self.assertEqual(seg['result']['completed'], 0)

    def test_infinite_right_round_trips_as_null(self):
        self.apply(batch('inf', [op(left=100, right=None, replacement=(5, 5))]))
        timeline = query(self.db, 1, 0, INF)
        self.assertIsNone(timeline[-1]['right'])
        self.assertEqual(timeline[-1]['left'], 100)

    def test_adjacent_equal_complete_results_merge(self):
        self.apply(batch('mg', [
            op(left=60, right=70, replacement=(7, 7)),
            op(left=70, right=80, replacement=(7, 7)),
        ]))
        bounds = [(seg['left'], seg['right'])
                  for seg in query(self.db, 1, 55, 85)]
        self.assertIn((60, 80), bounds)

    def test_expected_null_requires_entirely_pending_range(self):
        self.apply(batch('hole', [op(left=10, right=20, replacement=None)]))
        # [10,20) is pending -> expected null ok, replacement keeps pending
        seq, _ = self.apply(batch('p1', [op(left=12, right=15, expected=None,
                                            replacement=None)]))
        self.assertEqual(seq, 2)
        with self.assertRaises(ValueError):
            self.apply(batch('p2', [op(left=9, right=11, expected=None,
                                       replacement=(1, 1))]))

    def test_store_operation_cap_is_20000(self):
        many = [op(fixture='m', left=i, right=i + 1, expected=(2, 0),
                   replacement=(1, 0)) for i in range(1000)]
        for index in range(20):
            payload = batch('cap%d' % index, many)
            for entry in payload['operations']:
                entry['left'] += (index + 1) * 1000
                entry['right'] += (index + 1) * 1000
            self.apply(payload)
        with self.assertRaises(ValueError):
            self.apply(batch('overflow', [op(left=900000, right=900001,
                                             expected=(2, 0), replacement=(1, 0))]))


class MultiFixtureStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'store.db'
        data = {'teams': ['A', 'B', 'C'], 'fixtures': [
            {'id': 'm1', 'home': 'A', 'away': 'B'},
            {'id': 'm2', 'home': 'B', 'away': 'C'}],
            'results': [{'fixture': 'm1', 'home_goals': 1, 'away_goals': 0}],
            'deductions': []}
        teams, fixtures, results, deductions = parse(data)
        create_store(self.db, 'sid', 'digest', teams, fixtures, results, deductions)

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, payload):
        _, fixtures, _ = load_frozen(self.db)
        batch_id, operations, _, canonical = validate_batch(payload, fixtures)
        return apply_batch(self.db, batch_id, canonical, operations)

    def test_union_boundaries_and_combined_standings(self):
        self.apply({'batch_id': 'b', 'operations': [
            op(fixture='m1', left=10, right=20, expected=(1, 0),
               replacement=(2, 0)),
            op(fixture='m2', left=15, right=25, expected=None,
               replacement=(0, 3)),
        ]})
        timeline = query(self.db, 1, 0, 30)
        self.assertEqual([seg['left'] for seg in timeline], [0, 10, 15, 20, 25])
        self.assertEqual(timeline[-1]['right'], 30)
        middle = timeline[2]['result']
        self.assertEqual(middle['completed'], 2)
        self.assertEqual(middle['pending_fixtures'], [])
        points = {row['team']: row['points'] for row in middle['standings']}
        self.assertEqual(points, {'A': 3, 'B': 0, 'C': 3})
        # frozen knowledge 0 keeps m2 pending across the whole line
        frozen = query(self.db, 0, 0, 30)
        self.assertEqual(len(frozen), 1)
        self.assertEqual(frozen[0]['result']['pending_fixtures'], ['m2'])


if __name__ == '__main__':
    unittest.main()
