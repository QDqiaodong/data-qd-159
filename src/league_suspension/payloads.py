"""Strict parsing and validation of the discipline snapshot JSON.

The discipline file contains exactly six top-level fields::

    {"schema_version": 1,
     "players":       [player_id, ...],
     "fixture_order": [{"fixture", "seq"}, ...],
     "registrations": [{"player", "team", "seq"}, ...],
     "issues":        [{"case_id", "player", "seq", "games"}, ...],
     "revocations":   [{"case_id", "seq"}, ...],
     "appearances":   [{"fixture", "players"}, ...]}

Input array order never denotes effect order: every decision is replayed by
ascending ``seq``.  All identifiers reuse the league-table identifier
contract; sequences are plain integers in 1..1_000_000_000 and booleans are
rejected.  Cross-references (players, teams, fixtures, cases) must all be
valid, every formal fixture id appears in ``fixture_order`` exactly once, and
``appearances`` has exactly one row per formally completed fixture.
"""
from collections import namedtuple

from league_table.common import ident, require, rows

SEQ_MIN = 1
SEQ_MAX = 1_000_000_000
GAMES_MAX = 100_000
PLAYERS_LIMIT = 50_000
RECORDS_LIMIT = 100_000

Discipline = namedtuple(
    'Discipline',
    'players fixture_order registrations issues revocations appearances')


def _seq(value):
    if type(value) is not int or not SEQ_MIN <= value <= SEQ_MAX:
        raise ValueError('seq must be an integer in 1..1000000000')
    return value


def _games(value):
    if type(value) is not int or not 1 <= value <= GAMES_MAX:
        raise ValueError('games must be an integer in 1..100000')
    return value


def validate_discipline(data, teams, fixtures, completed):
    """Validate the discipline payload against the source snapshot.

    ``teams`` is the list/set of known team ids, ``fixtures`` the source
    fixture id -> ``(home, away)`` mapping and ``completed`` the set of
    fixture ids carrying a formal result.  Returns a canonical
    :class:`Discipline` tuple whose collections are independent of input
    array ordering.
    """
    require(data, ('schema_version players fixture_order registrations '
                   'issues revocations appearances'))
    if data['schema_version'] != 1 or type(data['schema_version']) is not int:
        raise ValueError('schema_version must be the integer 1')

    # Known players: nonblank unique identifiers, bounded population.
    raw_players = rows(data['players'], PLAYERS_LIMIT)
    players = []
    player_set = set()
    for entry in raw_players:
        player_id = ident(entry)
        if player_id in player_set:
            raise ValueError('duplicate player id: ' + player_id)
        players.append(player_id)
        player_set.add(player_id)

    # Formal schedule ordering: every source fixture id exactly once; one
    # team plays at most one fixture on a shared sequence number.
    raw_order = rows(data['fixture_order'], RECORDS_LIMIT)
    fixture_order = {}
    team_seq = {}
    for entry in raw_order:
        require(entry, 'fixture seq')
        fixture_id = ident(entry['fixture'])
        if fixture_id not in fixtures:
            raise ValueError('fixture_order references unknown fixture: '
                             + fixture_id)
        if fixture_id in fixture_order:
            raise ValueError('fixture listed more than once in fixture_order: '
                             + fixture_id)
        seq = _seq(entry['seq'])
        fixture_order[fixture_id] = seq
        home, away = fixtures[fixture_id]
        for team in (home, away):
            key = (team, seq)
            if key in team_seq:
                raise ValueError(
                    'team %s plays more than one fixture at seq %d'
                    % (team, seq))
            team_seq[key] = fixture_id
    if set(fixture_order) != set(fixtures):
        missing = sorted(set(fixtures) - set(fixture_order))
        raise ValueError('fixture_order must list every fixture; missing: '
                         + ','.join(missing))

    # Registration changes: team id null (deregister) or a known team; one
    # change per player per sequence.
    raw_regs = rows(data['registrations'], RECORDS_LIMIT)
    registrations = []
    reg_keys = set()
    for entry in raw_regs:
        require(entry, 'player team seq')
        player_id = ident(entry['player'])
        if player_id not in player_set:
            raise ValueError('registration references unknown player: '
                             + player_id)
        team = entry['team']
        if team is not None:
            team = ident(team)
            if team not in teams:
                raise ValueError('registration references unknown team: '
                                 + team)
        seq = _seq(entry['seq'])
        key = (player_id, seq)
        if key in reg_keys:
            raise ValueError(
                'multiple registrations for player %s at seq %d'
                % (player_id, seq))
        reg_keys.add(key)
        registrations.append((player_id, team, seq))

    # Case issuances: globally unique case ids; balances may be imposed on a
    # known player even while that player is unregistered.
    raw_issues = rows(data['issues'], RECORDS_LIMIT)
    issues = []
    case_ids = set()
    for entry in raw_issues:
        require(entry, 'case_id player seq games')
        case_id = ident(entry['case_id'])
        if case_id in case_ids:
            raise ValueError('duplicate case id: ' + case_id)
        player_id = ident(entry['player'])
        if player_id not in player_set:
            raise ValueError('case references unknown player: ' + player_id)
        seq = _seq(entry['seq'])
        games = _games(entry['games'])
        case_ids.add(case_id)
        issues.append((case_id, player_id, seq, games))

    # Revocations: target an issued case, strictly later than issuance, at
    # most once per case.  Serving a case to zero does not preclude it.
    raw_revocations = rows(data['revocations'], RECORDS_LIMIT)
    issue_seq = {case_id: seq for case_id, _, seq, _ in issues}
    revocations = []
    revoked_cases = set()
    for entry in raw_revocations:
        require(entry, 'case_id seq')
        case_id = ident(entry['case_id'])
        if case_id not in issue_seq:
            raise ValueError('revocation references unknown case: ' + case_id)
        seq = _seq(entry['seq'])
        if seq <= issue_seq[case_id]:
            raise ValueError(
                'revocation seq must be strictly later than issuance: '
                + case_id)
        if case_id in revoked_cases:
            raise ValueError('case revoked more than once: ' + case_id)
        revoked_cases.add(case_id)
        revocations.append((case_id, seq))

    # Appearance snapshots: exactly one row per formally completed fixture,
    # none for pending fixtures; merged two-team ids without duplicates.
    raw_appearances = rows(data['appearances'], RECORDS_LIMIT)
    appearances = {}
    for entry in raw_appearances:
        require(entry, 'fixture players')
        fixture_id = ident(entry['fixture'])
        if fixture_id not in fixtures:
            raise ValueError('appearance row references unknown fixture: '
                             + fixture_id)
        if fixture_id not in completed:
            raise ValueError('appearance row for a fixture without a formal '
                             'result: ' + fixture_id)
        if fixture_id in appearances:
            raise ValueError('duplicate appearance row: ' + fixture_id)
        listed = rows(entry['players'], RECORDS_LIMIT)
        line_up = []
        seen = set()
        for value in listed:
            player_id = ident(value)
            if player_id not in player_set:
                raise ValueError('appearance references unknown player: '
                                 + player_id)
            if player_id in seen:
                raise ValueError(
                    'player appears twice in one fixture: ' + player_id)
            seen.add(player_id)
            line_up.append(player_id)
        appearances[fixture_id] = line_up
    if set(appearances) != set(completed):
        missing = sorted(set(completed) - set(appearances))
        raise ValueError('every completed fixture needs an appearance row; '
                         'missing: ' + ','.join(missing))

    return Discipline(players, fixture_order, registrations, issues,
                      revocations, appearances)
