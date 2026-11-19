from dataclasses import dataclass, field
from typing import Dict, List, Optional


class TransactionStatus:
    ACTIVE = "active"
    ABORTED = "aborted"


@dataclass
class Transaction:
    """参与锁等待的事务。

    age_seconds: 事务已存活时间（秒），年龄越大回滚代价越高。
    work_done: 已执行的工作量（如逻辑读/已修改行数等统一度量）。
    retries: 该事务连续被选为牺牲者后重试的次数，用于防止饥饿。
    priority: 业务优先级，数值越大越重要。
    """

    txn_id: str
    age_seconds: float = 0.0
    work_done: float = 0.0
    retries: int = 0
    priority: int = 5
    status: str = TransactionStatus.ACTIVE

    @classmethod
    def from_dict(cls, data):
        # type: (dict) -> "Transaction"
        return cls(
            txn_id=str(data["txn_id"]),
            age_seconds=float(data.get("age_seconds", 0.0)),
            work_done=float(data.get("work_done", 0.0)),
            retries=int(data.get("retries", 0)),
            priority=int(data.get("priority", 5)),
            status=str(data.get("status", TransactionStatus.ACTIVE)),
        )

    @property
    def is_aborted(self):
        # type: () -> bool
        return self.status == TransactionStatus.ABORTED


@dataclass
class LockState:
    """单个锁对象上的持有与等待关系。

    holder: 当前持有该锁的事务 id；没有持有者时为 None。
    waiters: 按到达顺序排队等待该锁的事务 id 列表。
    每个 waiter 对 holder 产生一条等待边 waiter -> holder。
    """

    lock_id: str
    holder: Optional[str] = None
    waiters: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data):
        # type: (dict) -> "LockState"
        return cls(
            lock_id=str(data["lock_id"]),
            holder=data.get("holder"),
            waiters=[str(w) for w in data.get("waiters", [])],
        )


@dataclass(frozen=True)
class WaitEdge:
    """一条有向等待边：waiter 等待 holder 释放 lock_id。"""

    waiter: str
    holder: str
    lock_id: str
