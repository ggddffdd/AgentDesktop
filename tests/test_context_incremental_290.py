# -*- coding: utf-8 -*-
"""v4.289.0 回归：上下文增量摘要（有界合并，替代无界碎片）。

验证：
- 长会话多次自动压缩 → summaries 保持有界（仅 1 条合并摘要），不再无界累积碎片。
- 相同内容的 key_points 去重。
- 合并摘要的 period 刷新为「最早 ~ 最晚」、message_count 累计。
- get_compressed_context 仍回读 summaries 列表（契约不变）。
- compress_with_llm 显式快照另起一段且 method 标记保留，不被降级合并。
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import context_manager as cm


class ContextIncrementalTest(unittest.TestCase):
    def _new_mgr(self, max_window=5, compress_threshold=3):
        d = tempfile.mkdtemp(prefix="ctx_incr_test_")
        sp = os.path.join(d, "summary.json")
        kp = os.path.join(d, "key_info.json")
        return cm.ContextManager(max_window=max_window,
                                 compress_threshold=compress_threshold,
                                 summary_path=sp, key_info_path=kp)

    def test_bounded_single_merged_entry(self):
        mgr = self._new_mgr()
        for i in range(30):
            mgr.add_message("user", f"第{i}条消息 待办：做任务{i}")
        # 30 条 > max_window(5)，触发多次自动压缩。应为有界：仅 1 条合并摘要
        self.assertEqual(len(mgr.summaries), 1)
        self.assertEqual(mgr.summaries[0]["method"], "heuristic-merged")
        # 累计 message_count 应覆盖被压缩掉的历史条数
        self.assertGreaterEqual(mgr.summaries[0]["message_count"], 20)

    def test_dedup_key_points(self):
        mgr = self._new_mgr()
        for _ in range(20):
            mgr.add_message("user", "待办：同一个重复任务")  # 内容完全相同
        self.assertEqual(len(mgr.summaries), 1)
        kps = mgr.summaries[0]["key_points"]
        # 同内容应去重为 1 条（不随消息数膨胀）
        self.assertEqual(len(kps), 1)
        self.assertEqual(kps[0]["content"], "待办：同一个重复任务")

    def test_period_refreshed(self):
        mgr = self._new_mgr()
        mgr.add_message("user", "a")
        first_ts = mgr.messages[0]["timestamp"]  # 首条消息时间戳（最早）
        # 触发若干次压缩后，period 头部应保持为「最早压缩到的消息」
        for i in range(15):
            mgr.add_message("user", f"待办：任务{i}")
        self.assertEqual(len(mgr.summaries), 1)
        period = mgr.summaries[0]["period"]
        self.assertIn(" ~ ", period)
        head, tail = period.split(" ~ ")
        self.assertEqual(head, first_ts)  # 头部未被后续合并重置
        self.assertTrue(tail)             # 尾部为非空时间戳（已刷新到最近一次淘汰）

    def test_get_compressed_context_contract(self):
        mgr = self._new_mgr()
        for i in range(15):
            mgr.add_message("user", f"待办：任务{i}")
        ctx = mgr.get_compressed_context()
        self.assertIsInstance(ctx["summaries"], list)
        self.assertEqual(len(ctx["summaries"]), 1)
        self.assertEqual(ctx["summaries"][0]["method"], "heuristic-merged")

    def test_llm_snapshot_separate_and_preserved(self):
        mgr = self._new_mgr()
        for i in range(12):
            mgr.add_message("user", f"待办：任务{i}")
        # 先有一合并 heuristic 段
        self.assertEqual(len(mgr.summaries), 1)
        # 手动 LLM 压缩：mock 掉网络调用，返回固定摘要
        with mock.patch.object(cm, "_call_summary_llm", return_value="LLM 摘要文本"):
            out = mgr.compress_with_llm(cfg={})
        self.assertIn("LLM 摘要文本", out)
        # LLM 快照另起一段，method 保留 "llm"
        self.assertEqual(len(mgr.summaries), 2)
        self.assertEqual(mgr.summaries[-1]["method"], "llm")
        # 之后继续对话：自动压缩并入最后一段（llm 段），不新增碎片，method 不被降级
        for i in range(10):
            mgr.add_message("user", f"后续待办：任务{i}")
        self.assertEqual(len(mgr.summaries), 2)
        self.assertEqual(mgr.summaries[-1]["method"], "llm")


def run_tests():
    suite = unittest.TestLoader().loadTestsFromTestCase(ContextIncrementalTest)
    res = unittest.TextTestRunner(verbosity=1).run(suite)
    n_pass = res.testsRun - len(res.failures) - len(res.errors)
    n_fail = len(res.failures) + len(res.errors)
    print(f"PASS={n_pass} FAIL={n_fail}")
    return n_fail


if __name__ == "__main__":
    sys.exit(1 if run_tests() else 0)
