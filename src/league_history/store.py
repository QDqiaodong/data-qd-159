"""SQLite persistence for the offline fixture-result history store.

Bitemporal row model
--------------------
``results_rows`` stores, per fixture, non-overlapping half-open business
intervals [left_time, right_time) (NULL right == +infinity) together with the
knowledge interval [sys_from, sys_to) during which the row was the recorded
fact (NULL sys_to == still current).  Replacing a business range in batch k
closes every intersected current row as-is at sys_to=k (its original business
interval is never rewritten, so older knowledge stays reproducible) and
inserts the replacement rows plus any surviving outside boundary pieces with
sys_from=k.  An as-of query at knowledge sequence k therefore sees exactly
the rows whose knowledge interval contains k, and can never observe a
half-applied batch: every batch commits in one IMMEDIATE transaction.
"""
import sqlite3
from pathlib import Path

from league_table.standings import calculate

from .timeline import INF, check_expected, normalize, replace_range

APPLICATION_ID = 0x4C484953  # 'LHIS'
USER_VERSION = 1
SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE fixtures (
    ord INTEGER PRIMARY KEY,
    fixture_id TEXT NOT NULL UNIQUE,
    home TEXT NOT NULL,
    away TEXT NOT NULL
);
CREATE TABLE deductions (
    ord INTEGER PRIMARY KEY,
    team TEXT NOT NULL UNIQUE,
    points INTEGER NOT NULL
);
CREATE TABLE results_rows (
    rowid INTEGER PRIMARY KEY,
    sys_from INTEGER NOT NULL,
    sys_to INTEGER,
    fixture_id TEXT NOT NULL,
    left_time INTEGER NOT NULL,
    right_time INTEGER,
    home_goals INTEGER,
    away_goals INTEGER,
    CHECK ((home_goals IS NULL) = (away_goals IS NULL)),
    CHECK (right_time IS NULL OR left_time < right_time)
);
CREATE INDEX idx_rows_fixture ON results_rows (fixture_id, rowid);
CREATE INDEX idx_rows_knowledge ON results_rows (sys_from, sys_to);
CREATE TABLE batches (
    batch_id TEXT PRIMARY KEY,
    knowledge_seq INTEGER NOT NULL UNIQUE,
    canonical_content TEXT NOT NULL
);
"""


def _connect(path, readonly=False):
    target = Path(path).resolve()
    uri = target.as_uri() + ("?mode=ro" if readonly else "")
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def check_compatible(path):
    """Open an existing store read-only and verify its identity markers."""
    if not Path(path).exists():
        raise ValueError('store does not exist: ' + str(path))
    conn = _connect(path, readonly=True)
    try:
        app_id = conn.execute("PRAGMA application_id").fetchone()[0]
        if app_id != APPLICATION_ID:
            raise ValueError('not a league-history compatible store')
        if conn.execute("PRAGMA user_version").fetchone()[0] != USER_VERSION:
            raise ValueError('unsupported store schema version')
        meta = _meta(conn)
        if meta.get('schema_version') != str(SCHEMA_VERSION):
            raise ValueError('unsupported store schema version')
        return meta
    finally:
        conn.close()


def _meta(conn):
    return dict(conn.execute("SELECT key, value FROM meta").fetchall())


def create_store(path, store_id, source_sha256, teams, fixtures, results, deductions):
    """Freeze the trusted source snapshot into a brand-new SQLite store.

    The database is built at a temporary sibling path and atomically renamed
    into place, so any failure leaves neither a half store nor anything at
    the target path.
    """
    target = Path(path).resolve()
    if target.exists() or target.is_symlink():
        raise ValueError('store already exists: ' + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    handle, tmp_name = tempfile.mkstemp(prefix=target.name + '.', suffix='.tmp',
                                        dir=target.parent)
    Path(tmp_name).unlink()
    conn = sqlite3.connect(tmp_name, isolation_level=None)
    try:
        conn.execute("PRAGMA application_id=%d" % APPLICATION_ID)
        conn.execute("PRAGMA user_version=%d" % USER_VERSION)
        # executescript commits first; the file is still a private temp file,
        # then the data freeze itself runs inside one explicit transaction.
        conn.executescript(_DDL)
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", [
            ('schema_version', str(SCHEMA_VERSION)),
            ('store_id', store_id),
            ('source_sha256', source_sha256),
            ('knowledge_seq', '0'),
            ('op_count', '0'),
        ])
        conn.executemany("INSERT INTO fixtures(ord, fixture_id, home, away) VALUES (?, ?, ?, ?)",
                         [(i, fid, home, away)
                          for i, (fid, (home, away)) in enumerate(fixtures.items())])
        conn.executemany("INSERT INTO deductions(ord, team, points) VALUES (?, ?, ?)",
                         [(i, team, points) for i, (team, points) in enumerate(deductions.items())])
        initial = []
        for fid, (home, away) in fixtures.items():
            score = results.get(fid)
            home_goals = None if score is None else score[0]
            away_goals = None if score is None else score[1]
            initial.append((0, fid, 0, None, home_goals, away_goals))
        conn.executemany(
            "INSERT INTO results_rows(sys_from, fixture_id, left_time, right_time, "
            "home_goals, away_goals) VALUES (?, ?, ?, ?, ?, ?)", initial)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        conn.close()
        Path(tmp_name).unlink(missing_ok=True)
        raise
    conn.close()
    import os
    os.replace(tmp_name, target)


class _WorkState:
    """Private per-fixture working state for one open batch.

    Shadow rows stay disjoint and cover the whole business line.  Rows
    carrying a rowid are untouched persisted rows; rows with rowid None are
    batch-owned material inserted at commit (both replacement ranges and the
    outside pieces cut out of intersected persisted rows).  ``touched`` holds
    every persisted row intersected by the batch: each is closed as-is at
    this batch, so its original business interval stays queryable at every
    older knowledge sequence and historical rows are never reshaped in place.
    """

    def __init__(self, fixture_id, rows):
        self.fixture_id = fixture_id
        self.rows = sorted(rows, key=lambda row: row['left'])
        self.timeline = normalize([(row['left'], row['right'], row['value'])
                                   for row in self.rows])
        self.touched = set()

    def apply(self, left, right, expected, replacement):
        if not check_expected(self.timeline, left, right, expected):
            raise ValueError('expected value does not match the current interval state')
        self.timeline = replace_range(self.timeline, left, right, replacement)
        self.rows = _cut_rows(self.rows, left, right, self.touched)
        self.rows.append({'rowid': None, 'left': left, 'right': right, 'value': replacement})
        self.rows.sort(key=lambda row: row['left'])


def _cut_rows(rows, left, right, touched):
    """Cut shadow rows at [left, right); only outside pieces survive.

    Batch-owned rows (rowid None) simply drop their covered material, keeping
    the outside pieces batch-owned.  An intersected persisted row is always
    closed as-is: it is removed from the current shadow and every surviving
    outside piece is re-created as a batch-owned row carrying the same value.
    The covered interior is dropped from the current shadow while the closed
    row remains queryable at older knowledge sequences.
    """
    kept = []
    for row in rows:
        r_left, r_right, value = row['left'], row['right'], row['value']
        if r_right <= left or r_left >= right:
            kept.append(row)
            continue
        if row['rowid'] is None:
            if r_left < left:
                kept.append({'rowid': None, 'left': r_left, 'right': left, 'value': value})
            if r_right > right:
                kept.append({'rowid': None, 'left': right, 'right': r_right, 'value': value})
            continue
        touched.add(row['rowid'])
        if r_left < left:
            kept.append({'rowid': None, 'left': r_left, 'right': left, 'value': value})
        if r_right > right:
            kept.append({'rowid': None, 'left': right, 'right': r_right, 'value': value})
    kept.sort(key=lambda row: row['left'])
    return kept


def _load_current(conn):
    states = {}
    sql = ("SELECT rowid, fixture_id, left_time, right_time, home_goals, away_goals "
           "FROM results_rows WHERE sys_to IS NULL ORDER BY fixture_id, rowid")
    for rowid, fixture_id, left, right, home, away in conn.execute(sql):
        value = None if home is None else (home, away)
        states.setdefault(fixture_id, []).append(
            {'rowid': rowid, 'left': left, 'right': INF if right is None else right,
             'value': value})
    return {fixture_id: _WorkState(fixture_id, rows) for fixture_id, rows in states.items()}


def load_frozen(path):
    """Public read-only access to the frozen teams/fixtures/deductions."""
    check_compatible(path)
    conn = _connect(path, readonly=True)
    try:
        return _load_frozen(conn)
    finally:
        conn.close()


def apply_batch(path, batch_id, canonical, operations, operation_limit=20000):
    """Validate against and mutate the store in one IMMEDIATE transaction.

    Returns (knowledge_seq, replayed).  An exact replay of a known batch_id
    short-circuits before any expected-value check and writes nothing.
    """
    if not Path(path).exists():
        raise ValueError('store does not exist: ' + str(path))
    conn = _connect(path)
    txn = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        txn = True
        known = conn.execute(
            "SELECT knowledge_seq, canonical_content FROM batches WHERE batch_id=?",
            (batch_id,)).fetchone()
        if known is not None:
            seq, stored = known
            if stored != canonical:
                raise ValueError('batch_id reused with different canonical content')
            conn.execute("COMMIT")
            return seq, True
        meta = _meta(conn)
        seq = int(meta['knowledge_seq'])
        op_count = int(meta['op_count'])
        if op_count + len(operations) > operation_limit:
            raise ValueError('store operation limit of %d exceeded' % operation_limit)
        states = _load_current(conn)
        for op in operations:
            state = states.get(op['fixture'])
            if state is None:
                raise ValueError('unknown fixture: ' + op['fixture'])
            state.apply(op['left'], op['right'], op['expected'], op['replacement'])
        new_seq = seq + 1
        for state in states.values():
            for rowid in state.touched:
                # close the historical row without altering its business span
                conn.execute(
                    "UPDATE results_rows SET sys_to=? WHERE rowid=? AND sys_to IS NULL",
                    (new_seq, rowid))
            for row in state.rows:
                if row['rowid'] is not None:
                    continue
                right = None if row['right'] == INF else row['right']
                if row['value'] is None:
                    home = away = None
                else:
                    home, away = row['value']
                conn.execute(
                    "INSERT INTO results_rows(sys_from, fixture_id, left_time, right_time, "
                    "home_goals, away_goals) VALUES (?, ?, ?, ?, ?, ?)",
                    (new_seq, state.fixture_id, row['left'], right, home, away))
        conn.execute(
            "INSERT INTO batches(batch_id, knowledge_seq, canonical_content) VALUES (?, ?, ?)",
            (batch_id, new_seq, canonical))
        conn.execute("UPDATE meta SET value=? WHERE key='knowledge_seq'", (str(new_seq),))
        conn.execute("UPDATE meta SET value=? WHERE key='op_count'",
                     (str(op_count + len(operations)),))
        conn.execute("COMMIT")
        txn = False
        return new_seq, False
    finally:
        if txn and conn.in_transaction:
            conn.execute("ROLLBACK")
        conn.close()


def _load_frozen(conn):
    teams = []
    fixtures = {}
    for ord_, fixture_id, home, away in conn.execute(
            "SELECT ord, fixture_id, home, away FROM fixtures ORDER BY ord"):
        fixtures[fixture_id] = (home, away)
        for team in (home, away):
            if team not in teams:
                teams.append(team)
    deductions = dict(conn.execute(
        "SELECT team, points FROM deductions ORDER BY ord"))
    return teams, fixtures, deductions


def query(path, as_of, left, right):
    """Read-only timeline reconstruction as known at knowledge sequence as_of."""
    meta = check_compatible(path)
    current_seq = int(meta['knowledge_seq'])
    if type(as_of) is not int or not 0 <= as_of <= current_seq:
        raise ValueError('as-of sequence must be an integer in 0..%d' % current_seq)
    conn = _connect(path, readonly=True)
    try:
        teams, fixtures, deductions = _load_frozen(conn)
        timelines = {}
        sql = ("SELECT left_time, right_time, home_goals, away_goals FROM results_rows "
               "WHERE fixture_id=? AND sys_from<=? AND (sys_to IS NULL OR sys_to>?) "
               "ORDER BY rowid")
        for fixture_id in fixtures:
            segments = []
            for seg_left, seg_right, home, away in conn.execute(
                    sql, (fixture_id, as_of, as_of)):
                value = None if home is None else (home, away)
                segments.append((seg_left, INF if seg_right is None else seg_right, value))
            timelines[fixture_id] = normalize(sorted(segments, key=lambda seg: seg[0]))
    finally:
        conn.close()

    clipped = {}
    points = {left}
    for fixture_id, timeline in timelines.items():
        pieces = [(max(seg_left, left), min(seg_right, right), value)
                  for seg_left, seg_right, value in timeline
                  if seg_right > left and seg_left < right]
        clipped[fixture_id] = pieces
        for seg_left, _, _ in pieces:
            if left < seg_left < right:
                points.add(seg_left)

    nodes = sorted(points)
    raw = []
    for index, node in enumerate(nodes):
        segment_right = right if index + 1 == len(nodes) else nodes[index + 1]
        results = {}
        for fixture_id, pieces in clipped.items():
            for seg_left, seg_right, value in pieces:
                if seg_left <= node < seg_right:
                    if value is not None:
                        results[fixture_id] = value
                    break
        raw.append((node, segment_right, calculate(teams, fixtures, results, deductions)))

    # Merge neighboring segments iff the complete standings result is equal;
    # source operation boundaries inside an equal-result region never split.
    timeline_out = []
    for node, segment_right, computed in raw:
        if timeline_out and timeline_out[-1]['result'] == computed \
                and timeline_out[-1]['right'] == node:
            timeline_out[-1]['right'] = None if segment_right == INF else segment_right
        else:
            timeline_out.append({'left': node,
                                 'right': None if segment_right == INF else segment_right,
                                 'result': computed})
    return timeline_out
