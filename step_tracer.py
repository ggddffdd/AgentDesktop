# -*- coding: utf-8 -*-
"""小臭玩AI — Agent 步级追踪器（v4.59）

参照 OpenAI SDK trace span / LangSmith step-by-step 设计。
轻量增量模块：不篡改现有 agent 循环，在关键节点插桩记录结构化日志。
每条记录包含：步号、时间戳、阶段、模型输入摘要、决策、工具调用/结果、耗时。
输出为 JSONL 文件，可直接用 jq/grep 搜索，也可未来接可视化面板。

用法：
    from step_tracer import StepTracer
    tracer = StepTracer()
    tracer.start()
    tracer.trace(step=1, phase="thinking", summary="用户要求搜索AI趋势", model_thought="...")
    tracer.trace(step=1, phase="tool_call", tool_name="web_search", args={...})
    tracer.trace(step=1, phase="tool_result", tool_name="web_search", result="...", duration_ms=1234)
    tracer.done()

输出文件：~/Documents/小臭玩AI/traces/<session_id>.jsonl
"""

import json
import os
import threading
from datetime import datetime


class StepTracer:
    """Agent 步级追踪器：每步写一条 JSONL 记录，线程安全。"""

    def __init__(self, session_id=None):
        base = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI", "traces")
        os.makedirs(base, exist_ok=True)
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(base, f"{self.session_id}.jsonl")
        self._started_at = None
        self._lock = threading.Lock()

    def start(self):
        self._started_at = datetime.now()
        self._write({
            "type": "session_start",
            "session_id": self.session_id,
        })

    def trace(self, step, phase, **kwargs):
        """记录一个步级事件。

        phase 可选值：
          "thinking"  — 模型收到消息，开始思考
          "tool_call" — 模型决定调工具
          "tool_result" — 工具执行完成
          "nudge"     — 触发"光说不做"警告
          "done"      — 本轮完成
        """
        entry = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "step": step,
            "phase": phase,
        }
        entry.update(kwargs)
        self._write(entry)

    def done(self, total_steps=0, total_duration_s=0):
        self._write({
            "type": "session_end",
            "session_id": self.session_id,
            "total_steps": total_steps,
            "duration_s": round(total_duration_s, 1),
        })

    @property
    def trace_path(self):
        return self.path

    def _write(self, entry):
        try:
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 追踪失败不应影响主流程


# 便捷：从已有 session id 创建
def get_tracer(session_id):
    return StepTracer(session_id)
