# data-qd-159 · 联赛赛果积分核算

Auditable league standings from fixtures results and deductions。业务输入、计算和当前边界详见 [数据契约](docs/contract.md)。

## 安装与演示

Python >=3.11，运行和测试仅用标准库；先确认 `python3 --version`。
每仓建立独立环境，构建工具锁版本。首次安装无公共缓存时可能联网；业务运行不访问网络。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps .
.venv/bin/python -m pip check
.venv/bin/league-table --source fixtures/input.json --output run/report.json
.venv/bin/league-table --source fixtures/input-reconciliation.json \
  --observations fixtures/observations.json --output run/reconciliation.json
.venv/bin/league-table --source fixtures/input-tiebreak.json \
  --tie-break-profile fixtures/tie-break-profile.json --output run/tiebreak.json
.venv/bin/league-venue-audit --source fixtures/venue-audit-source.json \
  --schedule fixtures/venue-audit-schedule.json --output run/venue-audit.json
.venv/bin/python -m unittest discover -s tests -v
```

本地已有wheel时，安装构建工具可加 `--no-index --find-links <公共wheel缓存目录>`。
修改源码后重新安装。本仓不共享其他仓环境，不依赖Docker、外部账号或持续服务。

输入JSON（源文件与可选观测文件各自）最多8MB，拒绝重复键、非有限数值和未定义字段；各文件源字节SHA256随结果保留。
输出是可独立读取的JSON；成功后原子替换，输入/计算失败退出2并保留旧报告。
输入和输出不得相同，输出符号链接拒绝。单操作者批处理，不承诺并发发布或断电恢复。
同输入重跑不累加。测试使用临时目录，运行产物在忽略的run/。

src/league_table/model.py 为业务模型，相关算法模块独立组织；common.py 提供受限JSON及报告发布，
reconciliation.py 实现可审计赛果观测对账，cli.py 衔接数据链路。src/league_history 为离线赛果
历史仓：timeline.py 是纯区间引擎，payloads.py 严格校验批次并生成规范内容，store.py 负责
SQLite 双时态持久化与单事务提交，cli.py 提供 init/apply/query。src/league_venue_audit 为
场地容量审计：audit.py 是端点扫描线（最小堆维护有效容量、双 Fenwick 树一次扫描给出每笔
预订的撤销减量），payloads.py 严格校验独立排期 JSON，cli.py 提供 league-venue-audit。
fixtures 为明确自制样例，代码与样例采用MIT许可。

## 业务与样例

# League standings v1
赛事数据整理者输入唯一teams(1..200)、fixtures(<=20000，id/home/away)、results(fixture/home_goals/away_goals)和deductions(team/points/reason)。赛程双方必须为不同已知球队，赛果只允许一个已知fixture一次；进球为0..1000整数不接受bool。赛程允许同双方不同ID的多轮交锋，未提供赛果的比赛单列pending，不记0:0。
所有输入赛果均表示正式完赛，固定胜3平1负0；双方played与进失球配对守恒。每队最多一个已生效扣分(0..1000，原因非空)，可使总积分为负。输入为调用方完整可信快照，无日期推断、暂存赛果或取消状态。
依积分、净胜球、进球数降序；三者均同则共享competition rank（1,1,3），按队ID排展示顺序，不伪造胜负关系。无head-to-head或法定联赛规则声明。
样例C4分第一、A扣1后2分第二、B1分第三、D0分第四；m4待赛。支持空赛果、不参与球队保持0场。无博彩、真实赛程采集或判罚建议，合成数据。

## 赛果观测对账（可选 --observations）

独立赛果观测只带球队与候选赛程；整理者不得把一次猜测当成正式事实。在候选图上求
全局合法完整分配（每条观测选一个候选、每个赛程至多一条观测占用），只有一条观测在
**所有**合法完整分配中都只能落到同一赛程时（possible_fixtures 恰为单元素）才按球队
身份换算主客方向并导入，其余继续待赛。存在完整分配时 status="feasible"；不存在时
status="infeasible"、不导入任何观测、result 保持该 source 的基线核算，并给出可独立
校验的 Hall 证书 S 与完整邻居 N(S)（|N(S)|<|S|）。不可行属于业务诊断，退出0。

```sh
# 可行：o1 唯一落到 m1，o2 唯一落到 m2，两者导入
.venv/bin/league-table --source fixtures/input-reconciliation.json \
  --observations fixtures/observations.json --output run/feasible.json
# 仅一条观测、候选 [m1,m2]：两项都列入 possible_fixtures，均待赛
.venv/bin/league-table --source fixtures/input-reconciliation.json \
  --observations fixtures/observations-ambiguous.json --output run/ambiguous.json
# 两条观测只能选 m1：infeasible，证书 S 与 N(S)
.venv/bin/league-table --source fixtures/input-reconciliation.json \
  --observations fixtures/observations-infeasible.json --output run/infeasible.json
```

对账模式报告 schema_version=2，保留 source_sha256 与 result，增加按观测文件原始字节
计算的 observations_sha256 及 reconciliation；不带 --observations 时仍是 schema_version=1
的旧包络。字段、边界与全局可行性/证书正确性说明见 [数据契约](docs/contract.md)。

## 头对头排名分析（可选 --tie-break-profile）

`--tie-break-profile` 与 `--observations` 互斥；不传任何可选参数时，schema_version=1
报告与原排名行为完全不变。profile 为严格 JSON，精确含两个有序、非空、无重复的数组，
字段仅支持 points/goal_difference/goals_for，禁止隐式假定赛制：

```json
{"head_to_head":["points","goal_difference","goals_for"],
 "overall":["goal_difference","goals_for"]}
```

含扣分后的总积分仍是第一排序准则，profile 只作用于总积分相同的球队组。头对头小表
只取该组双方都在组内且已完赛的正式 fixture（pending 不计、扣分不重复计算，多轮交锋
全部计入），按 profile 顺序降序拆分；首个能拆分的准则生效后，每个仍并列的子组从首条
头对头准则在自己的受限 fixture 集上重新计算——重算可能改变沿用父组受限行所得次序。
头对头准则均无法拆分时，才按 overall 字段顺序用全局累计值回退；最终仍相同者保留共享
competition rank（1,1,3 留空规则），展示按球队 ID 升序，球队 ID 从不作为竞技准则。

```sh
# 五队样例：A/B/C/D 同 6 分，首轮头对头积分拆出 {A}；子组 {B,C,D} 重算净胜球
# 得到 B>D>C（沿用父组受限行则三者净胜球同为 -2 无法拆分），e1 待赛不计入
.venv/bin/league-table --source fixtures/input-tiebreak.json \
  --tie-break-profile fixtures/tie-break-profile.json --output run/tiebreak.json
```

政策模式报告为 schema_version=2，保留 source_sha256 与 result，并增加 tie_break_audit：
按初始总积分并列组顺序输出审计节点，递归子组各自单独记录；每节点含升序 team_ids、
实际使用的组内 fixture_ids、按 profile 顺序且区分 head_to_head/overall 来源的
criterion_values，以及按最终排名排列的 subgroups；无并列组时 nodes 为空。profile 非法
（未知/重复字段、空准则、多余键、重复 JSON 键等）退出 2 并保留已有报告。完整字段与
递归语义见 [数据契约](docs/contract.md)。

## 离线赛果历史仓 league-history（init/apply/query）

`league-history` 是可独立使用的本地 SQLite 历史仓，面向已有明确 fixture 身份的赛果
更正与撤回；它不接观测文件，也不改变积分、扣分、pending 与 competition rank 规则，
每个时间段直接复用原 `league_table.standings.calculate`。业务时刻为 0..1000000000
整数（拒绝 bool），区间一律 `[left,right)`，`right=null` 表示正无穷；知识序号
1,2,... 严格递增，0 表示 init 冻结快照。

```sh
.venv/bin/league-history init --source fixtures/history-source.json \
  --db run/history.db --output run/h-init.json
# 批1：[10,30) 2:0 改 0:1；[20,25) 撤回为 pending
.venv/bin/league-history apply --db run/history.db \
  --batch fixtures/history-batch1.json --output run/h-b1.json
# 回看知识序号1、业务窗口[0,40)：2:0 / 0:1 / pending / 0:1 / 2:0 五段
.venv/bin/league-history query --db run/history.db --as-of 1 \
  --from 0 --to 40 --output run/h-q1.json
# 无限右界用字符串 inf；k=0 重现 init 当时所见
.venv/bin/league-history query --db run/history.db --as-of 0 \
  --from 0 --to inf --output run/h-q0.json
# 该批因 [20,25) 内部 pending 洞被整批拒绝（退出2，仓内无变化）
.venv/bin/league-history apply --db run/history.db \
  --batch fixtures/history-batch2-rejected.json --output run/h-b2.json
```

每条操作要求 `expected` 等于该 fixture 在整个区间（含全部内部子段与 pending 空洞）的
现值；`replacement=null` 撤回为 pending 而非 0:0。批内操作按文件顺序在批次私有工作态
执行，任一条失败则整批回滚。同一 `batch_id` 重放同一规范业务内容（忽略键次序与空白、
保留 operations 次序）返回首次序号且无新增写入，重放先于 expected 校验；同 ID 异内容
报错。所有区间变更、历史与序号在单事务提交，两个进程竞争同一旧区间时至多一个成功。
报告包络为独立的 `schema_version=1`，含 `store_id`、`source_sha256`、`knowledge_seq`，
query 另含精确为 `{left,right,result}` 的 `timeline`，无限右界输出 null。完整 CLI、
批次格式、双时态持久化与并发语义见 [数据契约](docs/contract.md)。

## 场地容量审计 league-venue-audit

赛程发布前核对既定预订是否超过场地可用容量，并量化单独撤销每笔预订能缓解多少超限。
排期文件只含 `venues`/`limits`/`bookings` 三个数组（各至多 20000 项、允许为空）：
场地默认容量 0..1000；限制引用已知场地、可重叠可相同、容量 0 表示关闭，有效容量恒为
默认容量与所有生效限制的最小值；预订引用已知场地和原快照中**无正式赛果**的 fixture，
每个 fixture 至多一笔，未预订的待赛场次列为 `unscheduled_fixtures`。分钟为
0..1000000000 的整数且 `start < end`，布尔值一律拒绝。

整数半开时段 `[start,end)` 内，占用为该场地所有生效预订的单位数之和；每分钟超限量为
占用超过有效容量的正差，`excess_unit_minutes` 是其按时长的全计划总和。`overloads` 报告
同一场地容量与占用均不变且持续超限的最大连续时段，仅限制来源或占用成员变化但容量、占用
均未变化时不切段，按场地 ID、开始分钟升序。`booking_relief` 含每笔预订（含减量 0），
为只移除该预订占用（容量与其他预订不变）后整份计划减少的超限单位分钟；各笔减量相对同一
原计划，不能相互累加解释成联合撤销。

```sh
# 验收样例：默认容量3，限制[3,7)=2、[5,9)=1，两笔各2单位预订[0,8)、[2,6)
# 超限总量10；单独撤销两笔减量分别为9、7；四段 (3,4)(2,4)(1,4)(1,2)
.venv/bin/league-venue-audit --source fixtures/venue-audit-source.json \
  --schedule fixtures/venue-audit-schedule.json --output run/venue-audit.json
```

生产计算不展开逐分钟状态，也不逐段扫描活跃预订或逐笔重算计划：所有限制/预订端点合并为
一次 O(N log N) 扫描，最小堆（带显式退役标记）维护有效容量、双 Fenwick 树在单位轴
1..1000 上以 O(log 1000) 区间累加同时回答所有预订的减量快照；最坏时间
O(N log²(2+N))、额外空间与报告条目 O(N)。超限是业务事实退出 0；无效输入退出 2 并复用
原原子发布保留旧报告，输出不得与任一输入同路径、符号链接拒绝。算法恒等式在测试中由独立
逐分钟暴力实现对数百组随机实例交叉验证。完整字段、正确性论证与复杂度见
[数据契约](docs/contract.md)。
