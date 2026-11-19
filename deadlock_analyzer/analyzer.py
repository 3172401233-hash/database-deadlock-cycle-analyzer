from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .graph import WaitGraph, build_wait_graph
from .model import LockState, Transaction
from .report import AnalysisReport, CycleEvidence, VictimStep


class DeadlockAnalyzer:
    """锁等待图死锁分析器。

    分析流程：
    1. 构造等待图并去重；
    2. 用强连通分量定位所有死锁区域，枚举其中的全部基础等待环；
    3. 对跨多个环的事务集合统一打分，只选一个牺牲者，避免逐环重复回滚；
    4. 移除牺牲者后重新计算等待图，直到没有环或确认无法自动消解。

    评分越低越优先被牺牲。评分综合回滚成本（年龄、工作量、优先级）
    与连续重试次数：曾被反复牺牲的事务分数被显著抬高，防止低权重事务
    长期成为受害者。
    """

    def __init__(
        self,
        weight_age=1.0,
        weight_work=2.0,
        weight_priority=1.5,
        weight_retry=3.0,
        retry_exponent=1.5,
        max_victims=None,
    ):
        # type: (float, float, float, float, float, Optional[int]) -> None
        self.weight_age = weight_age
        self.weight_work = weight_work
        self.weight_priority = weight_priority
        self.weight_retry = weight_retry
        self.retry_exponent = retry_exponent
        self.max_victims = max_victims

    def analyze(self, transactions, lock_states):
        # type: (Sequence[Transaction], Sequence[LockState]) -> AnalysisReport
        transactions_by_id = {txn.txn_id: txn for txn in transactions}
        graph = build_wait_graph(transactions, lock_states)

        initial_evidence = self._build_evidence(graph, transactions_by_id)
        victim_order = []  # type: List[str]
        victim_steps = []  # type: List[VictimStep]
        unresolved_reasons = []  # type: List[str]
        step = 0

        while True:
            cycles = graph.elementary_cycles()
            if not cycles:
                break

            if self.max_victims is not None and step >= self.max_victims:
                unresolved_reasons.append(
                    "达到最大牺牲者数量上限 %d，剩余 %d 个环未处理"
                    % (self.max_victims, len(cycles))
                )
                break

            cyclic_nodes = set()  # type: Set[str]
            for cycle in cycles:
                cyclic_nodes.update(cycle)
            eligible = sorted(
                node
                for node in cyclic_nodes
                if not transactions_by_id[node].is_aborted
            )
            if not eligible:
                unresolved_reasons.append(
                    "剩余环中的事务全部已中止，无法自动选择牺牲者"
                )
                break

            step += 1
            evidence_before = self._build_evidence(graph, transactions_by_id)
            ids_before = [item.cycle_id for item in evidence_before]
            coverage = {node: 0 for node in cyclic_nodes}  # type: Dict[str, int]
            for cycle in cycles:
                for node in cycle:
                    coverage[node] += 1

            scores = {
                node: self._score(node, graph, transactions_by_id)
                for node in eligible
            }
            # 主排序：分数最低；分数相同时覆盖环数多者优先；再相同按 id 稳定决策。
            victim = min(
                eligible,
                key=lambda node: (
                    scores[node][0],
                    -coverage[node],
                    node,
                ),
            )
            victim_score, breakdown = scores[victim]
            cycles_broken = sorted(
                item.cycle_id
                for item in evidence_before
                if victim in item.transactions
            )

            graph.remove_transaction(victim)
            victim_order.append(victim)
            remaining = graph.elementary_cycles()
            victim_steps.append(
                VictimStep(
                    order=step,
                    txn_id=victim,
                    score=victim_score,
                    score_breakdown=breakdown,
                    reason=self._explain(
                        transactions_by_id[victim], breakdown, coverage[victim]
                    ),
                    cycles_broken=cycles_broken,
                    cycles_before=ids_before,
                    remaining_cycles=[
                        self._cycle_id(cycle) for cycle in remaining
                    ],
                )
            )

        remaining_cycles = self._build_evidence(graph, transactions_by_id)
        # 只要分析终止时仍存在环，无论原因是成员全部已中止还是达到
        # 牺牲者上限，都属于本次无法自动消解的情况。
        unresolved_cycles = list(remaining_cycles)
        if remaining_cycles and not unresolved_reasons:
            unresolved_reasons.append("存在无法自动消解的等待环")

        return AnalysisReport(
            initial_cycles=initial_evidence,
            victim_order=victim_order,
            victim_steps=victim_steps,
            remaining_cycles=remaining_cycles,
            unresolved_cycles=unresolved_cycles,
            unresolved_reasons=unresolved_reasons,
            duplicate_edges=[
                {
                    "waiter": edge.waiter,
                    "holder": edge.holder,
                    "lock_id": edge.lock_id,
                }
                for edge in graph.duplicate_edges
            ],
            auto_resolved=not remaining_cycles,
        )

    def _score(self, txn_id, graph, transactions_by_id):
        # type: (str, WaitGraph, Dict[str, Transaction]) -> Tuple[float, Dict[str, float]]
        active = [
            txn
            for txn in transactions_by_id.values()
            if txn.txn_id in graph.nodes and not txn.is_aborted
        ]
        max_age = max((txn.age_seconds for txn in active), default=0.0) or 1.0
        max_work = max((txn.work_done for txn in active), default=0.0) or 1.0
        priorities = [txn.priority for txn in active]
        priority_span = max(max(priorities) - min(priorities), 1)

        txn = transactions_by_id[txn_id]
        age_factor = 1.0 + txn.age_seconds / max_age
        work_factor = 1.0 + txn.work_done / max_work
        # 高优先级事务的惩罚因子大于 1，低优先级事务可低于 1，更容易被牺牲。
        priority_factor = 1.0 + (
            (txn.priority - min(priorities)) / priority_span
        )
        retry_factor = float(1 + txn.retries) ** self.retry_exponent

        score = (
            age_factor ** self.weight_age
            * work_factor ** self.weight_work
            * priority_factor ** self.weight_priority
            * retry_factor ** self.weight_retry
        )
        breakdown = {
            "age_factor": round(age_factor, 6),
            "work_factor": round(work_factor, 6),
            "priority_factor": round(priority_factor, 6),
            "retry_factor": round(retry_factor, 6),
        }
        return score, breakdown

    @staticmethod
    def _explain(txn, breakdown, coverage):
        # type: (Transaction, Dict[str, float], int) -> str
        return (
            "事务 %s 回滚成本最低（年龄因子 %.3f、工作量因子 %.3f、"
            "优先级因子 %.3f、重试因子 %.3f），本次可同时打破 %d 个环；"
            "其已连续重试 %d 次"
            % (
                txn.txn_id,
                breakdown["age_factor"],
                breakdown["work_factor"],
                breakdown["priority_factor"],
                breakdown["retry_factor"],
                coverage,
                txn.retries,
            )
        )

    def _build_evidence(self, graph, transactions_by_id):
        # type: (WaitGraph, Dict[str, Transaction]) -> List[CycleEvidence]
        evidence = []  # type: List[CycleEvidence]
        for index, cycle in enumerate(graph.elementary_cycles(), start=1):
            edges = []
            pairs = [
                (cycle[idx], cycle[(idx + 1) % len(cycle)])
                for idx in range(len(cycle))
            ]
            for waiter, holder in pairs:
                edges.append(
                    {
                        "waiter": waiter,
                        "holder": holder,
                        "locks": graph.edge_locks_for(waiter, holder),
                    }
                )
            aborted = sorted(
                node
                for node in cycle
                if transactions_by_id[node].is_aborted
            )
            active_members = sorted(node for node in cycle if node not in aborted)
            evidence.append(
                CycleEvidence(
                    cycle_id=self._cycle_id(cycle),
                    transactions=list(cycle),
                    is_self_wait=len(cycle) == 1,
                    edges=edges,
                    active_members=active_members,
                    aborted_members=aborted,
                    auto_resolvable=bool(active_members),
                )
            )
        return evidence

    @staticmethod
    def _cycle_id(cycle):
        # type: (List[str]) -> str
        return "C-%s" % "-".join(cycle)
