# League standings v1
赛事数据整理者输入唯一teams(1..200)、fixtures(<=20000，id/home/away)、results(fixture/home_goals/away_goals)和deductions(team/points/reason)。赛程双方必须为不同已知球队，赛果只允许一个已知fixture一次；进球为0..1000整数不接受bool。赛程允许同双方不同ID的多轮交锋，未提供赛果的比赛单列pending，不记0:0。
所有输入赛果均表示正式完赛，固定胜3平1负0；双方played与进失球配对守恒。每队最多一个已生效扣分(0..1000，原因非空)，可使总积分为负。输入为调用方完整可信快照，无日期推断、暂存赛果或取消状态。
依积分、净胜球、进球数降序；三者均同则共享competition rank（1,1,3），按队ID排展示顺序，不伪造胜负关系。无head-to-head或法定联赛规则声明。
样例C4分第一、A扣1后2分第二、B1分第三、D0分第四；m4待赛。支持空赛果、不参与球队保持0场。无博彩、真实赛程采集或判罚建议，合成数据。

# Observation reconciliation (schema v2, optional --observations)
`--observations` is optional. Without it every invocation keeps schema_version=1, the
same envelope `{schema_version, source_sha256, result}` and the same result. Passing it
produces schema_version=2 with the extra keys below; standings, ranking and deduction
rules are untouched.

## Observations file

Strict JSON, same 8 MB / duplicate-key / unknown-field / non-finite protections as the
source file. Exactly one top-level key `observations`, an array of 0..2000 records; the
total number of candidate entries must not exceed 20000.

```json
{"observations":[
  {"id":"o1","side_a":"A","side_b":"B","a_goals":2,"b_goals":1,
   "candidates":["m1","m2"]}
]}
```

* `id` — nonblank identifier, <=128 chars, unique within the file. Records are never
  merged even when two teams and the score are identical.
* `side_a`, `side_b` — distinct known team ids. `a_goals`/`b_goals` are the goals of
  `side_a`/`side_b` respectively, integers 0..1000, bool rejected.
* `candidates` — array (possibly empty) of distinct fixture ids. Every candidate must be
  a known fixture whose two sides are exactly `{side_a, side_b}`, and must not already
  have a formal entry in `results`. Fixtures with an existing result are locked: they
  may not be overwritten, matched, or silently ignored as candidates; listing one is an
  input error. The candidate list is the complete caller-provided possibility range; no
  edges are invented, including for fixtures with the same two teams.

## Reconciliation semantics

The candidate graph is bipartite: observations on the left, fixtures on the right,
edges exactly as listed. A *legal complete assignment* picks one candidate fixture for
each observation so that each fixture is used by at most one observation.

* A complete assignment exists → `status="feasible"`. For each observation,
  `possible_fixtures` is exactly the set of fixtures it can take **across all** legal
  complete assignments, in ascending id order; `resolved_fixture` is that sole fixture
  when the set has size one, otherwise `null`. Only `resolved_fixture` entries are
  imported: the observation's `a_goals`/`b_goals` are converted to the fixture's
  home/away orientation by team identity, merged into the original `results`, and
  `result` reflects that merge. Ambiguous observations stay pending. `hall_witness` is
  `null`.
* No complete assignment exists → `status="infeasible"`. This is a valid business
  diagnosis: exit code 0, no observation is imported, `observations` is `[]`, and
  `result` equals the baseline accounting of the source snapshot run through the
  original analyze (it is never read from an old report). `hall_witness` is a
  certificate `{"observation_ids":[...],"fixture_ids":[...]}`, sorted ascending, where
  the observation ids are a non-empty set S and the fixture ids are exactly the full
  neighbour set N(S) of S in the original candidate graph, with |N(S)| < |S|.

Computing `possible_fixtures` from an arbitrary single matching, a local candidate
count, or equal scores is not allowed; both the per-observation possibilities and the
infeasibility certificate come from the global structure (see below). Empty
observations are feasible with `observations=[]` and `hall_witness=null`.

All lists inside `reconciliation` are sorted ascending by identifier, so any input
permutation yields the same business result and the same certificate. `result` keeps the
original standings, ranking and pending fixture order.

## Report envelope (schema v2)

```json
{"schema_version":2,
 "source_sha256":"<sha256 of source bytes>",
 "observations_sha256":"<sha256 of observations file bytes>",
 "result":{"standings":[...],"pending_fixtures":[...],"completed":0},
 "reconciliation":{"status":"feasible","observations":[
   {"id":"o1","possible_fixtures":["m1"],"resolved_fixture":"m1"}],
   "hall_witness":null}}
```

Each file keeps its own byte digest. Output must differ from every input (source and
observations), symlink outputs are rejected, and publication is atomic; any parse,
validation or computation failure exits 2 and leaves the previous report intact.

# Head-to-head tie-break policy (schema v2, optional --tie-break-profile)
`--tie-break-profile` is optional and mutually exclusive with `--observations`.
Without either flag every invocation keeps schema_version=1, the same envelope
`{schema_version, source_sha256, result}` and the same ranking behavior. Passing a
profile produces schema_version=2 with an added `tie_break_audit`; the trusted
fixtures, results, deductions and the fixed baseline accounting (`calculate`) are
never rewritten, and source data bytes are not modified.

## Profile file

Strict JSON with the same 8 MB / duplicate-key / unknown-field / non-finite
protections as every other JSON input. Exactly two top-level keys, each an
ordered, non-empty array with no duplicates; only the supported fields
`points`, `goal_difference` and `goals_for` are allowed, and no competition
format is ever assumed implicitly:

```json
{"head_to_head":["points","goal_difference","goals_for"],
 "overall":["goal_difference","goals_for"]}
```

* `head_to_head` — criteria evaluated on the mini-table of one tied group.
* `overall` — global cumulative values from the fixed baseline, used only as
  the terminal fallback after every head-to-head criterion has failed.

## Tie-break semantics

Total points, including the original deduction rule, stay the first ordering
criterion; the profile only applies to groups of teams that finish level on
total points. Deducted totals select the initial tied groups, and initial
groups are processed in descending total-points order.

For each tied group, the head-to-head mini-table uses only fixtures that (a)
already carry a formal result and (b) have both sides inside the current
group. Pending fixtures are never counted, and deductions are never applied a
second time inside a mini-table. Multiple completed legs between the same two
teams all count. Criteria are applied in declared profile order, descending:

1. The first head-to-head criterion that splits the current group partitions
   it; a criterion that leaves the group intact is skipped for the next one.
2. Every still-tied subgroup restarts the whole procedure **from the first
   head-to-head criterion**, recomputed on that subgroup's own restricted
   fixture set. Recomputation can change the ordering relative to the parent
   group's restricted rows, so parent values must never be reused.
3. Only when no head-to-head criterion can split a group do the `overall`
   fields apply in their declared order, using the global cumulative values.
   Overall values are global, so each overall criterion is applied once; a
   block that stays tied keeps a shared competition rank (1,1,3 gap rule).
4. Team ids are never an athletic criterion. They only fix display order
   inside a genuinely tied block, which is emitted with ascending team ids.

## Report envelope (schema v2, policy mode)

```json
{"schema_version":2,
 "source_sha256":"<sha256 of source bytes>",
 "result":{"standings":[...],"pending_fixtures":[...],"completed":0},
 "tie_break_audit":{
   "profile":{"head_to_head":["points","goal_difference","goals_for"],
              "overall":["goal_difference","goals_for"]},
   "nodes":[
     {"team_ids":["A","B","C","D"],
      "fixture_ids":["g1","g2","g3","g4","g5","g6"],
      "criterion_values":[
        {"criterion":"points","source":"head_to_head",
         "values":{"A":9,"B":3,"C":3,"D":3}},
        {"criterion":"goal_difference","source":"head_to_head",
         "values":{"A":6,"B":-2,"C":-2,"D":-2}},
        {"criterion":"goals_for","source":"head_to_head",
         "values":{"A":6,"B":2,"C":1,"D":1}},
        {"criterion":"goal_difference","source":"overall",
         "values":{"A":6,"B":1,"C":1,"D":1}},
        {"criterion":"goals_for","source":"overall",
         "values":{"A":6,"B":5,"C":4,"D":4}}],
      "subgroups":[["A"],["B","C","D"]]}]}}
```

* `nodes` follow the initial tied-group order and then recurse depth-first;
  every recursive subgroup that still contains at least two teams gets its
  own node. When there is no tied group, `nodes` is `[]`.
* Each node carries `team_ids` ascending, the actually-used in-group
  `fixture_ids` ascending (pending fixtures excluded), `criterion_values` in
  exact profile order with `head_to_head`/`overall` sources distinguished,
  and `subgroups` in the final ranking order produced at that node.
* Every numeric value is computed from the data set the node actually used:
  head-to-head values from its own in-group completed fixtures, overall
  values from the global baseline.
* `result.standings` keeps the baseline rows' scalars; rows are re-ordered by
  the resolved blocks and receive policy ranks, where each block occupies one
  rank position and a block's rank is the position of its first team.
* Input/output path rules, atomic publication and exit-2 preservation of an
  existing report are identical to the other modes.

## Why the results are exact (no assignment enumeration)

A maximum matching decides feasibility in O(E·sqrt(V)) time. It never enumerates
assignments. For a feasible instance, an edge (observation o, fixture f) occurs in some
maximum matching (and therefore in some legal complete assignment) iff one of the
following holds:

1. it is in the chosen maximum matching, or
2. o and f lie in the same strongly connected component of the matching digraph, or
3. f can reach an unmatched fixture in the matching digraph (an alternating path
   between matched fixtures).

This is the standard allowed-edge characterisation for maximum matchings. Because every
observation is matched in a complete assignment, the union over complete assignments of
fixtures available to o is exactly the set of allowed edges at o. Each
`possible_fixtures` set is therefore exact, not a sample.

For an infeasible instance, let S be the observations reachable from unmatched
observations via alternating paths. By König's theorem |N(S)| < |S|, so S and its full
neighbour set N(S) form a directly checkable Hall violation; the algorithm reports
exactly that. A feasible instance is conversely certified by the complete matching whose
existence the maximum matching establishes. Both directions are verified against brute
force enumeration on all small bipartite graphs in the test suite.

# League history store (league-history, independent report envelope)

`league-history` is a standalone offline SQLite store for corrections and
withdrawals of results that already carry an explicit fixture identity.  It
does **not** consume observation files and never changes the standings,
points, deduction or competition-rank rules: every timeline segment reuses
the original `league_table.standings.calculate`.  Its report envelope
(`schema_version=1`, `store_id`, `source_sha256`, `knowledge_seq`) is
independent of the league-table v1/v2 envelopes.

## Business time and knowledge time

Two time axes are kept separate.

* **Business time** is an integer in `0..1000000000`; booleans are rejected.
  Every interval is half-open `[left, right)`. A null `right` means
  `+infinity`.  Results from the init source hold from business time 0; a
  fixture without an init result is **pending** (never 0:0).
* **Knowledge time** is a strictly increasing sequence `1,2,...` assigned to
  each successfully committed batch; sequence 0 is the frozen init snapshot.
  `query --as-of K` sees only batches whose sequence is `<= K`, so later
  corrections can never pollute an older knowledge view.

## CLI

```
league-history init  --source FILE --db DB --output REPORT
league-history apply   --db DB --batch FILE --output REPORT
league-history query   --db DB --as-of K --from L --to R --output REPORT
```

* `init` parses the same strict source contract as league-table, then freezes
  teams, fixtures, deductions and the initial results into a brand-new SQLite
  database and generates a stable `store_id`. It never overwrites an existing
  database and a failure leaves no half store at the target path.
* `apply` validates and commits one batch; `query` opens the database
  read-only, performs no dedup/sequence change and cannot write it.  Neither
  `apply` nor `query` creates a missing database; only a compatible store
  (matching application id, schema version and meta) is accepted.
* `R` is an integer or the string `inf` (infinite right bound); `null` is not
  accepted.  `L` and a finite `R` lie in `0..1000000000` with `L < R`; an
  empty window is an error.  `K` is an integer in `0..current sequence`; a
  sequence above the current one errors.  Times, sequences and goals all
  reject booleans.
* All three commands exit 0 on success and 2 on any parse, validation or
  storage error, preserving the database and any previous report.  The DB,
  report and every JSON input path must be distinct even after resolving
  symlinks/aliases; a query report can never overwrite the database.  Report
  publication reuses the original atomic `save` (temp file + `os.replace`,
  symlink outputs rejected).  The database and report are not cross-file
  atomic: if a report write fails after commit, replaying the same batch
  returns its original sequence without new writes.

## Batch file format

Strict JSON with the same 8 MB, duplicate-key, unknown-field, non-finite
number and identifier rules as every other JSON input:

```json
{"batch_id":"b1","operations":[
  {"fixture":"m","left":10,"right":30,"expected":[2,0],"replacement":[0,1]},
  {"fixture":"m","left":20,"right":25,"expected":[0,1],"replacement":null}
]}
```

* `batch_id` is a nonblank identifier (<=128 chars).  A batch has 1..1000
  operations; the store holds at most 20000 operations in total.
* Each operation names a frozen `fixture`, a finite `left`, a `right` that is
  an integer strictly greater than `left` or null, an `expected` value and a
  `replacement` value.  A value is either null or a two-element integer array
  `[home_goals, away_goals]` with each goal in `0..1000`.
* `expected` must equal the value present over the **whole** `[left,right)`
  range in the batch-private working state, including every internal
  sub-segment and any pending hole; checking only endpoints, single instants
  or the latest record is forbidden.  `expected=null` requires the entire
  range to be pending.
* On success only `[left,right)` becomes `replacement`; material outside the
  range and earlier knowledge history are untouched.  `replacement=null`
  withdraws the range back to pending, it does not record 0:0.
* Operations run in file order on the batch-private working state: a later
  operation sees intervals produced by earlier ones in the same batch.  If any
  precondition fails, the whole batch fails and no fact, sequence or dedup
  registration changes (single transaction).

## Idempotent replay and concurrent commits

A batch's canonical business content is the fully parsed `batch_id` and
`operations` values in their array order, ignoring object key order and JSON
whitespace; the file path and byte digest are not part of it.  Replaying a
known `batch_id` with identical canonical content returns its first sequence
and writes nothing — the replay check happens before any `expected`
verification, so it still succeeds after the current state has drifted.
Reusing a known `batch_id` with different content errors; a failed batch id is
not registered and remains available for corrected first-time content.

All interval changes, history rows, batch identity and sequence commit in one
IMMEDIATE SQLite transaction with a strictly increasing sequence.  Two
processes committing conflicting ranges serialize on the write lock: at most
one can satisfy its `expected` precondition against the same old value; the
other re-checks against the post-commit current state and fails, leaving no
partial batch or duplicate sequence.

## Query timeline

`query --as-of K --from L --to R` returns the maximal contiguous timeline
inside `[L,R)` as known at sequence K.  Each segment is exactly
`{"left":...,"right":...,"result":...}` where an infinite right bound is
emitted as null and `result` is the full league-table standings object
(including points, deductions, pending fixtures and competition rank).
Neighboring segments are merged if and only if the complete `result` is equal;
source operation boundaries inside an equal-result region never force a split.
K=0 reproduces the init snapshot regardless of later batches.

## Persistence model

`results_rows` is bitemporal: each row carries a business interval
(`left_time`,`right_time`) and a knowledge interval (`sys_from`,`sys_to`).
Replacing a range in batch k closes each intersected current row as-is
(`sys_to=k`, its business span is never mutated so historical queries stay
exact) and inserts the replacement rows and surviving outside pieces with
`sys_from=k`.  An as-of query selects rows with `sys_from<=K` and
(`sys_to` null or `sys_to>K`).  The frozen schedule lives in `fixtures` and
`deductions`; committed batches and their canonical content live in
`batches`; `meta` holds `schema_version`, `store_id`, `source_sha256`,
`knowledge_seq` and the running operation count.  Read-only connections never
observe a half-applied batch.
