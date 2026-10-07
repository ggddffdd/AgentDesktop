# -*- coding: utf-8 -*-
"""agent_loop_mixin.py —— v4.226 四阶段循环接线（从 agent.py 抽出）

为什么单独一个文件
------------------
与 v4.225 的 `agent_task_mixin.py` 同理：v4.216 立的拆分红线
（`tests/test_split_216.py` 的 `D2 agent.py < 2400 行`）作用是**防慢慢回弹**，
阈值不能抬。所以四阶段循环的**接线**搬出 agent.py，主类只留一行调用点。

分工（刻意不重叠）
------------------
    agent_loop.py        阶段常量 / LoopState / 五个门的判定（纯逻辑，可单测）
    agent_loop_mixin.py  取状态 → 调判定 → 执行副作用（注入消息、发信号）
    agent.py             一行调用点

**判定逻辑一律不在本文件实现** —— 两处判据漂移是本项目明确吃过的坑
（见 MEMORY.md 二·3）。这里只有「拿到结果后做什么」。

零行为变化保证
--------------
`should_plan` 只在「硬要求 ≥ 2 项 + 第一步」时为真（单硬要求 / 纯咨询一律
False）；`should_verify` 只固化记录、**不注入**补做指令（那唯一真源仍是
task_state.should_nudge）；`should_summarize` 要求「有硬要求 + 有事实 +
非空文本」三条件。任一为假 → 完全走 v4.225 的旧路径。

所有方法 fail-open：异常 → 返回「不介入」，主循环照旧。
"""
import logging

log = logging.getLogger("dsdesktop")

VERSION = "v4.229.0"


class AgentLoopMixin(object):
    """AgentWorker 的四阶段循环接线（无 self 状态假设之外的东西）。

    被 mixin 的类需能拿到：`_tstate`（v4.225 账本）、`messages`、
    `_emit_status`、`_force_next`。
    """

    # --------------------------------------------------------
    # 1) 循环启动：建 LoopState + 判定要不要注入计划
    # --------------------------------------------------------
    def _loop_start(self, agent_loop):
        """run() 开头调一次：建阶段状态 + （若门槛满足）注入计划清单。

        返回 True = 已注入计划（调用方据此把阶段推进到 EXECUTE）。
        """
        try:
            ts = getattr(self, "_tstate", None)
            goal = ts.goal if ts is not None else ""
            self._loop = agent_loop.LoopState(goal)
            if not agent_loop.should_plan(
                    ts.required_tools if ts is not None else ()):
                self._loop.enter(agent_loop.PHASE_EXECUTE, 0)
                return False
            plan = agent_loop.build_plan(
                goal, ts.required_tools if ts is not None else ())
            instr = agent_loop.plan_instruction(plan)
            if not instr:
                self._loop.enter(agent_loop.PHASE_EXECUTE, 0)
                return False
            self._loop.plan = list(plan or [])
            self._loop.plan_injected = True
            self._loop.enter(agent_loop.PHASE_EXECUTE, 1)
            self.messages.append({"role": "system", "content": instr,
                                  "_internal": True})
            self._emit_status("📋 已按用户点名的要求建立任务清单")
            log.info("四阶段循环：注入计划 %d 项", len(self._loop.plan))
            return True
        except Exception as e:
            log.warning("四阶段循环初始化失败（已忽略，退化为不介入）: %s", e)
            self._loop = None
            return False

    # --------------------------------------------------------
    # 2) 每步：同步步号（阶段本身不随步号推进，只记步）
    # --------------------------------------------------------
    def _loop_step(self, step):
        """每步同步步号到 LoopState（不推进阶段）。"""
        try:
            _lp = getattr(self, "_loop", None)
            if _lp is not None:
                _lp.step = int(step)
        except Exception:
            pass

    # --------------------------------------------------------
    # 3) 工具执行后：进 VERIFY 并固化核验记录
    # --------------------------------------------------------
    def _loop_verify(self, agent_loop):
        """工具批次执行完调：把账本结论固化成核验记录。

        **不注入任何指令** —— 「要不要补做一轮」的唯一真源是
        `task_state.should_nudge`（v4.225 收尾闸门）。本方法只标注阶段 +
        存记录，供总结阶段引用与日志归因。
        """
        try:
            _lp = getattr(self, "_loop", None)
            if _lp is None:
                return False
            ts = getattr(self, "_tstate", None)
            if not agent_loop.should_verify(ts, plan_injected=_lp.plan_injected):
                return False
            _lp.verification = agent_loop.verify_report(ts)
            _lp.verified = agent_loop.is_verified(ts)
            _lp.enter(agent_loop.PHASE_VERIFY, _lp.step)
            log.info("四阶段循环：核验 %s", _lp.summary())
            return True
        except Exception as e:
            log.warning("四阶段循环核验失败（已忽略）: %s", e)
            return False

    # --------------------------------------------------------
    # 4) 收尾：追加机器小结
    # --------------------------------------------------------
    def _loop_summary(self, agent_loop):
        """模型给纯文本结论后调：追加机器生成的执行小结。

        返回 True = 确实追加了（调用方可据此打日志）；False = 不介入。
        """
        try:
            _lp = getattr(self, "_loop", None)
            if _lp is None:
                return False
            ts = getattr(self, "_tstate", None)
            if not agent_loop.should_summarize(ts, emitted=_lp.summary_emitted):
                return False
            text = agent_loop.build_summary(ts)
            if not text:
                return False
            _lp.enter(agent_loop.PHASE_SUMMARIZE, _lp.step)
            _lp.summary_emitted = True
            self.stream_commit.emit("\n\n" + text)
            log.info("四阶段循环：已追加机器小结（阶段=%s）", _lp.phase)
            return True
        except Exception as e:
            log.warning("四阶段循环小结失败（已忽略）: %s", e)
            return False

    def _loop_state_dict(self):
        """LoopState 的可序列化快照（轨迹/日志用）；无状态返回 {}。"""
        try:
            _lp = getattr(self, "_loop", None)
            return _lp.to_dict() if _lp is not None else {}
        except Exception:
            return {}


__all__ = ["VERSION", "AgentLoopMixin"]