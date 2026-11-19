# database-deadlock-cycle-analyzer

数据库锁等待图（wait-for graph）死锁分析器：识别全部强连通等待环，
综合回滚成本、连续被牺牲次数与业务优先级选出一组牺牲事务，并在每次
移除牺牲者后重新计算等待图，输出可解释的 kill 候选顺序与证据。

> **安全边界：本工具只分析内存中的等待图并生成 kill 候选与解释报告，
> 不连接任何数据库，也不会执行 `KILL`、回滚或其他副作用操作。**
> 是否终止事务、何时终止，完全由调用方在数据库侧自行决定。

## 运行环境

- Python 3.8.2+，仅使用标准库（`dataclasses`、`typing`、`unittest`）。

## 输入模型

- `Transaction`：事务 id、`age_seconds`（年龄）、`work_done`（已执行
  工作量）、`retries`（连续被牺牲后重试次数）、`priority`（业务优先级，
  数值越大越重要）、`status`（`active` / `aborted`）。
- `LockState`：锁 id、当前持有者 `holder`、按顺序排队的 `waiters`。
  每个 waiter 对 holder 产生一条有向等待边 `waiter -> holder`。

## 分析流程

1. 构造等待图并去重：同一对事务在同一把锁上的重复边只保留一次，
   不同锁产生的等待合并为一条边但保留全部锁 id 作为证据，重复边
   会在报告中列出。
2. 用 Tarjan 算法求强连通分量，定位所有死锁区域（含单节点自等待），
   再用 Johnson 算法枚举其中的全部基础环，保证每个环都有完整证据。
3. 当多个环共享事务时，把当前所有环上的事务放在一起统一打分，每轮
   只选一个牺牲者，避免逐环重复回滚同一批事务。
4. 移除牺牲者后重新计算等待图，循环直到没有环，或确认无法自动消解。

### 牺牲者评分

分数越低越优先被牺牲，四个因子相乘（权重可在 `DeadlockAnalyzer` 构造
时调整）：

- 年龄因子 `1 + age / max_age`：越年轻回滚成本越低；
- 工作量因子 `1 + work / max_work`：已执行工作越少损失越小；
- 优先级因子：低优先级事务因子更小，高优先级事务受到保护；
- 重试因子 `(1 + retries) ** 1.5`：连续被牺牲过的事务分数被显著抬高，
  防止同一低权重事务长期反复成为受害者。

分数相同时，优先选择一次能打破更多环的事务；再相同则按事务 id
稳定决策，保证结果可复现。

### 无法自动消解

- 环上事务全部已是 `aborted` 状态：没有可牺牲的活动事务；
- 达到调用方设置的 `max_victims` 上限：保留剩余环并在
  `unresolved_reasons` 中说明原因。

## 快速开始

```python
from deadlock_analyzer import Transaction, LockState, analyze

transactions = [
    Transaction("T1", age_seconds=100, work_done=900, priority=9),
    Transaction("T2", age_seconds=20, work_done=100, priority=2),
    Transaction("T3", age_seconds=60, work_done=500, priority=5),
]
lock_states = [
    LockState("L1", holder="T2", waiters=["T1"]),
    LockState("L2", holder="T3", waiters=["T2"]),
    LockState("L3", holder="T1", waiters=["T3"]),
]

report = analyze(transactions, lock_states)
print(report.victim_order)          # ['T2']
print(report.initial_cycles[0].edges)
print(report.to_dict())             # 可 JSON 序列化的完整报告
```

也支持字典输入：`Transaction.from_dict(...)`、`LockState.from_dict(...)`。

## 报告内容

- `initial_cycles`：初始等待图中每个环的证据（事务序列、逐条边及对应
  锁、是否自等待、活动/已中止成员、是否可自动消解）；
- `victim_order` 与 `victim_steps`：牺牲者顺序，每步含评分明细、
  选择理由、本轮打破的环、本轮前的环与移除后的剩余环；
- `remaining_cycles` / `unresolved_cycles`：分析结束后仍存在的环；
- `unresolved_reasons`：无法自动消解的具体原因；
- `duplicate_edges`：建图时发现并去重的重复等待边；
- `auto_resolved`：所有环是否都已通过牺牲者消解。

## 测试

覆盖单环、多环交叉、共享事务、自等待、已中止事务、重复边、饥饿
保护与无死锁场景：

```bash
python -m unittest
```

## 目录结构

```
deadlock_analyzer/
    __init__.py     # 包入口与 analyze 便捷函数
    model.py        # Transaction / LockState / WaitEdge 输入模型
    graph.py        # 等待图、Tarjan SCC、Johnson 基础环枚举
    analyzer.py     # 评分、牺牲者选择、迭代重算与不可消解判定
    report.py       # CycleEvidence / VictimStep / AnalysisReport
tests/
    test_analyzer.py
```
