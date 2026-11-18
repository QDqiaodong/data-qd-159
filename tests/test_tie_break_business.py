import json, unittest
from pathlib import Path
from league_table.model import analyze, parse
from league_table.standings import calculate
from league_table.tie_break import apply_tie_breaks, parse_profile

ROOT = Path(__file__).resolve().parents[1]
PROFILE = (['points', 'goal_difference', 'goals_for'], ['goal_difference', 'goals_for'])


def acceptance_source():
    return json.loads((ROOT / 'fixtures/input-tiebreak.json').read_text())


def snapshot(teams, fixtures, results, deductions=()):
    return {'teams': teams, 'fixtures': fixtures, 'results': results,
            'deductions': list(deductions)}


def policy(data, profile=PROFILE):
    teams, fixtures, results, deductions = parse(data)
    baseline = calculate(teams, fixtures, results, deductions)
    result, audit = apply_tie_breaks(teams, fixtures, results, deductions,
                                     baseline, profile)
    return baseline, result, audit


class TieBreakAcceptance(unittest.TestCase):
    def test_recursive_head_to_head_changes_order_vs_parent_table(self):
        baseline, result, audit = policy(acceptance_source())
        # Four teams level on deducted total points; E is its own points group.
        self.assertEqual([(r['team'], r['points']) for r in baseline['standings']],
                         [('A', 6), ('B', 6), ('C', 6), ('D', 6), ('E', 0)])
        # The fixed baseline still ranks by global GD/GF, so it breaks the
        # points tie itself; policy mode is what re-runs head-to-head ordering.
        self.assertEqual([r['rank'] for r in baseline['standings']], [1, 2, 3, 3, 5])
        # First head-to-head criterion splits a strict singleton {A}; the rest
        # remain tied and must be re-analysed from the first criterion.
        root, child = audit['nodes']
        self.assertEqual(root['team_ids'], ['A', 'B', 'C', 'D'])
        self.assertEqual(root['fixture_ids'], ['g1', 'g2', 'g3', 'g4', 'g5', 'g6'])
        self.assertEqual(root['subgroups'], [['A'], ['B', 'C', 'D']])
        self.assertEqual(child['team_ids'], ['B', 'C', 'D'])
        self.assertEqual(child['fixture_ids'], ['g2', 'g3', 'g6'])
        self.assertEqual(child['subgroups'], [['B'], ['D'], ['C']])
        # Recalculation changes the ordering versus the parent restricted rows:
        # parent mini-table GD is (-2,-2,-2); the child mini-table GD is (1,-1,0).
        h2h = {c['criterion']: c['values']
               for c in root['criterion_values'] if c['source'] == 'head_to_head'}
        self.assertEqual([h2h['goal_difference'][t] for t in ('B', 'C', 'D')], [-2, -2, -2])
        child_h2h = {c['criterion']: c['values']
                     for c in child['criterion_values'] if c['source'] == 'head_to_head'}
        self.assertEqual([child_h2h['goal_difference'][t] for t in ('B', 'C', 'D')], [1, -1, 0])
        # Final order and distinct ranks after the whole tie is resolved.
        self.assertEqual([(r['team'], r['rank']) for r in result['standings']],
                         [('A', 1), ('B', 2), ('D', 3), ('C', 4), ('E', 5)])

    def test_audit_nodes_carry_profile_ordered_sourced_criterion_values(self):
        _, _, audit = policy(acceptance_source())
        self.assertEqual(audit['profile'],
                         {'head_to_head': ['points', 'goal_difference', 'goals_for'],
                          'overall': ['goal_difference', 'goals_for']})
        for node in audit['nodes']:
            self.assertEqual(node['team_ids'], sorted(node['team_ids']))
            self.assertEqual(node['fixture_ids'], sorted(node['fixture_ids']))
            entries = [(c['criterion'], c['source']) for c in node['criterion_values']]
            self.assertEqual(entries, [
                ('points', 'head_to_head'), ('goal_difference', 'head_to_head'),
                ('goals_for', 'head_to_head'), ('goal_difference', 'overall'),
                ('goals_for', 'overall')])
            for criterion in node['criterion_values']:
                self.assertEqual(sorted(criterion['values']), node['team_ids'])

    def test_pending_fixtures_are_never_counted(self):
        # e1 (A-E) is pending; it must be absent from every node fixture list
        # and the pending/completed accounting is carried through unchanged.
        baseline, result, audit = policy(acceptance_source())
        self.assertEqual(result['pending_fixtures'], ['e1'])
        self.assertEqual(result['completed'], baseline['completed'])
        used = {fid for node in audit['nodes'] for fid in node['fixture_ids']}
        self.assertNotIn('e1', used)
        self.assertEqual(used, {'g1', 'g2', 'g3', 'g4', 'g5', 'g6'})

    def test_deductions_select_group_but_are_not_reapplied_in_mini_table(self):
        _, _, audit = policy(acceptance_source())
        root = audit['nodes'][0]
        points = next(c for c in root['criterion_values']
                      if c['source'] == 'head_to_head' and c['criterion'] == 'points')
        # A's 3-point deduction disappears inside the mini-table: its three
        # head-to-head wins are worth the full 9 points.
        self.assertEqual(points['values'], {'A': 9, 'B': 3, 'C': 3, 'D': 3})
        overall_points = [c for c in root['criterion_values'] if c['criterion'] == 'points']
        self.assertEqual([c['source'] for c in overall_points], ['head_to_head'])

    def test_baseline_standings_remain_untouched(self):
        data = acceptance_source()
        baseline, result, _ = policy(data)
        self.assertEqual(analyze(data), baseline)
        # Policy mode reorders rows but preserves every scalar per team.
        base_rows = {r['team']: r for r in baseline['standings']}
        for row in result['standings']:
            team = row['team']
            for key, value in base_rows[team].items():
                if key == 'rank':
                    continue
                self.assertEqual(row[key], value)

    def test_no_completed_in_group_fixtures_falls_back_to_overall(self):
        data = snapshot(
            ['A', 'B', 'C', 'X'],
            [{'id': 'p1', 'home': 'A', 'away': 'B'},
             {'id': 'p2', 'home': 'B', 'away': 'C'},
             {'id': 'x1', 'home': 'X', 'away': 'A'},
             {'id': 'x2', 'home': 'X', 'away': 'B'},
             {'id': 'x3', 'home': 'X', 'away': 'C'}],
            [{'fixture': 'x1', 'home_goals': 1, 'away_goals': 0},
             {'fixture': 'x2', 'home_goals': 2, 'away_goals': 0},
             {'fixture': 'x3', 'home_goals': 3, 'away_goals': 0}])
        baseline, result, audit = policy(data)
        node, = audit['nodes']
        self.assertEqual(node['team_ids'], ['A', 'B', 'C'])
        self.assertEqual(node['fixture_ids'], [])
        for criterion in node['criterion_values']:
            if criterion['source'] == 'head_to_head':
                self.assertEqual(set(criterion['values'].values()), {0})
        # Overall GD (global cumulative) orders the group A > B > C. X wins
        # all its games and forms its own higher points group ahead of them.
        self.assertEqual(node['subgroups'], [['A'], ['B'], ['C']])
        self.assertEqual([r['team'] for r in baseline['standings']], ['X', 'A', 'B', 'C'])
        self.assertEqual([(r['team'], r['rank']) for r in result['standings']],
                         [('X', 1), ('A', 2), ('B', 3), ('C', 4)])

    def test_all_head_to_head_values_equal_uses_overall_fallback(self):
        data = snapshot(
            ['A', 'B', 'C'],
            [{'id': 'm1', 'home': 'A', 'away': 'B'},
             {'id': 'm2', 'home': 'B', 'away': 'C'},
             {'id': 'm3', 'home': 'A', 'away': 'C'}],
            [{'fixture': 'm1', 'home_goals': 1, 'away_goals': 1},
             {'fixture': 'm2', 'home_goals': 1, 'away_goals': 1},
             {'fixture': 'm3', 'home_goals': 1, 'away_goals': 1}])
        baseline, result, audit = policy(data)
        self.assertEqual([r['rank'] for r in baseline['standings']], [1, 1, 1])
        node, = audit['nodes']
        self.assertEqual(node['fixture_ids'], ['m1', 'm2', 'm3'])
        # Every head-to-head criterion is level so the node falls straight to
        # overall values; here those are level too.
        self.assertEqual(node['subgroups'], [['A', 'B', 'C']])
        self.assertEqual([r['rank'] for r in result['standings']], [1, 1, 1])
        self.assertEqual([r['team'] for r in result['standings']], ['A', 'B', 'C'])

    def test_recursion_that_stays_tied_keeps_competition_rank(self):
        # H2H points split {A} from {B,C}; B and C stay identical on every
        # remaining criterion, so they share a competition rank with a gap.
        data = snapshot(
            ['A', 'B', 'C'],
            [{'id': 'm1', 'home': 'A', 'away': 'B'},
             {'id': 'm2', 'home': 'A', 'away': 'C'},
             {'id': 'm3', 'home': 'B', 'away': 'C'}],
            [{'fixture': 'm1', 'home_goals': 1, 'away_goals': 0},
             {'fixture': 'm2', 'home_goals': 1, 'away_goals': 0},
             {'fixture': 'm3', 'home_goals': 0, 'away_goals': 0}],
            [{'team': 'A', 'points': 5, 'reason': 'synthetic rule breach'}])
        baseline, result, audit = policy(data)
        # A's deduction levels all totals at 1 point; baseline GD still orders
        # A first while B and C are globally identical (rank 2 shared).
        self.assertEqual([(r['team'], r['rank']) for r in baseline['standings']],
                         [('A', 1), ('B', 2), ('C', 2)])
        root, child = audit['nodes']
        self.assertEqual(root['team_ids'], ['A', 'B', 'C'])
        self.assertEqual(root['fixture_ids'], ['m1', 'm2', 'm3'])
        self.assertEqual(root['subgroups'], [['A'], ['B', 'C']])
        self.assertEqual(child['team_ids'], ['B', 'C'])
        self.assertEqual(child['fixture_ids'], ['m3'])
        self.assertEqual(child['subgroups'], [['B', 'C']])
        self.assertEqual([(r['team'], r['rank']) for r in result['standings']],
                         [('A', 1), ('B', 2), ('C', 2)])

    def test_team_id_is_never_an_athletic_criterion(self):
        # With every criterion exhausted the tied block keeps one rank and is
        # displayed by ascending team id even though the input order is reversed.
        data = snapshot(
            ['Z', 'Y', 'X'],
            [{'id': 'm1', 'home': 'Z', 'away': 'Y'},
             {'id': 'm2', 'home': 'Y', 'away': 'X'},
             {'id': 'm3', 'home': 'Z', 'away': 'X'}],
            [{'fixture': 'm1', 'home_goals': 0, 'away_goals': 0},
             {'fixture': 'm2', 'home_goals': 0, 'away_goals': 0},
             {'fixture': 'm3', 'home_goals': 0, 'away_goals': 0}])
        _, result, audit = policy(data)
        node, = audit['nodes']
        self.assertEqual(node['team_ids'], ['X', 'Y', 'Z'])
        self.assertEqual(node['subgroups'], [['X', 'Y', 'Z']])
        self.assertEqual([r['team'] for r in result['standings']], ['X', 'Y', 'Z'])
        self.assertEqual({r['rank'] for r in result['standings']}, {1})
        # The team id appears nowhere as a criterion entry.
        self.assertTrue(all(c['criterion'] in
                            ('points', 'goal_difference', 'goals_for')
                            for c in node['criterion_values']))

    def test_no_tied_group_yields_empty_audit(self):
        data = snapshot(
            ['A', 'B'],
            [{'id': 'm1', 'home': 'A', 'away': 'B'}],
            [{'fixture': 'm1', 'home_goals': 1, 'away_goals': 0}])
        baseline, result, audit = policy(data)
        self.assertEqual(audit, {'profile': {'head_to_head': PROFILE[0],
                                             'overall': PROFILE[1]}, 'nodes': []})
        self.assertEqual(result['standings'], baseline['standings'])
        self.assertEqual([r['rank'] for r in result['standings']], [1, 2])

    def test_multi_leg_fixtures_both_count_in_mini_table(self):
        data = snapshot(
            ['A', 'B'],
            [{'id': 'm1', 'home': 'A', 'away': 'B'},
             {'id': 'm2', 'home': 'B', 'away': 'A'}],
            [{'fixture': 'm1', 'home_goals': 2, 'away_goals': 2},
             {'fixture': 'm2', 'home_goals': 1, 'away_goals': 1}])
        _, result, audit = policy(data)
        # Two drawn legs leave both teams level on every global and head-to-head
        # statistic, so the single initial points group still emits one node.
        node, = audit['nodes']
        self.assertEqual(node['fixture_ids'], ['m1', 'm2'])
        h2h = {c['criterion']: c['values']
               for c in node['criterion_values'] if c['source'] == 'head_to_head'}
        self.assertEqual(h2h['points'], {'A': 2, 'B': 2})
        self.assertEqual(h2h['goal_difference'], {'A': 0, 'B': 0})
        self.assertEqual(h2h['goals_for'], {'A': 3, 'B': 3})
        # Nothing athletic separates them, so one shared block and rank 1.
        self.assertEqual(node['subgroups'], [['A', 'B']])
        self.assertEqual([(r['team'], r['rank']) for r in result['standings']],
                         [('A', 1), ('B', 1)])

    def test_overall_criterion_split_is_terminal_and_rank_gap_is_kept(self):
        # Two separate points groups; inside the tied group no head-to-head
        # result exists, so an overall GD split orders them with real ranks,
        # and the singleton points group after them takes rank 3 (gap rule).
        data = snapshot(
            ['A', 'B', 'C'],
            [{'id': 'p1', 'home': 'A', 'away': 'B'},
             {'id': 'x1', 'home': 'C', 'away': 'A'},
             {'id': 'x2', 'home': 'C', 'away': 'B'}],
            [{'fixture': 'x1', 'home_goals': 0, 'away_goals': 2},
             {'fixture': 'x2', 'home_goals': 0, 'away_goals': 1}])
        _, result, audit = policy(data)
        node, = audit['nodes']
        self.assertEqual(node['team_ids'], ['A', 'B'])
        self.assertEqual(node['fixture_ids'], [])
        self.assertEqual(node['subgroups'], [['A'], ['B']])
        self.assertEqual([(r['team'], r['rank']) for r in result['standings']],
                         [('A', 1), ('B', 2), ('C', 3)])

    def test_input_permutation_does_not_change_policy_result(self):
        data = acceptance_source()
        _, first, first_audit = policy(data)
        shuffled = {key: list(reversed(values)) for key, values in data.items()}
        _, second, second_audit = policy(shuffled)
        self.assertEqual(first, second)
        self.assertEqual(first_audit, second_audit)


class ProfileValidation(unittest.TestCase):
    def test_valid_profile(self):
        data = {'head_to_head': ['points', 'goal_difference', 'goals_for'],
                'overall': ['goal_difference', 'goals_for']}
        self.assertEqual(parse_profile(data),
                         (['points', 'goal_difference', 'goals_for'],
                          ['goal_difference', 'goals_for']))

    def test_unknown_field_rejected(self):
        for key in ('head_to_head', 'overall'):
            data = {'head_to_head': ['points'], 'overall': ['goal_difference']}
            data[key] = ['play_off_seed']
            with self.assertRaises(ValueError):
                parse_profile(data)

    def test_duplicate_field_rejected(self):
        with self.assertRaises(ValueError):
            parse_profile({'head_to_head': ['points', 'points'],
                           'overall': ['goal_difference']})
        with self.assertRaises(ValueError):
            parse_profile({'head_to_head': ['points'],
                           'overall': ['goals_for', 'goals_for']})

    def test_empty_criteria_rejected(self):
        with self.assertRaises(ValueError):
            parse_profile({'head_to_head': [], 'overall': ['goal_difference']})
        with self.assertRaises(ValueError):
            parse_profile({'head_to_head': ['points'], 'overall': []})

    def test_wrong_shape_rejected(self):
        bad_payloads = [
            {'head_to_head': 'points', 'overall': ['goal_difference']},
            {'head_to_head': ['points']},
            {'head_to_head': ['points'], 'overall': ['goal_difference'], 'extra': []},
            {'head_to_head': [1], 'overall': ['goal_difference']},
            {'head_to_head': [None], 'overall': ['goal_difference']},
        ]
        for payload in bad_payloads:
            with self.assertRaises(ValueError):
                parse_profile(payload)


if __name__ == '__main__':
    unittest.main()
