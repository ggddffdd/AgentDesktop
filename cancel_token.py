# -*- coding: utf-8 -*-
"""小臭玩AI — 统一取消令牌（v4.167.0）

为什么需要它
------------
军团与导演台原本各用各的"停止"：
  · 军团：`LegionWorker._aborted` 全局布尔，只在**波开始前**检查 ——
    波内成员（一次并行最多 5 个，每个还要跑多轮模型调用）进去之后没人再看，
    于是"点了停止还继续扣费"。
  · 导演台：`video_pipeline.cancelled` 也是全局**永久**布尔 —— 停止之后
    这个值不会自己恢复，`regenerate_clip()` 一看到 True 就直接返回，
    用户点"停止"就只能重置整个项目、丢掉已写好的剧本分镜。

两者其实是同一个病根：**停止意图没有作用域，也没有生命周期**。
本模块把它抽象成一个可继承、可一次性广播、可带原因与阶段记录的令牌，
让"停止当前这一轮"与"下一次还能继续"同时成立。

设计要点
--------
1. **一次性、幂等**：cancel() 可以调很多次，只有第一次生效；原因与阶段保留首次值
   （避免后续调用覆盖掉"到底是谁先喊停的"）。
2. **父链继承**：`child()` 出来的子令牌，父一取消子即视为已取消（读取时向上遍历，
   不注册回调 —— 零强引用、零回调泄漏）。子取消不影响父（一波取消不该杀掉整场运行）。
3. **阶段追踪**：`mark_stage()` 记录"最后经过的阶段"。取消时若调用方没给阶段，
   就用最后记录的那个 —— 这样上层能区分
   `cancelled_before_start` / `cancelled_during_model_call` /
   `cancelled_after_tool_call`，让大哥看清哪些成员真干了、哪些没有。
4. **零 Qt 依赖、纯标准库**：可在无 GUI 环境跑测试（与 browser_bridge / task_status 一致）。
5. **不抛异常地查询**：`is_cancelled` 永不抛；`raise_if_cancelled()` 才抛，
   由调用方在"能安全中断"的位置调（不要在 finally 清理路径里调）。

注意：本模块**只负责表达取消意图与状态**，不负责杀线程。
Python 无法安全强杀线程，所有中断点都必须是协作式的（在轮次/阶段之间检查）。
"""

import threading
import time
from typing import Callable, List, Optional


class CancelledError(RuntimeError):
    """协作式中断信号。带上是哪个阶段被取消的，便于上层归类终态。"""

    def __init__(self, reason: str = "", stage: str = ""):
        self.reason = reason or "已取消"
        self.stage = stage or ""
        super().__init__(f"已取消（{self.stage or '未标注阶段'}）：{self.reason}")


class CancellationToken:
    """可继承的一次性取消令牌（线程安全）。

    典型用法：
        token = CancellationToken(name="run")
        ...
        token.raise_if_cancelled("model_call")   # 每个阶段开头检查
        ...
        token.cancel("用户点了停止", stage="wave_dispatch")

        # 波内每个成员一个子令牌（父取消 → 全部子取消）
        sub = token.child(name=f"wave1/{role}")
    """

    def __init__(self, parent: Optional["CancellationToken"] = None, name: str = ""):
        self._lock = threading.RLock()
        self._cancelled = False
        self._reason = ""
        self._stage = ""            # 取消发生时所处的阶段（首次）
        self._ts = 0.0              # 取消时间戳
        self._last_stage = ""       # 最近经过的阶段（用于兜底 stage）
        self._callbacks: List[Callable] = []
        self._parent = parent
        self.name = name

    # ---------- 状态查询 ----------

    def _first_cancelled(self) -> Optional["CancellationToken"]:
        """返回链上第一个已取消的令牌（自己优先），没有则 None。"""
        node = self
        while node is not None:
            if node._cancelled:
                return node
            node = node._parent
        return None

    @property
    def is_cancelled(self) -> bool:
        return self._first_cancelled() is not None

    @property
    def reason(self) -> str:
        token = self._first_cancelled()
        return token._reason if token else ""

    @property
    def stage(self) -> str:
        """取消时任务**实际停在哪一步**（model_call / tool_call ...）；未取消为空。

        语义要点（踩过坑）：这里必须**优先返回 last_stage**（任务最后经过的阶段），
        而不是 cancel() 时指定的那个 stage。因为：
          · `cancel(stage=...)` 记的是"**在哪里喊的停**"（user_stop / wave_dispatch）
          · `raise_if_cancelled(stage)` 记的是"**任务跑到哪一步**"（model_call / tool_call）
        大哥想知道的是后者 —— "谁停在哪一步"，终态归类也靠它。
        想查"在哪喊的停"用 `cancel_site`。
        """
        token = self._first_cancelled()
        if token is None:
            return ""
        with token._lock:
            return token._last_stage or token._stage

    @property
    def cancel_site(self) -> str:
        """取消**在哪里被触发**（user_stop / wave_dispatch / reset ...）。

        与 `stage` 互补：stage 说"任务停在哪一步"，cancel_site 说"谁喊的停"。
        """
        token = self._first_cancelled()
        if token is None:
            return ""
        return token._stage

    @property
    def last_stage(self) -> str:
        """最近一次 mark_stage 记录的阶段（无论是否已取消）。"""
        with self._lock:
            return self._last_stage

    @property
    def cancelled_at(self) -> float:
        token = self._first_cancelled()
        return token._ts if token else 0.0

    @property
    def owner(self) -> Optional["CancellationToken"]:
        """链上真正被取消的那个令牌（用于排查"是父还是我"）。"""
        return self._first_cancelled()

    def state(self) -> dict:
        """快照，供写任务板 / 报告 / 测试断言。"""
        token = self._first_cancelled()
        with self._lock:
            return {
                "name": self.name,
                "cancelled": token is not None,
                "reason": token._reason if token else "",
                # stage = 任务停在哪一步；cancel_site = 在哪喊的停（两者含义不同）
                "stage": (token._last_stage or token._stage) if token else "",
                "cancel_site": token._stage if token else "",
                "last_stage": self._last_stage,
                "cancelled_at": token._ts if token else 0.0,
                "by_self": bool(self._cancelled),
            }

    # ---------- 阶段追踪 ----------

    def mark_stage(self, stage: str):
        """记录"当前进入的阶段"。取消时若没显式给 stage 就回落到这里。"""
        with self._lock:
            self._last_stage = stage or ""
        return self

    # ---------- 取消动作 ----------

    def cancel(self, reason: str = "", stage: str = "") -> bool:
        """请求取消。幂等：只有第一次生效（原因/阶段保留首次值）。

        返回 True 表示"本次调用真正完成了取消"，False 表示"此前已取消过"。
        """
        with self._lock:
            if self._cancelled:
                return False
            self._cancelled = True
            self._reason = reason or "已取消"
            self._stage = stage or self._last_stage
            self._ts = time.time()
            callbacks = list(self._callbacks)
            self._callbacks = []
        # 回调放在锁外调：回调里若再触碰令牌，不会自死锁
        for cb in callbacks:
            try:
                cb(self)
            except Exception:
                pass
        return True

    def on_cancel(self, callback: Callable[["CancellationToken"], None]):
        """注册取消回调。若注册时已取消，则立即调用（不静默丢事件）。"""
        fire_now = False
        with self._lock:
            if self._cancelled:
                fire_now = True
            else:
                self._callbacks.append(callback)
        if fire_now:
            try:
                callback(self)
            except Exception:
                pass
        return self

    def raise_if_cancelled(self, stage: str = "") -> None:
        """在可安全中断的位置调用：已取消则抛 CancelledError。

        同时把 stage 记为"最近经过的阶段" —— 即使这次没取消，
        之后别处取消时也能知道任务最后停在哪个阶段。
        """
        if stage:
            self.mark_stage(stage)
        token = self._first_cancelled()
        if token is not None:
            with token._lock:
                # 优先「任务实际停在哪一步」——终态归类要的是这个
                st = token._last_stage or token._stage or stage
            raise CancelledError(token._reason, st)

    # ---------- 派生 ----------

    def child(self, name: str = "") -> "CancellationToken":
        """派生一个子令牌：父取消 → 子视为已取消；子取消不影响父。"""
        return CancellationToken(parent=self, name=name)


# ---------- 便捷构造 ----------

def new_token(name: str = "") -> CancellationToken:
    return CancellationToken(name=name)
