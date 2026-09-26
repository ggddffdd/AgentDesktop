# -*- coding: utf-8 -*-
"""统一任务状态总线（可观测性）

解决的问题
----------
模型调用、网页抓取、文件解析、视频生成等任务此前只把状态写进日志，
用户界面感知是「没反应」，排查成本高。本模块把这些任务的生命周期
统一成四个用户看得懂的状态，并携带失败原因与「可重试」标记。

状态流转
--------
    received(已接收) → running(处理中) → done(完成)
                                      ↘ failed(失败，带原因，可标可重试)

设计约束（重要）
----------------
1. 纯标准库，**零 Qt 依赖**（与 browser_bridge 一致）：本模块只管状态与
   订阅通知；UI 侧自行用 Qt signal 做跨线程 queued 投递。
2. **线程安全**：桥接回调来自 HTTP 线程，模型/视频任务来自工作线程，
   订阅通知可能从任意线程触发。
3. **绝不存储敏感内容**：只保留标签与失败原因，且落库前统一脱敏 + 截断
   （抹掉 data URI / 长 base64 / URL 内 key / 明文 sk- 密钥）。
4. 订阅者异常不得影响调用方（生产者绝不能因状态总线报错而中断）。

用法
----
    import task_status as ts

    tid = ts.begin("browser", "抓取「标题」")
    ts.progress(tid, "已注入输入框")
    ts.done(tid)
    # 失败时：
    ts.fail(tid, "自动发送失败：TimeoutError", retryable=True)

或用上下文管理器：

    with ts.track("model", "对话回复") as tid:
        ...   # 抛异常自动记为 failed(retryable=True)
"""
import re
import threading
import time
import uuid

# ---------------- 状态与类别 ----------------

STATE_RECEIVED = "received"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"

STATE_LABEL = {
    STATE_RECEIVED: "已接收",
    STATE_RUNNING: "处理中",
    STATE_DONE: "完成",
    STATE_FAILED: "失败",
}

# 状态排序权重：数值越小越靠前（活跃任务优先展示）
STATE_ORDER = {
    STATE_RUNNING: 0,
    STATE_RECEIVED: 1,
    STATE_FAILED: 2,
    STATE_DONE: 3,
}

KIND_LABEL = {
    "model": "模型调用",
    "search": "联网搜索",
    "browser": "网页抓取",
    "file": "文件解析",
    "video": "视频生成",
    "image": "图片生成",
    "agent": "Agent 任务",
    "speech": "语音识别",
    "other": "任务",
}

# 一条失败原因最多保留的字符数（防御性截断）
DETAIL_MAX = 200
# 默认保留的已完成任务条数（含成功与失败）
KEEP_FINISHED = 5

# ---------------- 脱敏 ----------------
_DATA_URI_RE = re.compile(r"data:[^;,\s]+;base64,[A-Za-z0-9+/=]+")
_LONG_B64_RE = re.compile(r"[A-Za-z0-9+/]{80,}={0,2}")
_URL_KEY_RE = re.compile(r"([?&](?:key|token|api_key|access_token)=)[^&\s]+", re.I)
_SK_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def _safe(text):
    """脱敏 + 截断。状态条只用于展示，绝不保留敏感原文。"""
    try:
        s = "" if text is None else str(text)
    except Exception:
        return ""
    try:
        s = _DATA_URI_RE.sub("<img>", s)
        s = _LONG_B64_RE.sub("<b64>", s)
        s = _URL_KEY_RE.sub(r"\1<redacted>", s)
        s = _SK_RE.sub("sk-<redacted>", s)
    except Exception:
        pass
    s = s.replace("\r", " ").replace("\n", " ").strip()
    if len(s) > DETAIL_MAX:
        s = s[:DETAIL_MAX] + "…"
    return s


def _label(text, limit=60):
    s = _safe(text)
    if len(s) > limit:
        s = s[:limit] + "…"
    return s


# ---------------- 任务记录 ----------------

class Task:
    """单个任务的状态记录。"""

    __slots__ = ("id", "kind", "label", "state", "detail", "retryable",
                 "created_at", "updated_at", "ended_at")

    def __init__(self, task_id, kind, label):
        self.id = task_id
        self.kind = kind if kind in KIND_LABEL else "other"
        self.label = _label(label)
        self.state = STATE_RECEIVED
        self.detail = ""
        self.retryable = False
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.ended_at = None

    @property
    def kind_label(self):
        return KIND_LABEL.get(self.kind, KIND_LABEL["other"])

    @property
    def state_label(self):
        return STATE_LABEL.get(self.state, self.state)

    @property
    def is_active(self):
        return self.state in (STATE_RECEIVED, STATE_RUNNING)

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "kind_label": self.kind_label,
            "label": self.label,
            "state": self.state,
            "state_label": self.state_label,
            "detail": self.detail,
            "retryable": bool(self.retryable),
            "active": self.is_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ---------------- 状态总线 ----------------

class TaskRegistry:
    """线程安全的任务状态登记表 + 变更订阅。"""

    def __init__(self, keep_finished=KEEP_FINISHED):
        self._lock = threading.RLock()
        self._active = {}          # id -> Task（未结束）
        self._finished = []        # [Task] 已结束，最新的在末尾
        self._keep_finished = max(1, int(keep_finished))
        self._subs = []            # 变更订阅回调
        self._retry_hook = None    # 用户点「重试」时的回调

    # ---- 内部 ----
    def _notify(self, snap):
        for fn in list(self._subs):
            try:
                fn(snap)
            except Exception:
                # 订阅者异常不得影响生产者
                pass

    def _trim_finished(self):
        if len(self._finished) > self._keep_finished:
            del self._finished[:len(self._finished) - self._keep_finished]

    def _push_finished(self, task):
        with self._lock:
            self._finished.append(task)
            self._trim_finished()

    # ---- 生命周期 ----
    def begin(self, kind, label="", task_id=None):
        """登记并返回一个「已接收」任务。"""
        tid = task_id or uuid.uuid4().hex
        t = Task(tid, kind, label)
        with self._lock:
            self._active[tid] = t
        self._notify(self.snapshot())
        return tid

    def progress(self, task_id, detail=""):
        """标记为「处理中」，可附带阶段说明。"""
        with self._lock:
            t = self._active.get(task_id)
            if t is None:
                return False
            t.state = STATE_RUNNING
            if detail:
                t.detail = _label(detail, 120)
            t.updated_at = time.time()
        self._notify(self.snapshot())
        return True

    def done(self, task_id, detail=""):
        """标记为「完成」。"""
        with self._lock:
            t = self._active.pop(task_id, None)
            if t is None:
                return False
            t.state = STATE_DONE
            t.detail = _safe(detail)
            t.retryable = False
            t.updated_at = t.ended_at = time.time()
        self._push_finished(t)
        self._notify(self.snapshot())
        return True

    def fail(self, task_id, reason="", retryable=False):
        """标记为「失败」。reason 即用户看到的失败原因。"""
        with self._lock:
            t = self._active.pop(task_id, None)
            if t is None:
                return False
            t.state = STATE_FAILED
            t.detail = _safe(reason)
            t.retryable = bool(retryable)
            t.updated_at = t.ended_at = time.time()
        self._push_finished(t)
        self._notify(self.snapshot())
        return True

    # ---- 查询 ----
    def snapshot(self):
        """返回给 UI 渲染用的纯数据快照（JSON 安全）。"""
        with self._lock:
            active = sorted(self._active.values(),
                            key=lambda t: (STATE_ORDER.get(t.state, 9), t.created_at))
            recent = list(reversed(self._finished))
        return {
            "active": [t.to_dict() for t in active],
            "recent": [t.to_dict() for t in recent],
            "active_count": len(active),
            "failed_count": sum(1 for t in recent if t.state == STATE_FAILED),
        }

    def get(self, task_id):
        with self._lock:
            t = self._active.get(task_id)
            if t is not None:
                return t
            for f in self._finished:
                if f.id == task_id:
                    return f
        return None

    def clear_finished(self):
        """清空已完成/失败历史（UI 上的「知道了」）。"""
        with self._lock:
            self._finished = []
        self._notify(self.snapshot())

    def active_count(self):
        with self._lock:
            return len(self._active)

    # ---- 订阅 / 重试 ----
    def subscribe(self, fn):
        """注册变更回调 fn(snapshot)。返回该回调，便于之后退订。"""
        with self._lock:
            if fn not in self._subs:
                self._subs.append(fn)
        return fn

    def unsubscribe(self, fn):
        with self._lock:
            try:
                self._subs.remove(fn)
            except ValueError:
                pass

    def set_retry_hook(self, fn):
        """注册「重试」回调 fn(task_dict) -> bool。"""
        with self._lock:
            self._retry_hook = fn

    def retry(self, task_id):
        """用户点重试：交给重试钩子执行，成功则把该条从失败历史清掉。"""
        with self._lock:
            t = None
            for f in self._finished:
                if f.id == task_id:
                    t = f
                    break
            hook = self._retry_hook
        if t is None or hook is None:
            return False
        try:
            ok = bool(hook(t.to_dict()))
        except Exception:
            return False
        if ok:
            with self._lock:
                self._finished = [f for f in self._finished if f.id != task_id]
            self._notify(self.snapshot())
        return ok

    # ---- 测试辅助 ----
    def reset(self):
        with self._lock:
            self._active = {}
            self._finished = []
            self._subs = []
            self._retry_hook = None


# ---------------- 模块级单例与便捷函数 ----------------

registry = TaskRegistry()


def begin(kind, label="", task_id=None):
    return registry.begin(kind, label, task_id)


def progress(task_id, detail=""):
    return registry.progress(task_id, detail)


def done(task_id, detail=""):
    return registry.done(task_id, detail)


def fail(task_id, reason="", retryable=False):
    return registry.fail(task_id, reason, retryable)


def snapshot():
    return registry.snapshot()


def subscribe(fn):
    return registry.subscribe(fn)


def unsubscribe(fn):
    return registry.unsubscribe(fn)


def set_retry_hook(fn):
    return registry.set_retry_hook(fn)


def retry(task_id):
    return registry.retry(task_id)


def clear_finished():
    return registry.clear_finished()


class _Track:
    """上下文管理器：正常结束记 done，抛异常记 failed(可重试) 并继续上抛。"""

    def __init__(self, kind, label=""):
        self.kind = kind
        self.label = label
        self.id = None

    def __enter__(self):
        self.id = begin(self.kind, self.label)
        return self.id

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            done(self.id)
        else:
            fail(self.id, f"{exc_type.__name__}: {exc}", retryable=True)
        return False   # 不吞异常


def track(kind, label=""):
    return _Track(kind, label)
