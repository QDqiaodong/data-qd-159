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
