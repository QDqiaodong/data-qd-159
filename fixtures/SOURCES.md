本目录为原创虚构合成数据，使用MIT许可。没有真实个人、线上实验、生产日志、交易、项目进度或搜索用户信息；不来自外部平台。

- `input.json`：v1 积分核算基线样例（m4 待赛）。
- `input-reconciliation.json`：对账样例源快照，m1/m2 同一对球队不同主客方向，m3 为既有正式赛果。
- `observations.json`：两项均唯一解析并导入（o2 主客方向与 m2 相反）。
- `observations-ambiguous.json`：单条观测候选 [m1,m2]，两项均待赛。
- `observations-infeasible.json`：两条观测只能选 m1，输出 S 与 N(S) 的 Hall 证书。
- `history-source.json`：历史仓 init 源快照，A 主场对 B 的 m 赛果 2:0。
- `history-batch1.json`：历史仓验收批 1，先在 [10,30) 改 2:0 为 0:1，再在 [20,25) 撤回为 pending。
- `history-batch2-rejected.json`：在 [15,28) 以两端均为 0:1 的 expected 改 1:1，因内部 pending 洞被整批拒绝。
