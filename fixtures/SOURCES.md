本目录为原创虚构合成数据，使用MIT许可。没有真实个人、线上实验、生产日志、交易、项目进度或搜索用户信息；不来自外部平台。

- `input.json`：v1 积分核算基线样例（m4 待赛）。
- `input-reconciliation.json`：对账样例源快照，m1/m2 同一对球队不同主客方向，m3 为既有正式赛果。
- `observations.json`：两项均唯一解析并导入（o2 主客方向与 m2 相反）。
- `observations-ambiguous.json`：单条观测候选 [m1,m2]，两项均待赛。
- `observations-infeasible.json`：两条观测只能选 m1，输出 S 与 N(S) 的 Hall 证书。
- `input-tiebreak.json`：头对头政策样例，五队中 A/B/C/D 扣后同 6 分（E 独立），e1 待赛；首轮头对头积分拆出 {A}，子组 {B,C,D} 重算净胜球给出与父组受限行不同的次序。
- `tie-break-profile.json`：头对头准则 points/goal_difference/goals_for，全局回退 goal_difference/goals_for。
- `history-source.json`：历史仓 init 源快照，A 主场对 B 的 m 赛果 2:0。
- `history-batch1.json`：历史仓验收批 1，先在 [10,30) 改 2:0 为 0:1，再在 [20,25) 撤回为 pending。
- `history-batch2-rejected.json`：在 [15,28) 以两端均为 0:1 的 expected 改 1:1，因内部 pending 洞被整批拒绝。
- `venue-audit-source.json`：场地容量审计源快照，m1 已完赛，m2/m3/m4 待赛。
- `venue-audit-schedule.json`：验收排期，单场地默认容量 3，限制 [3,7)=2、[5,9)=1，预订 m2[0,8) 与 m3[2,6) 各 2 单位；超限总量 10，单独撤销减量 9 与 7。
- `ticket-close-source.json`：联票清分源快照，m1 已完赛（完赛可引用），m2/m3 待赛，m3 无业务输出零行。
- `ticket-close-ticketing.json`：清分验收销售与退款快照；订单 s1 第 5 日销售 lineA(m1,3 张)与 lineB(m2,2 张)、每张 4 分、整单折 3 分（A 票号实收 4/3/3，B 为 4/3），第 10 日退 A 票号 2..3 与 B 票号 1；窗口 [10,20) 下 m1 期初 10 退 6 期末 4，m2 期初 7 退 4 期末 3。
- `ticket-close-ticketing-rejected.json`：追加重票退款 r3（第 7 日退 lineA 票号 2，与窗口内 r1 重叠），即使 ID 不同且在窗口之前也整份拒绝。
