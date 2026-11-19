import unittest

from deadlock_analyzer import (
    DeadlockAnalyzer,
    LockState,
    Transaction,
    TransactionStatus,
    build_wait_graph,
    analyze,
)


def make_txn(txn_id, age=0.0, work=0.0, retries=0, priority=5,
             status=TransactionStatus.ACTIVE):
    return Transaction(
        txn_id=txn_id,
        age_seconds=age,
        work_done=work,
        retries=retries,
        priority=priority,
        status=status,
    )


class SingleCycleTest(unittest.TestCase):
    def test_single_three_node_cycle_picks_cheapest(self):
        # T1 -> T2 -> T3 -> T1：等待 L1/L2/L3。
        transactions = [
            make_txn("T1", age=100, work=900, priority=9),
            make_txn("T2", age=20, work=100, priority=2),
            make_txn("T3", age=60, work=500, priority=5),
        ]
        locks = [
            LockState("L1", holder="T2", waiters=["T1"]),
            LockState("L2", holder="T3", waiters=["T2"]),
            LockState("L3", holder="T1", waiters=["T3"]),
        ]

        report = analyze(transactions, locks)

        self.assertEqual(len(report.initial_cycles), 1)
        cycle = report.initial_cycles[0]
        self.assertEqual(cycle.transactions, ["T1", "T2", "T3"])
        self.assertFalse(cycle.is_self_wait)
        self.assertTrue(cycle.auto_resolvable)
        self.assertEqual(
            {(edge["waiter"], edge["holder"]) for edge in cycle.edges},
            {("T1", "T2"), ("T2", "T3"), ("T3", "T1")},
        )
        self.assertEqual(report.victim_order, ["T2"])
        self.assertEqual(len(report.victim_steps), 1)
        step = report.victim_steps[0]
        self.assertEqual(step.txn_id, "T2")
        self.assertEqual(step.cycles_broken, [cycle.cycle_id])
        self.assertEqual(step.remaining_cycles, [])
        self.assertEqual(report.remaining_cycles, [])
        self.assertEqual(report.unresolved_cycles, [])
        self.assertTrue(report.auto_resolved)


class CrossingCyclesTest(unittest.TestCase):
    def test_two_cycles_sharing_transaction_resolved_together(self):
        # 环1: T1 -> T2 -> T1；环2: T2 -> T3 -> T4 -> T2，共享 T2。
        transactions = [
            make_txn("T1", age=10, work=10),
            make_txn("T2", age=80, work=800, priority=9),
            make_txn("T3", age=10, work=10),
            make_txn("T4", age=10, work=10),
        ]
        locks = [
            LockState("L1", holder="T2", waiters=["T1"]),
            LockState("L2", holder="T1", waiters=["T2"]),
            LockState("L3", holder="T3", waiters=["T2"]),
            LockState("L4", holder="T4", waiters=["T3"]),
            LockState("L5", holder="T2", waiters=["T4"]),
        ]

        report = analyze(transactions, locks)

        cycle_ids = {cycle.cycle_id for cycle in report.initial_cycles}
        self.assertEqual(len(cycle_ids), 2)
        self.assertIn("C-T1-T2", cycle_ids)
        self.assertIn("C-T2-T3-T4", cycle_ids)

        # T1、T3、T4 成本相同；T1 位于环1，T3/T4 位于环2，
        # T2 同时在两个环但回滚成本最高，不应被选。
        self.assertNotIn("T2", report.victim_order)
        self.assertGreaterEqual(len(report.victim_order), 1)

        # 逐轮重算后所有环都被消解。
        self.assertEqual(report.remaining_cycles, [])
        self.assertTrue(report.auto_resolved)
        # 第一步的证据必须同时列出当时存在的两个环。
        self.assertEqual(
            sorted(report.victim_steps[0].cycles_before),
            sorted(cycle_ids),
        )

    def test_coverage_tie_break_prefers_transaction_breaking_more_cycles(self):
        # 三个事务同构（成本一致），T2 同时属于两个环，应优先牺牲 T2。
        transactions = [
            make_txn("T1", age=10, work=10),
            make_txn("T2", age=10, work=10),
            make_txn("T3", age=10, work=10),
            make_txn("T4", age=10, work=10),
        ]
        locks = [
            LockState("L1", holder="T2", waiters=["T1"]),
            LockState("L2", holder="T1", waiters=["T2"]),
            LockState("L3", holder="T3", waiters=["T2"]),
            LockState("L4", holder="T2", waiters=["T3"]),
            LockState("L5", holder="T4", waiters=["T2"]),
            LockState("L6", holder="T2", waiters=["T4"]),
        ]
        report = analyze(transactions, locks)
        self.assertEqual(report.victim_order[0], "T2")
        # T2 同时出现在两个（枚举后含复合环则更多）环中，覆盖数严格高于 T1。
        self.assertGreaterEqual(
            len(report.victim_steps[0].cycles_broken), 2
        )
        self.assertTrue(report.auto_resolved)


class SharedTransactionTest(unittest.TestCase):
    def test_no_duplicate_victim_across_overlapping_cycles(self):
        # 三个两两成环的事务：T1-T2、T2-T3、T1-T3 共三个环。
        transactions = [
            make_txn("T1", age=5, work=5),
            make_txn("T2", age=5, work=5),
            make_txn("T3", age=500, work=5000, priority=9),
        ]
        locks = [
            LockState("L1", holder="T2", waiters=["T1"]),
            LockState("L2", holder="T1", waiters=["T2"]),
            LockState("L3", holder="T3", waiters=["T2"]),
            LockState("L4", holder="T2", waiters=["T3"]),
            LockState("L5", holder="T3", waiters=["T1"]),
            LockState("L6", holder="T1", waiters=["T3"]),
        ]
        report = analyze(transactions, locks)

        # 三个节点两两成环，枚举全部基础环（含经过三个节点的复合环）。
        self.assertGreaterEqual(len(report.initial_cycles), 3)
        pair_cycles = {
            frozenset(cycle.transactions)
            for cycle in report.initial_cycles
            if len(cycle.transactions) == 2
        }
        self.assertEqual(
            pair_cycles,
            {frozenset(("T1", "T2")),
             frozenset(("T2", "T3")),
             frozenset(("T1", "T3"))},
        )
        self.assertEqual(len(report.victim_order), len(set(report.victim_order)))
        # 高成本的 T3 不应先被牺牲。
        self.assertNotEqual(report.victim_order[0], "T3")
        self.assertTrue(report.auto_resolved)
        # 每一步之后都重新计算了剩余环。
        for step in report.victim_steps:
            self.assertEqual(
                len(step.remaining_cycles),
                len(step.cycles_before) - len(step.cycles_broken),
            )


class SelfWaitTest(unittest.TestCase):
    def test_self_loop_is_detected_as_cycle(self):
        transactions = [
            make_txn("T1", age=10, work=10),
            make_txn("T2", age=10, work=10),
        ]
        locks = [
            # T1 等待自己已经持有的锁（如自引用重入场景）。
            LockState("L1", holder="T1", waiters=["T1"]),
            LockState("L2", holder="T2", waiters=["T1"]),
        ]
        report = analyze(transactions, locks)

        self.assertEqual(len(report.initial_cycles), 1)
        cycle = report.initial_cycles[0]
        self.assertTrue(cycle.is_self_wait)
        self.assertEqual(cycle.transactions, ["T1"])
        self.assertEqual(
            cycle.edges,
            [{"waiter": "T1", "holder": "T1", "locks": ["L1"]}],
        )
        self.assertEqual(report.victim_order, ["T1"])
        self.assertTrue(report.auto_resolved)


class AbortedTransactionTest(unittest.TestCase):
    def test_aborted_only_cycle_cannot_auto_resolve(self):
        transactions = [
            make_txn("T1", status=TransactionStatus.ABORTED),
            make_txn("T2", status=TransactionStatus.ABORTED),
            make_txn("T3", age=10, work=10),
        ]
        locks = [
            LockState("L1", holder="T2", waiters=["T1"]),
            LockState("L2", holder="T1", waiters=["T2"]),
            LockState("L3", holder="T3", waiters=["T2"]),
        ]
        report = analyze(transactions, locks)

        self.assertEqual(len(report.initial_cycles), 1)
        cycle = report.initial_cycles[0]
        self.assertFalse(cycle.auto_resolvable)
        self.assertEqual(cycle.aborted_members, ["T1", "T2"])
        self.assertEqual(cycle.active_members, [])
        self.assertEqual(report.victim_order, [])
        self.assertEqual(len(report.unresolved_cycles), 1)
        self.assertTrue(report.unresolved_reasons)
        self.assertFalse(report.auto_resolved)

    def test_aborted_member_is_skipped_in_favor_of_active(self):
        transactions = [
            make_txn("T1", status=TransactionStatus.ABORTED),
            make_txn("T2", age=100, work=1000),
            make_txn("T3", age=1, work=1),
        ]
        locks = [
            LockState("L1", holder="T2", waiters=["T1"]),
            LockState("L2", holder="T3", waiters=["T2"]),
            LockState("L3", holder="T1", waiters=["T3"]),
        ]
        report = analyze(transactions, locks)

        self.assertEqual(report.victim_order, ["T3"])
        self.assertTrue(report.auto_resolved)


class DuplicateEdgeTest(unittest.TestCase):
    def test_duplicate_wait_edges_are_deduplicated_and_reported(self):
        transactions = [
            make_txn("T1", age=10, work=10),
            make_txn("T2", age=100, work=100),
        ]
        locks = [
            LockState("L1", holder="T2", waiters=["T1"]),
            # 同一对 waiter/holder 在同一把锁上的重复登记。
            LockState("L1", holder="T2", waiters=["T1"]),
            # 不同锁产生的等待合并为一条边，但保留两把锁的证据。
            LockState("L2", holder="T2", waiters=["T1"]),
            LockState("L3", holder="T1", waiters=["T2"]),
        ]
        report = analyze(transactions, locks)

        self.assertEqual(len(report.initial_cycles), 1)
        edge = report.initial_cycles[0].edges
        t1_waits = [item for item in edge if item["waiter"] == "T1"][0]
        self.assertEqual(t1_waits["locks"], ["L1", "L2"])
        self.assertEqual(
            report.duplicate_edges,
            [{"waiter": "T1", "holder": "T2", "lock_id": "L1"}],
        )
        self.assertEqual(report.victim_order, ["T1"])


class StarvationProtectionTest(unittest.TestCase):
    def test_repeated_victim_is_protected_by_retry_count(self):
        # 两个事务成本接近，但 T1 已连续被牺牲 3 次，
        # 即使优先级略低，也应改由 T2 成为本次牺牲者。
        transactions = [
            make_txn("T1", age=10, work=10, retries=3, priority=3),
            make_txn("T2", age=12, work=12, retries=0, priority=5),
        ]
        locks = [
            LockState("L1", holder="T2", waiters=["T1"]),
            LockState("L2", holder="T1", waiters=["T2"]),
        ]
        report = analyze(transactions, locks)

        self.assertEqual(report.victim_order, ["T2"])
        analyzer = DeadlockAnalyzer()
        graph = build_wait_graph(transactions, locks)
        txn_map = {txn.txn_id: txn for txn in transactions}
        t1_score, t1_breakdown = analyzer._score("T1", graph, txn_map)
        t2_score, _ = analyzer._score("T2", graph, txn_map)
        self.assertGreater(t1_breakdown["retry_factor"], 1.0)
        self.assertGreater(t1_score, t2_score)


class NoDeadlockTest(unittest.TestCase):
    def test_acyclic_wait_graph_needs_no_victim(self):
        transactions = [
            make_txn("T1", age=10, work=10),
            make_txn("T2", age=10, work=10),
            make_txn("T3", age=10, work=10),
        ]
        locks = [
            LockState("L1", holder="T2", waiters=["T1"]),
            LockState("L2", holder="T3", waiters=["T2"]),
        ]
        report = DeadlockAnalyzer().analyze(transactions, locks)
        self.assertEqual(report.initial_cycles, [])
        self.assertEqual(report.victim_order, [])
        self.assertTrue(report.auto_resolved)


if __name__ == "__main__":
    unittest.main()
