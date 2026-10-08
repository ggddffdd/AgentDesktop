# -*- coding: utf-8 -*-
"""
v4.231 A 断点行为级判据：账本把"调过"当"做成"。

核心 bug 复现：用户点名工具 → 模型调用了但失败 → 进入 failed_tools，
但 pending() 完全不读 failed_tools → 账本误判"已完成" → 静默收尾、不 nudge、不重试。

修复后：
- A1 pending() 必须包含失败的"点名工具"（改前为空 → 红灯）
- A2 该失败项文案必须点名工具且含"失败"语义
- A3 should_nudge 在失败 + 有步数余量 + 未注入过 时返回 True（能触发补做一轮）
- A4 失败后若同工具成功调用 → pending() 不再报该工具（修复不误伤"先败后成"）
- A5 续跑（reset_nudge 语义：already_injected=False）后，失败工具应能重新 nudge（钉 C：修 A 即修 C）
- A6 自主调用（非点名）工具失败时不应污染点名账本（避免对非点名失败误 nudge）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from task_state import TaskState  # noqa: E402


class TestTaskStateFailedPending(unittest.TestCase):
    def test_A1_pending_includes_failed_required_tool(self):
        ts = TaskState(goal="做视频", required_tools=["video_gen"])
        ts.record_tool("video_gen", args={}, ok=False)
        pending = ts.pending()
        self.assertTrue(pending, "失败的点名工具必须出现在 pending()（改前为空 = 假完成 bug）")

    def test_A2_pending_names_failure(self):
        ts = TaskState(goal="做视频", required_tools=["video_gen"])
        ts.record_tool("video_gen", args={}, ok=False)
        text = " ".join(ts.pending())
        self.assertIn("video_gen", text)
        self.assertIn("失败", text)

    def test_A3_should_nudge_triggers_on_failure(self):
        ts = TaskState(goal="做视频", required_tools=["video_gen"])
        ts.record_tool("video_gen", args={}, ok=False)
        # 有步数余量、未注入过 → 应触发补做
        self.assertTrue(ts.should_nudge(step=3, max_steps=10, already_injected=False),
                        "失败的点名工具应触发 nudge 补做一轮")

    def test_A4_success_after_failure_clears_pending(self):
        ts = TaskState(goal="做视频", required_tools=["video_gen"])
        ts.record_tool("video_gen", args={}, ok=False)
        self.assertTrue(ts.pending(), "先败：pending 应非空")
        ts.record_tool("video_gen", args={}, ok=True)
        self.assertFalse(ts.pending(),
                         "先败后成：pending 应清空，不该把已成功的工具当失败")

    def test_A5_resume_can_re_nudge_failed(self):
        ts = TaskState(goal="做视频", required_tools=["video_gen"])
        ts.record_tool("video_gen", args={}, ok=False)
        # 主轮已注入过 nudge（already_injected=True）→ 不再补做
        self.assertFalse(ts.should_nudge(step=3, max_steps=10, already_injected=True))
        # 续跑 reset_nudge 语义：already_injected=False → 应重新能 nudge（钉 C）
        self.assertTrue(ts.should_nudge(step=4, max_steps=10, already_injected=False),
                        "续跑轮重置 nudge 闸后，失败工具应能重新补做")

    def test_A6_non_required_failure_not_pollutes(self):
        ts = TaskState(goal="做视频", required_tools=["video_gen"])
        # 模型自主调了 web_search 且失败（非用户点名）
        ts.record_tool("web_search", args={}, ok=False)
        # 点名工具 video_gen 没调过 → 仍应报 missing（A 只补 failed 维度，不吞 missing）
        pending = ts.pending()
        joined = " ".join(pending)
        self.assertIn("video_gen", joined, "video_gen 没调过应仍报缺失")
        # web_search 是自主失败，不应在 pending 里（避免对非点名失败误 nudge）
        self.assertNotIn("web_search", joined)

    def test_A7_incomplete_then_all_done(self):
        # 正常路径：点名工具成功 + 产物真实落地 → pending 空
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        try:
            ts = TaskState(goal="写报告", required_tools=["write_file"])
            ts.record_tool("write_file", args={"path": path}, ok=True)
            self.assertFalse(ts.pending(), "成功调用点名工具且产物存在后 pending 应为空")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


if __name__ == "__main__":
    import sys as _s
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = res.testsRun - len(res.failures) - len(res.errors)
    print("FAILPENDING_PASS=%d FAIL=%d" % (passed, len(res.failures) + len(res.errors)))
    _s.exit(0 if res.wasSuccessful() else 1)
