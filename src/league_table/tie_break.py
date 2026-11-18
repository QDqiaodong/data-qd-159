"""Auditable head-to-head tie-break analysis (optional policy mode).

The fixed baseline accounting in :mod:`league_table.standings` remains the
authority for points (including the original deduction rule), goals and the
default ranking.  This module never rewrites fixtures, results or deductions;
it only re-orders teams that finish level on total points and emits an exact
audit trail of every tie-break step.

A policy profile explicitly declares two ordered, non-empty, duplicate-free
criterion lists:

* ``head_to_head`` — criteria evaluated on the mini-table built from the
  *completed* fixtures whose two sides both belong to the current group.
  Pending fixtures are never counted and deductions are never applied again.
* ``overall`` — global cumulative values from the fixed baseline, used only
  after every head-to-head criterion has failed to split a group.

After each head-to-head split, every still-tied subgroup restarts from the
first head-to-head criterion on its own restricted fixture set, so a
sub-group mini-table can differ from the parent group's restricted rows.
Team ids are never an athletic criterion; they only determine display order
inside blocks that remain genuinely tied, which keep a shared competition
rank.
"""
from .common import read, require

SUPPORTED_FIELDS = ('points', 'goal_difference', 'goals_for')


def parse_profile(data):
    """Validate the strict profile object and return ``(head_to_head, overall)``."""
    require(data, 'head_to_head overall')
    parsed = {}
    for key in ('head_to_head', 'overall'):
        value = data[key]
        if not isinstance(value, list) or not value:
            raise ValueError('profile ' + key + ' must be a nonempty array')
        criteria = []
        for field in value:
            if not isinstance(field, str) or field not in SUPPORTED_FIELDS:
                raise ValueError('unsupported profile criterion: ' + repr(field))
            if field in criteria:
                raise ValueError('duplicate profile criterion: ' + field)
            criteria.append(field)
        parsed[key] = criteria
    return parsed['head_to_head'], parsed['overall']


def load_profile(path):
    """Read a profile file with the same strict JSON protections as sources."""
    data, _ = read(path)
    return parse_profile(data)


def _mini_table(group, fixtures, results):
    """Build head-to-head statistics for ``group`` from completed in-group fixtures.

    Returns ``(stats, fixture_ids)`` where fixture ids are sorted ascending and
    stats only include fixtures whose two sides are both in the group and that
    carry a formal result.  Deductions are never re-applied.
    """
    members = set(group)
    stats = {team: {'points': 0, 'goal_difference': 0, 'goals_for': 0,
                    'goals_against': 0, 'played': 0} for team in group}
    used = []
    for fid in sorted(fixtures):
        if fid not in results:
            continue
        home, away = fixtures[fid]
        if home not in members or away not in members:
            continue
        home_goals, away_goals = results[fid]
        used.append(fid)
        for team, goals_for, goals_against in (
                (home, home_goals, away_goals), (away, away_goals, home_goals)):
            row = stats[team]
            row['played'] += 1
            row['goals_for'] += goals_for
            row['goals_against'] += goals_against
            row['goal_difference'] += goals_for - goals_against
            if goals_for > goals_against:
                row['points'] += 3
            elif goals_for == goals_against:
                row['points'] += 1
    return stats, used


def _split_by(criteria_values):
    """Partition teams (ascending id input) by descending criterion value."""
    parts = []
    current = []
    current_value = None
    for team, value in criteria_values:
        if not current or value == current_value:
            current.append(team)
        else:
            parts.append(current)
            current = [team]
        current_value = value
    if current:
        parts.append(current)
    return parts


def _criterion_values(team_ids, source, criteria, source_name, stats=None):
    """Emit profile-ordered criterion values for one audit node.

    Head-to-head values come from the node's own mini-table (``stats``);
    overall values come from the fixed global baseline rows (``source``).
    """
    entries = []
    for field in criteria:
        values = {}
        for team in team_ids:
            if source_name == 'head_to_head':
                values[team] = stats[team][field]
            else:
                values[team] = source[team][field]
        entries.append({'criterion': field, 'source': source_name,
                        'values': {team: values[team] for team in team_ids}})
    return entries


def apply_tie_breaks(teams, fixtures, results, deductions, baseline, profile):
    """Return a policy-mode result with re-ordered standings and a full audit."""
    head_to_head, overall = profile
    rows = {row['team']: row for row in baseline['standings']}

    # Initial groups: teams level on total points (deductions already included),
    # emitted in descending points order; teams inside a group stay id-sorted.
    by_points = {}
    for team in sorted(teams):
        by_points.setdefault(rows[team]['points'], []).append(team)
    initial_groups = [by_points[points] for points in sorted(by_points, reverse=True)]

    audit_nodes = []
    ordered_blocks = []

    def resolve(group):
        """Split one level-on-points group; append ordered display blocks."""
        team_ids = sorted(group)
        stats, fixture_ids = _mini_table(team_ids, fixtures, results)
        node = {
            'team_ids': team_ids,
            'fixture_ids': fixture_ids,
            'criterion_values': _criterion_values(
                team_ids, rows, head_to_head, 'head_to_head', stats)
            + _criterion_values(team_ids, rows, overall, 'overall'),
            'subgroups': None,
        }
        audit_nodes.append(node)

        # Head-to-head pass: the first criterion that splits the current group
        # wins this node; every still-tied subgroup restarts the whole profile.
        for field in head_to_head:
            parts = _split_by(sorted(
                ((team, stats[team][field]) for team in team_ids),
                key=lambda item: (-item[1], item[0])))
            if len(parts) > 1:
                node['subgroups'] = parts
                for part in parts:
                    if len(part) == 1:
                        ordered_blocks.append(part)
                    else:
                        resolve(part)
                return

        # Every head-to-head criterion failed to split the group: fall back to
        # the global cumulative values, in declared overall order.  Overall
        # values are global so each criterion is applied once; blocks that stay
        # tied share a competition rank and are displayed by ascending team id.
        parts = [team_ids]
        for field in overall:
            refined = []
            for part in parts:
                ranked = sorted(((team, rows[team][field]) for team in part),
                                key=lambda item: (-item[1], item[0]))
                refined.extend(_split_by(ranked))
            parts = refined
        node['subgroups'] = parts
        ordered_blocks.extend(parts)

    for group in initial_groups:
        if len(group) == 1:
            ordered_blocks.append(group)
        else:
            resolve(group)

    # Assign competition ranks with gaps (1,1,3 style): every block occupies
    # one rank position, a block's rank is the position of its first team.
    standings = []
    position = 1
    for block in ordered_blocks:
        rank = position
        for team in block:
            row = dict(rows[team])
            row['rank'] = rank
            standings.append(row)
        position += len(block)

    audit = {'profile': {'head_to_head': head_to_head, 'overall': overall},
             'nodes': audit_nodes}
    result = {'standings': standings,
              'pending_fixtures': list(baseline['pending_fixtures']),
              'completed': baseline['completed']}
    return result, audit
