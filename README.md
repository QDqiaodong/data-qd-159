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
.venv/bin/python -m unittest discover -s tests -v
```

本地已有wheel时，安装构建工具可加 `--no-index --find-links <公共wheel缓存目录>`。
修改源码后重新安装。本仓不共享其他仓环境，不依赖Docker、外部账号或持续服务。

输入JSON（源文件与可选观测文件各自）最多8MB，拒绝重复键、非有限数值和未定义字段；各文件源字节SHA256随结果保留。
输出是可独立读取的JSON；成功后原子替换，输入/计算失败退出2并保留旧报告。
输入和输出不得相同，输出符号链接拒绝。单操作者批处理，不承诺并发发布或断电恢复。
同输入重跑不累加。测试使用临时目录，运行产物在忽略的run/。

src/league_table/model.py 为业务模型，相关算法模块独立组织；common.py 提供受限JSON及报告发布，
reconciliation.py 实现可审计赛果观测对账，cli.py 衔接数据链路。fixtures 为明确自制样例，代码与样例采用MIT许可。

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
