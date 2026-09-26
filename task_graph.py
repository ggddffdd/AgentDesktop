# -*- coding: utf-8 -*-
"""小臭玩AI — 任务图引擎 v4.60

用 WorkBuddy 任务编排同款模式：
  TaskCreate(subject, description) → 新建节点
  addBlockedBy(task_id)             → 定义依赖边
  status: pending→in_progress→completed / failed / cancelled / incomplete → 状态流转
  TaskList()                        → 全局进度

Agent 拿到复杂任务后，先拆成 TaskGraph，再逐个推进（自动完成依赖就绪的任务）。
"""

from typing import Any, Callable, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

# v4.167.0：统一取消令牌（纯标准库，无 Qt 依赖）
from cancel_token import CancelledError

# v4.168.0（审查 #2 残余）：执行器的返回字典里带这个保留键 = 「跑完了，但没有产出」。
#
# 为什么要有独立的 incomplete 态：`AgentNode` 跑满 max_turns 却始终只有工具调用、
# 没有正文时，旧实现会把一句可读说明当 output 正常返回 —— TaskGraph 见「正常返回」
# 就标 completed，于是「只跑了工具、什么都没写」被记成「成员已完成」，
# PM 与最终报告都以为真交了东西。现在这条线有了自己的终态，
# 既不冒充成功（completed），也不冤枉成崩溃（failed）。
INCOMPLETE_FLAG = "__incomplete__"
# 保留键（不写进 state，避免污染下游上下文）
_RESERVED_KEYS = (INCOMPLETE_FLAG,)


class Task:
    """一个可执行节点：有 subject、有 executor、有依赖、有状态。"""
    def __init__(self, task_id: str, subject: str, executor: Callable, description: str = ""):
        self.id = task_id
        self.subject = subject
        self.executor = executor  # (state: dict) -> dict
        self.description = description
        self.blocked_by: List[str] = []  # 依赖的任务 ID 列表
        self.blocks: List[str] = []      # 被本任务阻塞的任务 ID 列表
        # v4.168.0：状态机补 incomplete —— 终态集合＝
        #   completed（正常交活）/ failed（执行异常）/ cancelled（被叫停）
        #   / incomplete（跑完但没有正文产出）
        self.status = "pending"          # pending | in_progress | completed
        #                                  | failed | cancelled | incomplete
        self.result = None               # 执行结果

    def is_ready(self, task_map: Dict[str, "Task"]) -> bool:
        """所有依赖都 completed 才算就绪。"""
        if self.status != "pending":
            return False
        return all(task_map[bid].status == "completed" for bid in self.blocked_by if bid in task_map)

    def to_dict(self):
        return {
            "id": self.id, "subject": self.subject,
            "status": self.status, "blockedBy": self.blocked_by,
        }


class TaskGraph:
    """任务图引擎：创建 → 编排依赖 → 自动推进。

    用法：
        tg = TaskGraph()
        # 定义任务
        tg.create("search", "搜索资料", research_node)
        tg.create("analyze", "分析结果", analyze_node)
        tg.create("write", "写报告", write_node)
        # 定义依赖：analyze 依赖 search, write 依赖 analyze
        tg.depend("analyze", "search")
        tg.depend("write", "analyze")
        # 执行
        result = tg.run({"query": "AI趋势"})
    """

    def __init__(self):
        self._tasks: Dict[str, Task] = {}
        self._entry_ids: List[str] = []   # 无依赖的入口任务
        self._lock = threading.Lock()

    # ---- 任务编排 API（对应用户熟悉的 TaskCreate / addBlockedBy）----

    def create(self, task_id: str, subject: str, executor: Callable, description: str = ""):
        """TaskCreate = 注册一个执行节点。"""
        task = Task(task_id, subject, executor, description)
        self._tasks[task_id] = task
        self._entry_ids.append(task_id)  # 初设入口，后面 depend() 会移除有依赖的
        return self

    def depend(self, task_id: str, blocked_by_id: str):
        """addBlockedBy = 定义依赖。task_id 依赖 blocked_by_id 先完成。"""
        t = self._tasks.get(task_id)
        dep = self._tasks.get(blocked_by_id)
        if not t or not dep:
            raise ValueError(f"任务不存在: {task_id} 或 {blocked_by_id}")
        t.blocked_by.append(blocked_by_id)
        dep.blocks.append(task_id)
        # 有依赖的任务不再是入口
        if task_id in self._entry_ids:
            self._entry_ids.remove(task_id)
        return self

    def task_list(self) -> List[dict]:
        """TaskList = 查看全局进度。"""
        return [t.to_dict() for t in self._tasks.values()]

    # ---- 自动执行引擎 ----

    # ---------- v4.167.0：取消支持（审查 §3） ----------

    @staticmethod
    def _cancel_state_of(stage: str) -> str:
        """把「取消发生时所处的阶段」归类成大哥能看懂的终态标签。

        注意判断顺序：必须先认 tool 再认 model —— `"tool_call"` 里也含
        `"call"`，若先判 `"call"` 会把工具阶段的取消误标成模型阶段
        （实测踩过：tool_call 被归成 cancelled_during_model_call）。
        """
        s = (stage or "").lower()
        if not s or s in ("before_start", "submit", "dispatch"):
            return "cancelled_before_start"
        if "tool" in s:
            return "cancelled_after_tool_call"
        if "model" in s or "call" in s:
            return "cancelled_during_model_call"
        return f"cancelled_mid_stage:{stage}"

    def _mark_pending_cancelled(self, token):
        """把还没开始的节点标成取消（区分「未开始」与「跑到一半被停」）。

        语义很重要：这不是失败 —— 是"因为大哥喊停所以没跑"。
        任务板/报告据此能回答"哪些成员真干了、哪些没有"。
        """
        n = 0
        reason = token.reason if token is not None else ""
        for task in self._tasks.values():
            if task.status == "pending":
                task.status = "cancelled"
                task.result = {"cancelled": True,
                               "cancel_state": "cancelled_before_start",
                               "reason": reason}
                n += 1
        return n

    def run(self, state: Dict[str, Any], token=None) -> Dict[str, Any]:
        """自动推进：找到就绪的任务 → 执行 → 标记完成 → 循环直到全部完成。

        token（v4.167.0）：统一取消令牌。检查点有三处，缺一就会出现
        「明明点了停止，还继续扣费」：
          1. 每轮派发前 —— 已取消则**不再 submit 任何成员**
          2. 单个成员 submit 前 —— 已取消则直接标 cancelled（不进线程池队列）
          3. executor 开头 —— 进了线程池但还没跑的，立刻抛 CancelledError

        终态区分：取消发生在哪个阶段由令牌的 stage 记录归类，
        写进 `task.result["cancel_state"]`（cancelled_before_start /
        cancelled_during_model_call / cancelled_after_tool_call / ...），
        这样"谁真干了、谁被停了"一目了然。

        v4.168.0（审查 #2 残余）：执行器若在返回字典里带 `INCOMPLETE_FLAG`，
        该节点标 `incomplete`（跑完了但没产出）—— 既不冒充 completed，
        也不冤枉成 failed。下游拿到的 state 里会带 `incomplete: True`。
        """
        state = dict(state)
        # 无任务的空图直接返回
        if not self._tasks:
            return state

        # 确保至少有入口
        if not self._entry_ids:
            raise ValueError("任务图没有入口节点（所有任务都有依赖，存在循环依赖？）")

        while True:
            # ---- 检查点 1：派发前。用户已喊停 → 一个成员都不再发起 ----
            if token is not None and token.is_cancelled:
                self._mark_pending_cancelled(token)
                state["__cancelled__"] = {"reason": token.reason, "stage": token.stage}
                break

            # 找就绪任务（依赖全部完成 + 状态 pending）
            ready = [tid for tid in self._tasks
                     if self._tasks[tid].is_ready(self._tasks)]

            if not ready:
                # 检查是否全部完成（被取消 / 无产出的都算"已终态"，不再等待）
                all_done = all(t.status in ("completed", "cancelled", "incomplete")
                               for t in self._tasks.values())
                any_failed = any(t.status in ("failed", "incomplete")
                                 for t in self._tasks.values())
                if all_done:
                    break
                if any_failed:
                    # 有失败/无产出的不阻塞全局，跳过它们继续（但绝不当作成功）
                    break
                # 没有就绪但有未完成的 → 可能有循环依赖
                pending = [t for t in self._tasks.values() if t.status == "pending"]
                if not pending:
                    break
                raise RuntimeError(
                    f"任务图死锁：{len(pending)} 个任务等待中，但无就绪任务。"
                    f" 可能循环依赖: {[p.id for p in pending]}"
                )

            # 并行执行所有就绪任务
            results = {}
            with ThreadPoolExecutor(max_workers=min(len(ready), 5)) as pool:
                futures = {}
                for tid in ready:
                    t = self._tasks[tid]
                    # ---- 检查点 2：submit 前。已取消 → 不进线程池队列 ----
                    if token is not None and token.is_cancelled:
                        t.status = "cancelled"
                        t.result = {"cancelled": True,
                                    "cancel_state": "cancelled_before_start",
                                    "reason": token.reason}
                        results[tid] = t.result
                        continue
                    t.status = "in_progress"
                    # ---- 检查点 3：executor 开头（见 _guarded_exec）----
                    futures[pool.submit(self._guarded_exec(t.executor, token),
                                        dict(state))] = tid

                for f in as_completed(futures):
                    tid = futures[f]
                    t = self._tasks[tid]
                    try:
                        r = f.result()
                        # v4.168.0（审查 #2 残余）：执行器自报「跑完但无产出」→
                        # 标 incomplete，不得冒充 completed。
                        if isinstance(r, dict) and r.get(INCOMPLETE_FLAG):
                            t.status = "incomplete"
                            t.result = {k: v for k, v in r.items()
                                        if k not in _RESERVED_KEYS}
                            t.result["incomplete"] = True
                            results[tid] = t.result
                            continue
                        t.status = "completed"
                        t.result = r
                        results[tid] = r
                    except CancelledError as ce:
                        # 已开始但被取消：不是失败，是"被叫停"
                        info = {
                            "cancelled": True,
                            "cancel_state": self._cancel_state_of(ce.stage),
                            "stage": ce.stage,
                            "reason": ce.reason,
                        }
                        t.status = "cancelled"
                        t.result = info
                        results[tid] = info
                    except Exception as e:
                        t.status = "failed"
                        t.result = {"error": str(e)}
                        results[tid] = {"error": str(e)}

            # 合并结果到 state
            for tid, r in results.items():
                if isinstance(r, dict):
                    state[tid] = r
                    # 传播节点更新的共享上下文，使下游节点能看到前序产出（否则串行流水线会断链）
                    if r.get("context"):
                        state["context"] = r["context"]

        return state

    @staticmethod
    def _guarded_exec(executor, token):
        """给 executor 包一层：进线程池后、真正开跑前先查一次令牌。

        为什么必须在这一层查：已经 submit 进线程池的成员，若用户此刻点停止，
        它们仍会被线程池逐个取出执行 —— 这一查就是拦住它们的那道门。
        """
        def _fn(state):
            if token is not None:
                token.raise_if_cancelled("before_start")
            return executor(state)
        return _fn