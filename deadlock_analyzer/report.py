from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class CycleEvidence:
    """单个等待环的证据。"""

    cycle_id: str
    transactions: List[str]
    is_self_wait: bool
    edges: List[Dict[str, str]]
    active_members: List[str]
    aborted_members: List[str]
    auto_resolvable: bool


@dataclass
class VictimStep:
    """一次牺牲者决策。"""

    order: int
    txn_id: str
    score: float
    score_breakdown: Dict[str, float]
    reason: str
    cycles_broken: List[str]
    cycles_before: List[str]
    remaining_cycles: List[str]


@dataclass
class AnalysisReport:
    """完整分析结果。分析器只产出 kill 候选，不执行任何数据库操作。"""

    initial_cycles: List[CycleEvidence]
    victim_order: List[str]
    victim_steps: List[VictimStep]
    remaining_cycles: List[CycleEvidence]
    unresolved_cycles: List[CycleEvidence]
    unresolved_reasons: List[str]
    duplicate_edges: List[Dict[str, str]]
    auto_resolved: bool

    def to_dict(self):
        # type: () -> dict
        from dataclasses import asdict

        return asdict(self)
