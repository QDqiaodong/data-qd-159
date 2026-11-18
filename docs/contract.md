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

# Venue capacity audit (league-venue-audit, independent report envelope)

`league-venue-audit --source FILE --schedule FILE --output REPORT` audits
bookings of already-scheduled, not-yet-played fixtures against venue
capacity.  It is a standalone offline command: it never changes the source
snapshot and its report envelope (`schema_version=1`, `source_sha256`,
`schedule_sha256`, ...) is independent of the league-table and history
envelopes.  Overload is a business finding and exits 0; only malformed input
exits 2.

## Inputs

The `--source` file is the original trusted league snapshot under the same
strict contract as league-table (unique teams/fixtures, formal `results`,
deductions; duplicate keys, unknown fields and non-finite numbers rejected).
The independent `--schedule` file is strict JSON with the same 8 MB,
duplicate-key and identifier rules and exactly three top-level arrays, each
holding at most 20 000 records and possibly empty:

* `venues` — `{"id","capacity"}`; venue ids are unique and `capacity` is the
  default capacity, an integer 0..1000 (booleans rejected).
* `limits` — `{"venue","start","end","capacity"}` referencing a known venue.
  Restrictions may overlap or be identical and are never order-overriding;
  the effective capacity on any minute is the minimum of the default and
  every active limit.  Capacity 0 closes the venue.  Minutes are plain
  integers in 0..1000000000 with `start < end`.
* `bookings` — `{"fixture","venue","start","end","units"}` referencing a
  known venue and a fixture that exists in the source snapshot and has **no
  formal result**; each fixture carries at most one booking.  `units` is an
  integer 1..1000 (booleans rejected).  A pending fixture without a booking
  remains legal input and is reported as unscheduled.

All intervals are half-open `[start, end)`: a limit or booking starting or
ending at minute `t` first applies at minute `t` and no longer applies at
minute `t`.  Overlapping bookings and booking demand above effective
capacity are audited business facts, not validation errors.

## Report

```json
{"schema_version": 1,
 "source_sha256": "<sha256 of the source file bytes>",
 "schedule_sha256": "<sha256 of the schedule file bytes>",
 "excess_unit_minutes": 10,
 "overloads": [
   {"venue": "v1", "start": 2, "end": 3, "capacity": 3, "load": 4}],
 "booking_relief": [
   {"fixture": "m2", "relief_unit_minutes": 9}],
 "unscheduled_fixtures": ["m4"]}
```

* `excess_unit_minutes` — plan-wide sum, over every minute, of the positive
  difference between total active load and effective capacity; zero when no
  minute is overloaded.
* `overloads` — the maximal contiguous periods, per venue, on which both
  capacity and load are unchanged and the load exceeds capacity, as
  `{venue,start,end,capacity,load}`.  A boundary where only the limit source
  or booking membership changes while capacity and load stay equal never
  splits a period.  Sorted by venue id and then start minute.
* `booking_relief` — one row per booking, including zero relief, sorted by
  fixture id.  `relief_unit_minutes` is the amount by which the plan-wide
  excess unit-minutes fall when **that single booking** is removed; capacity
  and every other booking are untouched.  Every value is measured against the
  same original plan, so rows cannot be added or read as the effect of a
  joint cancellation.
* `unscheduled_fixtures` — fixture ids with neither a booking nor a formal
  result, ascending.

Each input file keeps its own byte-faithful digest.  The output must differ
from both inputs after path resolution, symlink outputs are rejected, and
publication reuses the atomic temp-file + `os.replace` publisher: any parse,
validation or computation failure exits 2 and leaves a previous report
exactly in place.

## Worked example

Default capacity 3; limits `[3,7)` capacity 2 and `[5,9)` capacity 1; two
bookings `[0,8)` and `[2,6)`, each 2 units.  The effective profile is

| minutes | capacity | load | excess per minute |
| --- | --- | --- | --- |
| [0,2) | 3 | 2 | 0 |
| [2,3) | 3 | 4 | 1 |
| [3,5) | 2 | 4 | 2 |
| [5,6) | 1 | 4 | 3 |
| [6,8) | 1 | 2 | 1 |
| [8,9) | 1 | 0 | 0 |

so the total is 1 + 2·2 + 3 + 2·1 = 10 excess unit-minutes and the four
overload periods are `[2,3) (3,4)`, `[3,5) (2,4)`, `[5,6) (1,4)` and
`[6,8) (1,2)`.  Cancelling the long booking removes 1 + 4 + 3 + 1 = 9;
cancelling the short one removes 1 + 4 + 2 = 7.

## Why the results are exact, and the complexity bound

Records are grouped by venue and all limit/booking endpoints form one sorted
sweep.  At most O(N) constant-state segments exist because every segment
begins at an endpoint, and the minute axis is never materialized.  The
effective capacity is the minimum of the default capacity and the active
limits, maintained as a min-heap with explicit per-limit retirement tokens:
a lazy "pop expired top" rule alone is unsound because an expired
high-capacity entry can hide under an active low-capacity top, so each
limit's unique token is marked retired at its end and discarded when it
reaches the heap top.  Load is a running sum updated only at booking
endpoints.

For per-booking relief, an overloaded segment of length `d`, capacity `c`,
load `L` and excess `e = L-c` contributes `d·min(u, e)` when a booking of
`u` units is removed.  Splitting the active unit classes `1..L` at `e`

* adds `d` to a Fenwick tree over classes `1..e`, and
* adds `d·e` to a second Fenwick tree over classes `e+1..L`,

so two O(log 1000) point queries answer `relief(u) = minutes(u)·u + flat(u)`
for any booking size.  Each booking takes a snapshot pair at its start and
compares it at its end; the plan is never recomputed per booking and no
segment scans its active bookings.  The identity is cross-checked against an
independent minute-grid brute force on hundreds of randomized instances in
the test suite.

With `N` total records across both input files: sorting and the heap/BIT
operations take O(N log N) ⊂ O(N log²(2+N)) worst-case time; auxiliary space
and the number of report entries are both O(N).  Integer arithmetic covers
spans up to 10⁹ minutes and loads up to 20 000·1000 units without overflow.

# League ticket close (league-ticket-close, independent report envelope)

`league-ticket-close --source FILE --ticketing FILE --from-day L --to-day R
--output REPORT` settles cross-fixture league ticket orders for one closing
window `[L,R)`.  It is a standalone offline command: it never changes the
source snapshot and its report envelope (`schema_version=1`,
`source_sha256`, `ticketing_sha256`, `from_day`, `to_day`, `fixtures`,
`entries`) is independent of every other envelope.  All validation is
global over the complete snapshot; malformed input or I/O failure exits 2
and preserves any previous report, success exits 0.

## Inputs

The `--source` file is the original trusted league snapshot under the same
strict contract as league-table.  Ticket lines reference fixtures by id;
pending and completed fixtures may both be referenced, and distinct
fixtures between the same two teams are never merged — every amount is
accounted per fixture id.

The `--ticketing` file is the complete sales+refunds snapshot, strict JSON
with the same 8 MB / duplicate-key / identifier rules and exactly two
top-level arrays:

* `sales` — `{"id","day","discount_cents","lines"}`.  Sale ids are unique.
  `lines` is a non-empty array of `{"id","fixture","quantity",
  "list_price_cents"}`; line ids are unique within their own order only.
  `quantity` is an integer 1..1000000, `list_price_cents` 0..1000000 and
  `discount_cents` an integer from 0 up to the order's pre-discount total.
* `refunds` — `{"id","day","sale","line","first","stop"}`.  Refund ids are
  unique.  `sale`/`line` reference the original order and line; `[first,
  stop)` is the half-open ticket-number range with
  `1 <= first < stop <= quantity+1`; the refund day must not precede its
  sale day (same-day refunds are legal).

Sales, total lines across all orders and refunds each hold at most 20000
records.  `day`, `L` and `R` are integers in 0..1000000000 with `L < R`;
amounts, quantities, ticket numbers and days reject booleans (and floats).
Input array order never influences the business result.

## Discount allocation and per-ticket net

A line's pre-discount amount is `quantity * list_price_cents`.  The order
discount is split across its lines proportionally to these amounts, exact
to the cent: each line first receives the integer part of its exact share
`discount * pre / total`; the remaining cents go one per line to the
largest fractional remainders, ties broken by ascending line id.  The
remainder is always smaller than the line count, so no line ever receives
more than one extra cent.  When the order's pre-discount total is zero
every line's discount is zero.

A line's net (`pre - allocated discount`, always non-negative) is then
distributed over its tickets: every ticket first receives the integer part
`net // quantity`, and the `net % quantity` remaining cents go one each to
the lowest ticket numbers.  Ticket `t` therefore carries `base + 1` cents
for `t <= extra` and `base` otherwise, where `(base, extra) = divmod(net,
quantity)`.

## Refunds

A refund selects a continuous ticket range of one original line; its
amount is by definition the sum of the per-ticket nets of the selected
tickets at sale time.  Across the complete input each ticket is refunded
at most once — adjacent but disjoint ranges may be refunded separately,
and free (zero-cent) tickets are protected exactly like paid ones.  The
closing window only decides which records become entries of this period;
it never narrows reference resolution or the duplicate-refund check, so
records outside the window — before or after — are validated exactly like
in-window ones, and an illegal record after the window still rejects the
whole input.

## Report

```json
{"schema_version": 1,
 "source_sha256": "<sha256 of the source file bytes>",
 "ticketing_sha256": "<sha256 of the ticketing file bytes>",
 "from_day": 10,
 "to_day": 20,
 "fixtures": [
   {"fixture": "m1", "opening_cents": 10, "sales_cents": 0,
    "refunds_cents": 6, "closing_cents": 4}],
 "entries": [
   {"kind": "refund", "day": 10, "fixture": "m1", "sale": "s1",
    "line": "lineA", "refund": "r1", "first": 2, "stop": 4,
    "amount_cents": 6}]}
```

* `fixtures` — one row per source fixture id, ascending, including
  all-zero rows.  `opening_cents` sums line nets of sales with `day < L`
  minus refund amounts with `day < L`; `sales_cents`/`refunds_cents` sum
  the non-negative in-window (`[L,R)`) amounts; `closing_cents =
  opening + sales - refunds`.  Legal records with `day >= R` contribute
  nothing.
* `entries` — one row per in-window sale line `{kind:"sale", day, fixture,
  sale, line, amount_cents}` and per in-window refund `{kind:"refund",
  day, fixture, sale, line, refund, first, stop, amount_cents}`, including
  zero amounts.  Refund entries carry the original order, line and ticket
  range and are attributed to the original line's fixture even when the
  sale happened before the window.  Ordering: business day, sales before
  refunds, then sale id, line id and (refunds only) refund id, all
  ascending.

Each input file keeps its own byte-faithful digest; every other business
structure is identical under input permutation.  The output must differ
from both inputs after path resolution, symlink outputs are rejected, and
publication reuses the atomic temp-file + `os.replace` publisher: any
parse, validation or computation failure exits 2 and leaves a previous
report exactly in place.

## Compact representation: why per-ticket nets are refund-independent

A single line may hold one million tickets, so the engine never expands
per-ticket state.  Each line's per-ticket net is a two-step function fully
described by `(quantity, base, extra)` — tickets `1..extra` carry
`base + 1`, tickets `extra+1..quantity` carry `base` — and a range sum is
the O(1) closed form

    sum(first, stop) = (stop - first) * base
                       + max(0, min(stop - 1, extra) - first + 1)

This triple is derived once from the validated sale line (its net and
quantity) before any refund is processed and is never mutated afterwards:
refunds only read it.  A refund's amount is therefore anchored to the
original sale alone and cannot be shifted by the presence, absence or
ordering of other refunds.

## Cross-period duplicate-refund verification

Every refund range of the complete snapshot — regardless of business day —
is grouped by `(sale, line)`, sorted, and any overlap (including a single
shared ticket, paid or free) rejects the whole input before any report is
produced.  Because the ticketing file is the complete sales+refunds
snapshot and the window only filters report entries, a ticket refunded on
an earlier business day and again inside the window appears twice in the
file and is always caught; adjacent ranges that merely touch
(`stop == first`) remain legal.

With `S` sales, `L` lines and `R` refunds (each <= 20000), validation and
aggregation take O((S+L+R) log(S+L+R)) time and O(S+L+R) space; the
ticket-number axis (up to 10⁶ per line) and the day axis (up to 10⁹) are
never materialized.  Integer arithmetic covers amounts up to
20000·10⁶·10⁶ cents without overflow.

# League suspension accounting (league-suspension, independent report envelope)

`league-suspension --source FILE --discipline FILE --output REPORT` explains
which case a transferred player served in which fixture, and when a player
with an open balance took the pitch.  It is a standalone offline command:
the `--source` file is parsed through the unchanged league-table contract
(`teams`, `fixtures`, `results`, `deductions`), standings and deduction
rules are never recomputed or rewritten, and the report envelope
(`schema_version=1`, `source_sha256`, `discipline_sha256`, `events`,
`cases`) is independent of every other envelope.  A suspension violation
is a business finding and exits 0; only malformed input exits 2.

## Inputs

The source snapshot keeps its existing strict contract.  Fixture identity
and the two participating teams come exclusively from the formal
`fixtures` rows (`id`, `home`, `away`); a fixture counts as completed iff
a formal `results` row exists for that id.  Scores and points deductions
never influence suspension rules, and discipline data is never written
back into results.

The independent `--discipline` file is strict JSON with the same 8 MB,
duplicate-key, unknown-field, non-finite-number and identifier rules, and
exactly these six top-level fields:

* `schema_version` — the integer `1`.
* `players` — known player ids, at most 50 000, unique.
* `fixture_order` — `{"fixture","seq"}` rows; every formal fixture id
  (pending fixtures included) appears exactly once.  Distinct fixtures may
  share a sequence number, but one team plays at most one fixture on any
  sequence number, pending fixtures included.
* `registrations` — `{"player","team","seq"}` rows, at most 100 000.
  Players start unregistered.  A known team id sets the registration
  (replacing any prior one); `null` deregisters.  At most one
  registration row per player per sequence.
* `issues` — `{"case_id","player","seq","games"}` rows, at most 100 000.
  `case_id` is globally unique and `games` is an integer 1..100000
  (booleans rejected).  A case may be imposed on a known player who is
  currently unregistered.
* `revocations` — `{"case_id","seq"}` rows, at most 100 000, each
  targeting an issued case at a strictly later sequence, at most once per
  case.  Revoking a fully served case is legal and waives zero games.
* `appearances` — `{"fixture","players"}` rows, exactly one per formally
  completed fixture (an empty array means nobody actually played) and
  none for pending fixtures.  `players` is the merged id list of everyone
  who actually took the pitch for either side (nominees who never play are
  excluded), unique within the fixture.  Every appearing player must be
  registered at kick-off to one of that fixture's two teams; this is
  checked even for players with no open balance.

All sequences are integers in 1..1000000000 and need not be contiguous;
registrations, issuances and revocations may land on sequences without a
fixture and after the last fixture.  Input array order never denotes
effect order.  Every player, team, fixture and case reference must be
valid.

## Ordered replay and per-case accounting

Rows are replayed in ascending sequence number.  Within one sequence the
order is fixed:

1. registration changes;
2. case issuances and revocations (decisions on different cases at the
   same sequence never interfere);
3. fixture judgments, only after every decision above has settled.

Each case keeps its own `imposed / served / waived / remaining` counters.
A revocation moves the case's current `remaining` into `waived` and keeps
its `served` history, so serving records are never erased.  Balances are
attached to the player, not the team: a transfer (or a deregistration
followed by re-registration) moves the outstanding cases unchanged.
Unregistered players and players who take no part in any completed
fixture of a sequence consume nothing, and pending fixtures consume
nothing — later completed fixtures always judge against the balances left
by every prior per-case service and revocation.

For a formally completed fixture, among players registered to a
participating team at kick-off:

* a player who appears in the line-up while owing a balance records one
  `violation` attributed to the earliest outstanding case, and **no**
  balance is deducted;
* a player who does not appear and still owes a balance serves one game
  against the earliest outstanding case, decrementing its `remaining` and
  incrementing its `served`.

The earliest outstanding case of a player is the minimum by
`(issued_seq, case_id)`, excluding served-out and revoked cases; id
ordering is the Unicode code-point order of the strings.  One completed
fixture produces at most one event per player.

## Report

```json
{"schema_version": 1,
 "source_sha256": "<sha256 of the source file bytes>",
 "discipline_sha256": "<sha256 of the discipline file bytes>",
 "events": [
   {"seq": 1, "fixture": "m1", "team": "A", "player": "p",
    "case_id": "x", "kind": "served"}],
 "cases": [
   {"case_id": "x", "player": "p", "issued_seq": 1,
    "revoked_seq": 4, "imposed": 2, "served": 1,
    "waived": 1, "remaining": 0}]}
```

* `events` — `{"seq","fixture","team","player","case_id","kind"}` rows
  where `team` is the player's kick-off registration and `kind` is
  `served` or `violation`, sorted by `(seq, fixture, player)`.
* `cases` — one row per issued case, including cases with no accounting
  event, sorted by `case_id`; `revoked_seq` is `null` when the case was
  never revoked.  Every row satisfies
  `imposed = served + waived + remaining` with non-negative counters.

Each input file keeps its own byte-faithful digest.  Identical input
bytes produce identical report bytes; permuting input arrays changes
nothing except the discipline digest.  The output must differ from both
inputs after path resolution, symlink outputs are rejected, and
publication reuses the atomic temp-file + `os.replace` publisher: any
parse, validation or computation failure exits 2 and leaves a previous
report exactly in place.

## How affected fixtures and players are located

The engine never expands empty sequences and never scans the full player
population per fixture.  During one indexing pass every registration,
issuance, revocation and completed fixture is filed under its sequence
number, so the sweep visits only sequence numbers that carry work.

Per player, outstanding cases live in a heap keyed by
`(issued_seq, case_id)`; finished or revoked entries are popped lazily
when the earliest outstanding case is queried, which gives contract order
without re-sorting.  Per team, a membership set names exactly the
registered players who currently have an active balance.  The set is
re-synced only at the points that can change membership — a registration
change, an issuance, a revocation, or a service that zeroes a case — so
balances can follow a player across transfers while stale team sets are
never read.  A fixture then touches only its own line-up plus the two
participating teams' membership sets.

## Complexity

Let `N` be the total number of discipline input rows (players, ordering,
registrations, issues, revocations, appearances) and `R` the number of
report rows (`events` plus one row per issued case).  Indexing and the
sequence sweep are linear in the rows, heap and set operations cost
`O(log N)` amortized, and final sorting costs `O(R log R)`; overall time
is `O(N log N + R log R)` and space is `O(N + R)`.  The sequence axis
(up to 10⁹), never-issued events and players with no balance are never
materialized.
