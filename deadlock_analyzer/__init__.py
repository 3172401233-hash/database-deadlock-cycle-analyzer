"""数据库锁等待图死锁分析器。

本包只分析等待图并生成 kill 候选与解释证据，
不会连接数据库，也不会执行任何终止/回滚操作。
"""

from .analyzer import DeadlockAnalyzer
from .graph import WaitGraph, build_wait_graph
from .model import LockState, Transaction, TransactionStatus, WaitEdge
from .report import AnalysisReport, CycleEvidence, VictimStep

__all__ = [
    "DeadlockAnalyzer",
    "WaitGraph",
    "build_wait_graph",
    "LockState",
    "Transaction",
    "TransactionStatus",
    "WaitEdge",
    "AnalysisReport",
    "CycleEvidence",
    "VictimStep",
    "analyze",
]


def analyze(transactions, lock_states, **kwargs):
    """便捷入口：构造分析器并执行一次死锁分析。"""
    analyzer = DeadlockAnalyzer(**kwargs)
    return analyzer.analyze(transactions, lock_states)
