import sys
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .model import LockState, Transaction, WaitEdge

# 图分析使用递归 DFS，默认递归深度对大事务集不够用，这里放宽限制。
sys.setrecursionlimit(max(100000, sys.getrecursionlimit()))


class WaitGraph:
    """锁等待有向图。

    节点为事务 id，边 waiter -> holder 表示 waiter 正在等待 holder
    持有的某个锁。同一对事务在同一个锁上的重复边只保留一次。
    """

    def __init__(self):
        self.nodes = set()  # type: Set[str]
        self.adj = {}  # type: Dict[str, Set[str]]
        self.edge_locks = {}  # type: Dict[Tuple[str, str], List[str]]
        self.duplicate_edges = []  # type: List[WaitEdge]

    def add_edge(self, edge):
        # type: (WaitEdge) -> None
        self.nodes.add(edge.waiter)
        self.nodes.add(edge.holder)
        self.adj.setdefault(edge.waiter, set())
        self.adj.setdefault(edge.holder, set())
        key = (edge.waiter, edge.holder)
        if key not in self.edge_locks:
            self.edge_locks[key] = [edge.lock_id]
            self.adj[edge.waiter].add(edge.holder)
            return
        locks = self.edge_locks[key]
        if edge.lock_id in locks:
            self.duplicate_edges.append(edge)
        else:
            # 同一对事务因不同锁产生的等待仍然合并为一条边，
            # 但保留全部锁 id 作为证据。
            locks.append(edge.lock_id)

    def remove_transaction(self, txn_id):
        # type: (str) -> None
        """从事务集合中移除一个牺牲者及其全部出入边。"""
        self.nodes.discard(txn_id)
        self.adj.pop(txn_id, None)
        for neighbors in self.adj.values():
            neighbors.discard(txn_id)
        self.edge_locks = {
            key: locks
            for key, locks in self.edge_locks.items()
            if txn_id not in key
        }

    def edge_locks_for(self, waiter, holder):
        # type: (str, str) -> List[str]
        return list(self.edge_locks.get((waiter, holder), []))

    def _tarjan_subset(self, allowed):
        # type: (Optional[Set[str]]) -> List[List[str]]
        """Tarjan 强连通分量算法，只统计 allowed 内的节点。"""
        index_of = {}  # type: Dict[str, int]
        lowlink = {}  # type: Dict[str, int]
        on_stack = set()  # type: Set[str]
        stack = []  # type: List[str]
        components = []  # type: List[List[str]]
        counter = [0]

        def strongconnect(node):
            index_of[node] = counter[0]
            lowlink[node] = counter[0]
            counter[0] += 1
            stack.append(node)
            on_stack.add(node)
            for neighbor in self.adj.get(node, set()):
                if allowed is not None and neighbor not in allowed:
                    continue
                if neighbor not in index_of:
                    strongconnect(neighbor)
                    lowlink[node] = min(lowlink[node], lowlink[neighbor])
                elif neighbor in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[neighbor])
            if lowlink[node] == index_of[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)

        seeds = self.nodes if allowed is None else self.nodes & allowed
        for node in sorted(seeds):
            if node not in index_of:
                strongconnect(node)
        return components

    def strongly_connected_components(self):
        # type: () -> List[List[str]]
        return self._tarjan_subset(None)

    def cyclic_components(self):
        # type: () -> List[Set[str]]
        """返回包含等待环的强连通分量（含自等待的单节点分量）。"""
        result = []  # type: List[Set[str]]
        for component in self.strongly_connected_components():
            members = set(component)
            if len(members) > 1:
                result.append(members)
                continue
            node = component[0]
            if node in self.adj.get(node, set()):
                result.append(members)
        return result

    def elementary_cycles(self):
        # type: () -> List[List[str]]
        """Johnson 算法枚举全部基础环（简单环），结果按节点序列排序。

        自等待（自环）返回单节点环 [txn_id]。
        """
        cycles = []  # type: List[List[str]]
        remaining = set(self.nodes)

        while remaining:
            sub_components = self._tarjan_subset(remaining)
            # 选择包含最小编号节点的、至少含一条环的强连通分量。
            candidate = None  # type: Optional[Set[str]]
            for component in sub_components:
                members = set(component)
                has_cycle = len(members) > 1 or any(
                    node in self.adj.get(node, set()) for node in members
                )
                if not has_cycle:
                    continue
                least = min(members)
                if candidate is None or least < min(candidate):
                    candidate = members
            if candidate is None:
                break

            start = min(candidate)
            blocked = {start}  # type: Set[str]
            blocked_map = {node: set() for node in candidate}  # type: Dict[str, Set[str]]

            def unblock(node):
                blocked.discard(node)
                while blocked_map[node]:
                    dependent = blocked_map[node].pop()
                    if dependent in blocked:
                        unblock(dependent)

            def circuit(node, path):
                found = False
                path.append(node)
                blocked.add(node)
                for neighbor in sorted(self.adj.get(node, set())):
                    if neighbor not in candidate:
                        continue
                    if neighbor == start:
                        cycles.append(list(path))
                        found = True
                    elif neighbor not in blocked:
                        if circuit(neighbor, path):
                            found = True
                if found:
                    unblock(node)
                else:
                    for neighbor in self.adj.get(node, set()):
                        if neighbor in candidate:
                            blocked_map[neighbor].add(node)
                path.pop()
                return found

            circuit(start, [])
            remaining.discard(start)

        return sorted(
            (self._rotate_cycle(cycle) for cycle in cycles),
            key=lambda cycle: (len(cycle), cycle),
        )

    @staticmethod
    def _rotate_cycle(cycle):
        # type: (List[str]) -> List[str]
        """把环旋转到最小节点开头，保证同一环有唯一、稳定的表示。"""
        if not cycle:
            return cycle
        start = min(range(len(cycle)), key=lambda idx: cycle[idx])
        return cycle[start:] + cycle[:start]


def build_wait_graph(transactions, lock_states):
    # type: (Iterable[Transaction], Iterable[LockState]) -> WaitGraph
    """从事务表和锁状态构造等待图，并校验引用完整性。"""
    known_ids = {txn.txn_id for txn in transactions}
    graph = WaitGraph()
    for txn_id in sorted(known_ids):
        graph.nodes.add(txn_id)
        graph.adj.setdefault(txn_id, set())

    for lock in lock_states:
        if lock.holder is not None and lock.holder not in known_ids:
            raise ValueError(
                "锁 %s 的持有者 %s 不在事务列表中" % (lock.lock_id, lock.holder)
            )
        for waiter in lock.waiters:
            if waiter not in known_ids:
                raise ValueError(
                    "锁 %s 的等待者 %s 不在事务列表中" % (lock.lock_id, waiter)
                )
            if lock.holder is None:
                # 无主锁不存在等待关系，跳过而不是报错。
                continue
            graph.add_edge(WaitEdge(waiter, lock.holder, lock.lock_id))
    return graph
