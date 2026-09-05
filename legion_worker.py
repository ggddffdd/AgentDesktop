# -*- coding: utf-8 -*-
"""Agent 军团执行器 v4.121

把 legion.py 定义的项目（波次 + 角色成员）编译成 task_graph.TaskGraph 并执行：

- **wave 内并行**：同一波次成员无依赖，TaskGraph 依赖就绪即并行（内部 ThreadPoolExecutor，
  上限 min(len(ready), 5)，与既有 multi_search 同款）。
- **wave 间串行**：wave N 的每个成员 depend wave N-1 的**全部**成员，天然形成波次屏障。
- **每个成员 = 一个 AgentNode**：角色 prompt 由 legion.build_role_prompt(角色) 生成，
  工具白名单来自角色的 tools 字段，自动工具循环由 AgentNode 负责。

LLM 通道沿用 OrchestrateWorker 的既有模式：mw._agent_call（见 ui.py:579），
不另起一套网络栈，避免与主程序的模型/超时/重试策略分叉。

已知限制：TaskGraph.run 不支持中途取消（无取消钩子），MVP 阶段不提供停止按钮；
后续如需取消，可在 _wrap 里查 self._stop_requested 抛异常中断。
"""
import logging

from PySide6.QtCore import QThread, Signal

# 顶层导入（不放在 run() 内）：保证 PyInstaller 静态分析能扫到这三个模块，
# 否则打包后运行会 ModuleNotFoundError。三者之间无循环依赖，可安全顶层导入。
import legion
from task_graph import TaskGraph
from agent_node import AgentNode

log = logging.getLogger("legion")


class LegionWorker(QThread):
    """按项目波次跑一个军团任务，结果以纯文本归并后经 done 信号抛出。"""

    log_line = Signal(str)              # 进度/日志文本（追加到日志框）
    node_status = Signal(str, str)      # (task_id, status) status ∈ running / done / error
    done = Signal(str)                  # 归并后的完整结果文本

    def __init__(self, mw, project, task="", parent=None):
        super().__init__(parent)
        self.mw = mw
        self.project = project or {}
        self.task = task or ""
        self._outputs = []              # [(wave_idx, role_name, text)]

    # ---- 内部：包装 executor 让 UI 能看到每个成员的起止 ----
    def _wrap(self, agent, tid):
        def _exec(state):
            self.node_status.emit(tid, "running")
            try:
                r = agent.run(state)
                self.node_status.emit(tid, "done")
                return r
            except Exception as e:
                self.node_status.emit(tid, "error")
                self.log_line.emit(f"  [{tid}] 成员执行异常：{e}\n")
                # 返回原 state 快照：让 TaskGraph 继续推进，单个成员失败不拖垮整个军团
                return dict(state)
        return _exec

    def run(self):
        proj = self.project
        pname = f"{proj.get('emoji', '')}{proj.get('name', '军团')}".strip()
        waves = legion.wave_members(proj)

        if not waves:
            self.log_line.emit("该项目还没有配置团队成员 —— 先点「+ 添加团队」把人加进来。\n")
            self.done.emit("")
            return

        task = self.task
        tg = TaskGraph()
        id_map = []  # id_map[wave_idx] = [(task_id, role_name), ...]

        # ---- 建图：每个成员一个节点 ----
        for wi, members in enumerate(waves):
            ids = []
            for mi, role in enumerate(members):
                tid = f"w{wi}_m{mi}"
                role_name = f"{role.get('emoji', '')}{role.get('name', '角色')}".strip()
                prompt = legion.build_role_prompt(role)
                agent = AgentNode(
                    tid,
                    prompt,
                    tools=set(role.get("tools") or []),
                    mw=self.mw,
                )
                tg.create(tid, role_name, self._wrap(agent, tid),
                          role.get("mission", ""))
                ids.append((tid, role_name))
            id_map.append(ids)

        # ---- 波间依赖：本波每个成员依赖上一波全部成员 ----
        for wi in range(1, len(id_map)):
            for tid, _ in id_map[wi]:
                for ptid, _ in id_map[wi - 1]:
                    tg.depend(tid, ptid)

        total = sum(len(w) for w in id_map)
        self.log_line.emit(
            f"▶ 军团启动：{pname} · {len(waves)} 个波次 · {total} 位成员\n")
        self.log_line.emit(f"任务：{task}\n")
        for wi, ids in enumerate(id_map):
            names = "、".join(n for _, n in ids)
            self.log_line.emit(f"  第 {wi + 1} 波（并行）：{names}\n")
        self.log_line.emit("\n")

        # ---- 执行 ----
        try:
            state = tg.run({"task": task, "query": task})
        except Exception as e:
            self.log_line.emit(f"\n✗ 军团执行失败：{e}\n")
            self.done.emit("")
            return

        # ---- 归并：按波次顺序汇总每位成员的产出 ----
        parts = []
        for wi, ids in enumerate(id_map):
            for tid, role_name in ids:
                node_out = state.get(tid, {})
                txt = ""
                if isinstance(node_out, dict):
                    txt = node_out.get(f"{tid}_output", "") or ""
                if txt:
                    parts.append(f"## 第 {wi + 1} 波 · {role_name}\n\n{txt}")

        output = "\n\n---\n\n".join(parts)
        self.log_line.emit(
            f"\n✓ 军团执行完毕（{len(parts)}/{total} 位成员有产出）。\n")
        self.done.emit(output or "（军团未产出内容）")
