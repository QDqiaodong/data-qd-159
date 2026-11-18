"""Business tests for league-suspension.

A deliberately simple, independent brute-force replay re-derives every
event and per-case balance by re-walking the whole timeline per fixture,
so the expected results do not derive from the heap/set engine under test.
Randomized fuzz instances cross-check the two implementations, and the
fixed scenarios pin every contract edge case.
"""
import random
import unittest

from league_suspension.engine import replay
from league_suspension.payloads import validate_discipline

TEAMS = ['A', 'B', 'C']
FIXTURES = {
    'm1': ('A', 'C'), 'm2': ('A', 'C'),
    'm3': ('B', 'C'), 'm4': ('B', 'C')}
# The acceptance schedule: m2 is the pending fixture.
COMPLETED = {'m1', 'm3', 'm4'}
ALL_COMPLETED = {'m1', 'm2', 'm3', 'm4'}


def discipline(players, order, regs, issues, revokes, appearances):
    return {'schema_version': 1, 'players': players,
            'fixture_order': order, 'registrations': regs,
            'issues': issues, 'revocations': revokes,
            'appearances': appearances}


def order(seq_by_fixture):
    return [{'fixture': f, 'seq': s} for f, s in seq_by_fixture.items()]


def parse_and_run(payload, fixtures=FIXTURES, completed=COMPLETED):
    parsed = validate_discipline(payload, TEAMS, fixtures, completed)
    return replay(fixtures, completed, parsed)


def brute_force(payload, fixtures, completed):
    """Independent reference: rebuild all state from scratch per fixture."""
    order_map = {row['fixture']: row['seq']
                 for row in payload['fixture_order']}
    appearance_map = {row['fixture']: row['players']
                      for row in payload['appearances']}
    # Cases start empty: they only exist once their issuance seq is reached.
    cases = {row['case_id']: {'case_id': row['case_id'],
                              'player': row['player'],
                              'issued_seq': row['seq'],
                              'revoked_seq': None, 'imposed': row['games'],
                              'served': 0, 'waived': 0,
                              'remaining': row['games']}
             for row in payload['issues']}
    revokes_by_seq = {}
    for row in payload['revocations']:
        revokes_by_seq.setdefault(row['seq'], []).append(row['case_id'])

    events = []
    # Walk completed fixtures in timeline order; revocations living on
    # sequences without a fixture must still take effect before the next
    # judgment point, so apply every pending revocation up to each seq.
    applied_revoke_seqs = set()

    def apply_revocations(seq):
        for revoke_seq in revokes_by_seq:
            if revoke_seq <= seq and revoke_seq not in applied_revoke_seqs:
                for case_id in revokes_by_seq[revoke_seq]:
                    case = cases[case_id]
                    case['revoked_seq'] = revoke_seq
                    case['waived'] += case['remaining']
                    case['remaining'] = 0
                applied_revoke_seqs.add(revoke_seq)

    # Only formally completed fixtures exist as judgment points; pending
    # fixtures (absent from completed) never consume a balance.
    for fixture_id in sorted(completed, key=lambda f: (order_map[f], f)):
        seq = order_map[fixture_id]
        home, away = fixtures[fixture_id]
        sides = {home, away}

        # Revocations at or before this seq take effect before kick-off;
        # the served history is retained and only the remainder is waived.
        apply_revocations(seq)

        def registration_at(player_id):
            current = None
            for row in payload['registrations']:
                if row['player'] == player_id and row['seq'] <= seq:
                    current = row['team']
            return current

        def outstanding(player_id):
            # Cases issued strictly later do not yet exist.
            live = [c for c in cases.values()
                    if c['player'] == player_id
                    and c['issued_seq'] <= seq and c['remaining'] > 0]
            live.sort(key=lambda c: (c['issued_seq'], c['case_id']))
            return live[0] if live else None

        line_up = appearance_map[fixture_id]
        present = set(line_up)
        for player_id in line_up:
            case = outstanding(player_id)
            if case is not None:
                events.append({'seq': seq, 'fixture': fixture_id,
                               'team': registration_at(player_id),
                               'player': player_id,
                               'case_id': case['case_id'],
                               'kind': 'violation'})
        for player_id in payload['players']:
            if player_id in present:
                continue
            team = registration_at(player_id)
            if team not in sides:
                continue
            case = outstanding(player_id)
            if case is None:
                continue
            case['remaining'] -= 1
            case['served'] += 1
            events.append({'seq': seq, 'fixture': fixture_id,
                           'team': team, 'player': player_id,
                           'case_id': case['case_id'], 'kind': 'served'})

    # Revocations after the last completed fixture still waive balances and
    # must appear in the final per-case rows.
    last_seq = max((order_map[f] for f in completed), default=0)
    for revoke_seq in sorted(revokes_by_seq):
        if revoke_seq <= last_seq:
            continue
        for case_id in revokes_by_seq[revoke_seq]:
            case = cases[case_id]
            case['revoked_seq'] = revoke_seq
            case['waived'] += case['remaining']
            case['remaining'] = 0

    events.sort(key=lambda e: (e['seq'], e['fixture'], e['player']))
    rows = []
    for case_id in sorted(cases):
        c = cases[case_id]
        rows.append({'case_id': case_id, 'player': c['player'],
                     'issued_seq': c['issued_seq'],
                     'revoked_seq': c['revoked_seq'],
                     'imposed': c['imposed'], 'served': c['served'],
                     'waived': c['waived'], 'remaining': c['remaining']})
    return events, rows


def assert_matches_bruteforce(testcase, payload, fixtures=FIXTURES,
                              completed=COMPLETED, teams=TEAMS):
    parsed = validate_discipline(payload, teams, fixtures, completed)
    engine_events, engine_cases = replay(fixtures, completed, parsed)
    brute_events, brute_cases = brute_force(payload, fixtures, completed)
    testcase.assertEqual(engine_events, brute_events)
    testcase.assertEqual(engine_cases, brute_cases)
    for row in engine_cases:
        testcase.assertEqual(
            row['imposed'],
            row['served'] + row['waived'] + row['remaining'])
    return engine_events, engine_cases


class AcceptanceScenario(unittest.TestCase):
    """The contract's worked example, expressed from end to end."""

    def payload(self):
        # m2 is pending: fixture_order still lists it, but no appearance row.
        return discipline(
            players=['p', 'q'],
            order=order({'m1': 1, 'm2': 2, 'm3': 3, 'm4': 4}),
            regs=[{'player': 'p', 'team': 'A', 'seq': 1},
                  {'player': 'p', 'team': 'B', 'seq': 3},
                  {'player': 'q', 'team': 'C', 'seq': 1}],
            issues=[{'case_id': 'x', 'player': 'p', 'seq': 1, 'games': 2},
                    {'case_id': 'y', 'player': 'p', 'seq': 3, 'games': 2}],
            revokes=[{'case_id': 'x', 'seq': 4}],
            appearances=[{'fixture': 'm1', 'players': ['q']},
                         {'fixture': 'm3', 'players': ['p', 'q']},
                         {'fixture': 'm4', 'players': ['q']}])

    def test_acceptance_values(self):
        events, cases = parse_and_run(self.payload())
        self.assertEqual(events, [
            {'seq': 1, 'fixture': 'm1', 'team': 'A', 'player': 'p',
             'case_id': 'x', 'kind': 'served'},
            {'seq': 3, 'fixture': 'm3', 'team': 'B', 'player': 'p',
             'case_id': 'x', 'kind': 'violation'},
            {'seq': 4, 'fixture': 'm4', 'team': 'B', 'player': 'p',
             'case_id': 'y', 'kind': 'served'}])
        by_id = {row['case_id']: row for row in cases}
        self.assertEqual(
            (by_id['x']['served'], by_id['x']['waived'],
             by_id['x']['remaining']), (1, 1, 0))
        self.assertEqual(
            (by_id['y']['served'], by_id['y']['waived'],
             by_id['y']['remaining']), (1, 0, 1))

    def test_pending_fixture_consumes_nothing(self):
        payload = self.payload()
        completed = {'m1', 'm3', 'm4'}
        payload['appearances'] = [row for row in payload['appearances']
                                  if row['fixture'] in completed]
        events, _ = parse_and_run(payload, completed=completed)
        # m2 (A-C, seq 2) is pending: no event between seq 1 and seq 3.
        self.assertEqual([e['fixture'] for e in events],
                         ['m1', 'm3', 'm4'])

    def test_matches_bruteforce(self):
        assert_matches_bruteforce(self, self.payload())


class SameSeqTieBreak(unittest.TestCase):
    FIXTURES = {'m1': ('A', 'B'), 'm2': ('A', 'C')}

    def test_two_issues_same_seq_pick_lowest_case_id(self):
        completed = {'m1', 'm2'}
        payload = discipline(
            players=['p'],
            order=order({'m1': 5, 'm2': 6}),
            regs=[{'player': 'p', 'team': 'A', 'seq': 1}],
            issues=[{'case_id': 'zb', 'player': 'p', 'seq': 5,
                     'games': 1},
                    {'case_id': 'aa', 'player': 'p', 'seq': 5,
                     'games': 1}],
            revokes=[],
            appearances=[{'fixture': 'm1', 'players': []},
                         {'fixture': 'm2', 'players': []}])
        events, cases = parse_and_run(
            payload, fixtures=self.FIXTURES, completed=completed)
        # Unicode code-point ordering: 'aa' < 'zb' at the same issued seq.
        self.assertEqual([e['case_id'] for e in events], ['aa', 'zb'])
        self.assertEqual(sorted(c['case_id'] for c in cases),
                         ['aa', 'zb'])
        assert_matches_bruteforce(
            self, payload, fixtures=self.FIXTURES, completed=completed)


class RevokeNonFirstCase(unittest.TestCase):
    FIXTURES = {'m1': ('A', 'B'), 'm2': ('A', 'C'), 'm3': ('B', 'C')}

    def test_revoke_outstanding_second_case_keeps_first_open(self):
        payload = discipline(
            players=['p'],
            order=order({'m1': 1, 'm2': 2, 'm3': 3}),
            regs=[{'player': 'p', 'team': 'A', 'seq': 1}],
            issues=[{'case_id': 'c1', 'player': 'p', 'seq': 1,
                     'games': 2},
                    {'case_id': 'c2', 'player': 'p', 'seq': 1,
                     'games': 3}],
            revokes=[{'case_id': 'c2', 'seq': 2}],
            appearances=[{'fixture': 'm1', 'players': []},
                         {'fixture': 'm2', 'players': []},
                         {'fixture': 'm3', 'players': []}])
        completed = {'m1', 'm2', 'm3'}
        events, cases = parse_and_run(
            payload, fixtures=self.FIXTURES, completed=completed)
        by_id = {row['case_id']: row for row in cases}
        # seq1 serves c1; seq2 revokes c2 (waives 3); seq3 finishes c1.
        self.assertEqual([e['case_id'] for e in events],
                         ['c1', 'c1'])
        self.assertEqual(by_id['c1'],
                         {'case_id': 'c1', 'player': 'p', 'issued_seq': 1,
                          'revoked_seq': None, 'imposed': 2, 'served': 2,
                          'waived': 0, 'remaining': 0})
        self.assertEqual(by_id['c2'],
                         {'case_id': 'c2', 'player': 'p', 'issued_seq': 1,
                          'revoked_seq': 2, 'imposed': 3, 'served': 0,
                          'waived': 3, 'remaining': 0})
        assert_matches_bruteforce(
            self, payload, fixtures=self.FIXTURES, completed=completed)

    def test_revoke_fully_served_case_waives_zero(self):
        fixtures = {'m1': ('A', 'B'), 'm2': ('A', 'C')}
        completed = {'m1', 'm2'}
        payload = discipline(
            players=['p'],
            order=order({'m1': 1, 'm2': 3}),
            regs=[{'player': 'p', 'team': 'A', 'seq': 1}],
            issues=[{'case_id': 'done', 'player': 'p', 'seq': 1,
                     'games': 1}],
            revokes=[{'case_id': 'done', 'seq': 3}],
            appearances=[{'fixture': 'm1', 'players': []},
                         {'fixture': 'm2', 'players': []}])
        _, cases = parse_and_run(
            payload, fixtures=fixtures, completed=completed)
        row = cases[0]
        self.assertEqual((row['served'], row['waived'],
                          row['remaining']), (1, 0, 0))
        self.assertEqual(row['revoked_seq'], 3)
        assert_matches_bruteforce(
            self, payload, fixtures=fixtures, completed=completed)


class DeregisterReregister(unittest.TestCase):
    def test_balance_survives_deregistration_and_transfer(self):
        payload = discipline(
            players=['p'],
            order=order({'m1': 1, 'm2': 2, 'm3': 3, 'm4': 4}),
            regs=[{'player': 'p', 'team': 'A', 'seq': 1},
                  {'player': 'p', 'team': None, 'seq': 2},
                  {'player': 'p', 'team': 'B', 'seq': 3}],
            issues=[{'case_id': 'c', 'player': 'p', 'seq': 1,
                     'games': 3}],
            revokes=[],
            appearances=[{'fixture': f, 'players': []}
                         for f in ('m1', 'm2', 'm3', 'm4')])
        events, cases = parse_and_run(payload, completed=ALL_COMPLETED)
        # Served at m1 (A); nothing while deregistered (m2, an A-C fixture);
        # the balance follows p and resumes at B for m3/m4.
        self.assertEqual([(e['seq'], e['team']) for e in events],
                         [(1, 'A'), (3, 'B'), (4, 'B')])
        self.assertEqual(cases[0]['served'], 3)
        assert_matches_bruteforce(self, payload, completed=ALL_COMPLETED)


class ShuffledInputAndSparseSeqs(unittest.TestCase):
    def _payload(self):
        # Players stay on one fixture side for the whole timeline, so every
        # appearance reference stays valid regardless of array ordering.
        return discipline(
            players=['p0', 'p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7'],
            order=[{'fixture': 'm4', 'seq': 900},
                   {'fixture': 'm2', 'seq': 300},
                   {'fixture': 'm1', 'seq': 100},
                   {'fixture': 'm3', 'seq': 700}],
            regs=[{'player': f'p{i}',
                   'team': 'A' if i % 2 == 0 else 'C', 'seq': 50}
                  for i in range(8)],
            issues=[{'case_id': f'k{i}', 'player': f'p{i}',
                     'seq': 99 + i, 'games': 1 + (i % 2)}
                    for i in range(8)],
            revokes=[{'case_id': 'k3', 'seq': 350}],
            appearances=[{'fixture': 'm1', 'players': ['p0']},
                         {'fixture': 'm2', 'players': []},
                         {'fixture': 'm3', 'players': ['p1']},
                         {'fixture': 'm4', 'players': []}])

    def test_shuffled_arrays_identical_business_output(self):
        payload = self._payload()
        shuffled = dict(payload)
        rng = random.Random(42)
        for key in ('fixture_order', 'registrations', 'issues',
                    'revocations', 'appearances', 'players'):
            shuffled[key] = list(payload[key])
            rng.shuffle(shuffled[key])
        first_events, first_cases = parse_and_run(
            payload, completed=ALL_COMPLETED)
        second_events, second_cases = parse_and_run(
            shuffled, completed=ALL_COMPLETED)
        self.assertEqual(first_events, second_events)
        self.assertEqual(first_cases, second_cases)
        assert_matches_bruteforce(self, payload, completed=ALL_COMPLETED)
        assert_matches_bruteforce(self, shuffled, completed=ALL_COMPLETED)

    def test_many_clean_players_never_create_events(self):
        # 200 players with no balance across sparse sequence numbers:
        # fixtures must not iterate the whole population.
        players = [f'clean{i}' for i in range(200)]
        payload = discipline(
            players=players + ['debtor'],
            order=order({'m1': 10, 'm2': 1_000_000_000,
                         'm3': 500_000_000, 'm4': 777}),
            regs=[{'player': p, 'team': TEAMS[i % 3], 'seq': 1}
                  for i, p in enumerate(players)]
            + [{'player': 'debtor', 'team': 'A', 'seq': 1}],
            issues=[{'case_id': 'only', 'player': 'debtor',
                     'seq': 1, 'games': 3}],
            revokes=[],
            appearances=[{'fixture': f, 'players': []}
                         for f in ('m1', 'm2', 'm3', 'm4')])
        events, cases = parse_and_run(payload, completed=ALL_COMPLETED)
        # The debtor is on A and therefore only eligible at the A-C games.
        self.assertEqual({e['fixture'] for e in events}, {'m1', 'm2'})
        self.assertTrue(all(e['player'] == 'debtor' for e in events))
        self.assertEqual(cases[0]['remaining'], 1)
        assert_matches_bruteforce(self, payload, completed=ALL_COMPLETED)


class ValidationErrors(unittest.TestCase):
    def _base(self):
        return discipline(
            players=['p'],
            order=order({'m1': 1, 'm2': 2, 'm3': 3, 'm4': 4}),
            regs=[{'player': 'p', 'team': 'A', 'seq': 1}],
            issues=[], revokes=[],
            appearances=[{'fixture': f, 'players': []}
                         for f in ('m1', 'm2', 'm3', 'm4')])

    def test_appearance_on_wrong_team_rejected(self):
        payload = self._base()
        # p is registered at A; m3 is B vs C, so p cannot appear there.
        payload['appearances'] = [
            {'fixture': 'm3', 'players': ['p']}] + [
            {'fixture': f, 'players': []}
            for f in ('m1', 'm2', 'm4')]
        parsed = validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)
        with self.assertRaises(ValueError):
            replay(FIXTURES, ALL_COMPLETED, parsed)

    def test_unregistered_appearance_rejected(self):
        payload = self._base()
        # p appears at m1 but holds no registration at any seq.
        payload['registrations'] = []
        payload['appearances'] = [
            {'fixture': 'm1', 'players': ['p']}] + [
            {'fixture': f, 'players': []}
            for f in ('m2', 'm3', 'm4')]
        with self.assertRaises(ValueError):
            self._replay(payload)

    def _replay(self, payload):
        parsed = validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)
        return replay(FIXTURES, ALL_COMPLETED, parsed)

    def test_duplicate_player(self):
        payload = self._base()
        payload['players'] = ['p', 'p']
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)

    def test_boolean_seq_rejected(self):
        payload = self._base()
        payload['fixture_order'][0]['seq'] = True
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)

    def test_seq_bounds(self):
        payload = self._base()
        payload['fixture_order'][0]['seq'] = 10**9 + 1
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)
        payload['fixture_order'][0]['seq'] = 0
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)

    def test_unknown_case_revocation(self):
        payload = self._base()
        payload['revocations'] = [{'case_id': 'ghost', 'seq': 2}]
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)

    def test_revocation_not_strictly_later(self):
        payload = self._base()
        payload['issues'] = [{'case_id': 'c', 'player': 'p',
                              'seq': 3, 'games': 1}]
        payload['revocations'] = [{'case_id': 'c', 'seq': 3}]
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)

    def test_double_revocation(self):
        payload = self._base()
        payload['issues'] = [{'case_id': 'c', 'player': 'p',
                              'seq': 1, 'games': 2}]
        payload['revocations'] = [{'case_id': 'c', 'seq': 2},
                                  {'case_id': 'c', 'seq': 3}]
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)

    def test_appearance_row_for_pending_fixture_rejected(self):
        payload = self._base()
        completed = {'m2', 'm3', 'm4'}
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, completed)

    def test_missing_appearance_row_rejected(self):
        payload = self._base()
        payload['appearances'].pop()
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)

    def test_duplicate_appearance_in_one_fixture(self):
        payload = self._base()
        payload['appearances'][0]['players'] = ['p', 'p']
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)

    def test_two_registrations_same_player_same_seq(self):
        payload = self._base()
        payload['registrations'].append(
            {'player': 'p', 'team': 'B', 'seq': 1})
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)

    def test_team_plays_twice_on_one_seq(self):
        payload = self._base()
        # m1 (A-B) and m4 (A-B) cannot share a sequence number.
        for row in payload['fixture_order']:
            if row['fixture'] in ('m1', 'm4'):
                row['seq'] = 1
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)

    def test_games_bounds_and_bool(self):
        payload = self._base()
        payload['issues'] = [{'case_id': 'c', 'player': 'p',
                              'seq': 1, 'games': 0}]
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)
        payload['issues'][0]['games'] = True
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)

    def test_issue_to_unregistered_known_player_is_legal(self):
        payload = self._base()
        payload['registrations'] = []
        payload['issues'] = [{'case_id': 'c', 'player': 'p',
                              'seq': 1, 'games': 1}]
        # No appearances involve p; the balance simply stays open.
        parsed = validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)
        _, cases = replay(FIXTURES, ALL_COMPLETED, parsed)
        self.assertEqual(cases[0]['remaining'], 1)

    def test_unknown_top_level_field(self):
        payload = self._base()
        payload['extra'] = []
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)

    def test_wrong_schema_version(self):
        payload = self._base()
        payload['schema_version'] = 2
        with self.assertRaises(ValueError):
            validate_discipline(payload, TEAMS, FIXTURES, ALL_COMPLETED)


class EmptyAndCleanAppearances(unittest.TestCase):
    FIXTURES = {'m1': ('A', 'B')}
    COMPLETED = {'m1'}

    def _payload(self, players, line_up):
        return discipline(
            players=players,
            order=order({'m1': 1}),
            regs=[{'player': p, 'team': 'A', 'seq': 1} for p in players],
            issues=[], revokes=[],
            appearances=[{'fixture': 'm1', 'players': line_up}])

    def test_no_cases_no_events(self):
        payload = self._payload(['p'], ['p'])
        events, cases = parse_and_run(
            payload, fixtures=self.FIXTURES, completed=self.COMPLETED)
        self.assertEqual(events, [])
        self.assertEqual(cases, [])

    def test_indebted_player_absent_clean_player_appears(self):
        # The clean player's appearance is legal; only the absent debtor
        # serves a game, and the appearance check still runs for the clean
        # player who has no balance.
        payload = self._payload(['clean', 'debtor'], ['clean'])
        payload['issues'] = [{'case_id': 'k', 'player': 'debtor',
                              'seq': 1, 'games': 1}]
        events, cases = parse_and_run(
            payload, fixtures=self.FIXTURES, completed=self.COMPLETED)
        self.assertEqual(events, [
            {'seq': 1, 'fixture': 'm1', 'team': 'A', 'player': 'debtor',
             'case_id': 'k', 'kind': 'served'}])
        self.assertEqual(cases[0]['remaining'], 0)
        assert_matches_bruteforce(
            self, payload, fixtures=self.FIXTURES,
            completed=self.COMPLETED)

    def test_empty_line_up_still_serves_balance(self):
        payload = self._payload(['debtor'], [])
        payload['issues'] = [{'case_id': 'k', 'player': 'debtor',
                              'seq': 1, 'games': 1}]
        events, _ = parse_and_run(
            payload, fixtures=self.FIXTURES, completed=self.COMPLETED)
        self.assertEqual([e['kind'] for e in events], ['served'])


class FuzzAgainstBruteForce(unittest.TestCase):
    def test_randomized_instances(self):
        rng = random.Random(20261118)
        teams = ['A', 'B', 'C', 'D']
        fixtures = {f'm{i}': (teams[i % 4], teams[(i + 1) % 4])
                    for i in range(1, 9)}
        for trial in range(60):
            seqs = rng.sample(range(1, 40), k=len(fixtures))
            fixture_order = [{'fixture': f, 'seq': s}
                             for f, s in zip(fixtures, seqs)]
            # Keep one-team-one-fixture-per-seq by construction: retry on
            # clashes, mirroring the input constraint rather than relaxing it.
            used = {}
            clash = False
            for row in fixture_order:
                home, away = fixtures[row['fixture']]
                for team in (home, away):
                    if (team, row['seq']) in used:
                        clash = True
                    used[(team, row['seq'])] = row['fixture']
            if clash:
                continue
            completed = set(rng.sample(sorted(fixtures),
                                       k=rng.randrange(1, len(fixtures) + 1)))
            players = [f'p{i}' for i in range(rng.randrange(1, 12))]
            regs = []
            for player in players:
                current = None
                for s in sorted(rng.sample(range(1, 40),
                                           k=rng.randrange(0, 5))):
                    if rng.random() < 0.2:
                        team = None
                    else:
                        team = rng.choice(teams)
                    if team == current:
                        continue
                    current = team
                    regs.append({'player': player, 'team': team, 'seq': s})

            def team_at(player, seq):
                current = None
                for row in regs:
                    if row['player'] == player and row['seq'] <= seq:
                        current = row['team']
                return current

            issues = []
            case_seq = {}
            for i in range(rng.randrange(0, 10)):
                player = rng.choice(players)
                s = rng.randrange(1, 40)
                cid = f'case-{trial}-{i}'
                issues.append({'case_id': cid, 'player': player,
                               'seq': s, 'games': rng.randrange(1, 4)})
                case_seq[cid] = (player, s)
            revokes = []
            for cid, (_, s) in case_seq.items():
                if s < 39 and rng.random() < 0.3:
                    revokes.append({'case_id': cid,
                                    'seq': rng.randrange(s + 1, 40)})
            appearances = []
            for fixture_id in completed:
                seq = next(r['seq'] for r in fixture_order
                           if r['fixture'] == fixture_id)
                home, away = fixtures[fixture_id]
                line_up = []
                for player in players:
                    if team_at(player, seq) in (home, away) \
                            and rng.random() < 0.4:
                        line_up.append(player)
                appearances.append({'fixture': fixture_id,
                                    'players': line_up})
            payload = discipline(players, fixture_order, regs, issues,
                                 revokes, appearances)
            assert_matches_bruteforce(self, payload, fixtures, completed,
                                      teams=teams)


if __name__ == '__main__':
    unittest.main()
