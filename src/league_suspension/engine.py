"""Ordered replay of registrations, cases, revocations and fixtures.

The engine never materializes empty sequence numbers and never scans the
full player population per fixture.  Instead every decision row is indexed
by its sequence number once, and per-team membership sets name exactly the
registered players who still owe a suspension, so a fixture only touches
its line-up plus the two participating teams' indebted players.

Per-player case state
---------------------
Each case keeps ``imposed / served / waived / remaining`` counters.  A
player's outstanding cases live in a heap keyed by ``(issued_seq,
case_id)``; finished or revoked entries are popped lazily when the earliest
outstanding case is queried, so case ordering always follows the contract
without re-sorting on every fixture.

* Registration changes apply first at a sequence.  Balances follow the
  player: transferring a player moves the team membership, never the cases.
* Issuances and revocations apply next; a revocation moves the case's
  remaining balance into ``waived`` and keeps its ``served`` history.
* Fixtures are judged last.  An appearing player with a balance records a
  ``violation`` against the earliest outstanding case without deducting it;
  a non-appearing registered player with a balance serves one game against
  that case.  Pending fixtures consume nothing.
"""
import heapq


def _new_case(case_id, player, seq, games):
    return {'case_id': case_id, 'player': player, 'issued_seq': seq,
            'revoked_seq': None, 'imposed': games, 'served': 0,
            'waived': 0, 'remaining': games}


def replay(fixtures, completed, discipline):
    """Replay the validated discipline snapshot.

    ``fixtures`` maps fixture id to ``(home, away)`` and ``completed`` is
    the set of fixture ids with a formal result.  Returns
    ``(events, case_rows)`` in the report's canonical order.
    """
    # Index every decision by sequence number in a single pass; only
    # sequence numbers that carry work are ever visited.
    buckets = {}

    def bucket(seq):
        entry = buckets.get(seq)
        if entry is None:
            entry = {'regs': [], 'issues': [], 'revokes': [], 'fixtures': []}
            buckets[seq] = entry
        return entry

    for player_id, team, seq in discipline.registrations:
        bucket(seq)['regs'].append((player_id, team))
    for case_id, player_id, seq, games in discipline.issues:
        bucket(seq)['issues'].append((case_id, player_id, games))
    for case_id, seq in discipline.revocations:
        bucket(seq)['revokes'].append(case_id)
    for fixture_id, seq in discipline.fixture_order.items():
        if fixture_id in completed:
            bucket(seq)['fixtures'].append(fixture_id)

    cases = {}
    heaps = {}
    # player -> current team (absent/None means unregistered at kick-off).
    registered = {}
    # team -> players registered there who currently have an active balance.
    team_owed = {}
    events = []

    def earliest(player_id):
        """Return the earliest outstanding case, lazily dropping dead ones."""
        heap = heaps.get(player_id)
        if heap is None:
            return None
        while heap:
            _, case_id = heap[0]
            case = cases[case_id]
            if case['remaining'] > 0:
                return case
            heapq.heappop(heap)
        return None

    def refresh(player_id):
        """Re-sync the player's indebted-team membership after a balance change."""
        team = registered.get(player_id)
        if team is None:
            return
        members = team_owed.setdefault(team, set())
        if earliest(player_id) is None:
            members.discard(player_id)
        else:
            members.add(player_id)

    def apply_registration(player_id, team):
        old_team = registered.get(player_id)
        if old_team is not None:
            team_owed.get(old_team, set()).discard(player_id)
        registered[player_id] = team
        if team is not None and earliest(player_id) is not None:
            team_owed.setdefault(team, set()).add(player_id)

    def apply_issue(case_id, player_id, games, seq):
        if case_id in cases:
            raise ValueError('duplicate case id: ' + case_id)
        case = _new_case(case_id, player_id, seq, games)
        cases[case_id] = case
        heap = heaps.setdefault(player_id, [])
        heapq.heappush(heap, (seq, case_id))
        team = registered.get(player_id)
        if team is not None:
            team_owed.setdefault(team, set()).add(player_id)

    def apply_revocation(case_id, seq):
        case = cases.get(case_id)
        if case is None:
            raise ValueError('revocation references unknown case: ' + case_id)
        if case['revoked_seq'] is not None:
            raise ValueError('case revoked more than once: ' + case_id)
        case['revoked_seq'] = seq
        case['waived'] += case['remaining']
        case['remaining'] = 0
        refresh(case['player'])

    for seq in sorted(buckets):
        entry = buckets[seq]
        # 1) registration changes, 2) issuances and revocations,
        # 3) fixture judgments — all before any match at this seq kicks off.
        for player_id, team in entry['regs']:
            apply_registration(player_id, team)
        for case_id, player_id, games in entry['issues']:
            apply_issue(case_id, player_id, games, seq)
        for case_id in entry['revokes']:
            apply_revocation(case_id, seq)

        for fixture_id in sorted(entry['fixtures']):
            home, away = fixtures[fixture_id]
            sides = (home, away)
            line_up = discipline.appearances[fixture_id]

            present = set()
            for player_id in line_up:
                team = registered.get(player_id)
                if team not in sides:
                    raise ValueError(
                        'appearing player %s is not registered to a side of '
                        'fixture %s at seq %d' % (player_id, fixture_id, seq))
                present.add(player_id)

            # Appearing while still owing: violation on the earliest case,
            # no balance deducted.
            for player_id in line_up:
                case = earliest(player_id)
                if case is not None:
                    events.append({
                        'seq': seq, 'fixture': fixture_id,
                        'team': registered[player_id],
                        'player': player_id, 'case_id': case['case_id'],
                        'kind': 'violation'})

            # Non-appearing registered players with a balance serve one game.
            candidates = set()
            for team in sides:
                candidates.update(team_owed.get(team, ()))
            for player_id in candidates:
                if player_id in present:
                    continue
                case = earliest(player_id)
                if case is None or registered.get(player_id) not in sides:
                    continue
                case['remaining'] -= 1
                case['served'] += 1
                events.append({
                    'seq': seq, 'fixture': fixture_id,
                    'team': registered[player_id],
                    'player': player_id, 'case_id': case['case_id'],
                    'kind': 'served'})
                if case['remaining'] == 0:
                    refresh(player_id)

    events.sort(key=lambda event: (event['seq'], event['fixture'],
                                   event['player']))

    case_rows = []
    for case_id in sorted(cases):
        case = cases[case_id]
        if case['imposed'] != (case['served'] + case['waived']
                               + case['remaining']):
            raise ValueError('case balance identity violated: ' + case_id)
        case_rows.append({
            'case_id': case_id, 'player': case['player'],
            'issued_seq': case['issued_seq'],
            'revoked_seq': case['revoked_seq'],
            'imposed': case['imposed'], 'served': case['served'],
            'waived': case['waived'], 'remaining': case['remaining']})
    return events, case_rows
