# -*- coding: utf-8 -*-
"""task_state.py —— v4.225 主 Agent 轻量任务状态机（审查报告 P2「主 Agent 任务状态机」）

为什么要有它
------------
v4.225 之前，Agent 主循环判断「任务做完没有」只有一个信号：**模型这一轮
是不是输出了纯文本**。只要模型停下来说话，循环就认为可以收尾了。于是：

* 模型说「我先帮你搜一下资料」→ 文本 → 直接收尾 → **一件没干**（这靠
  `_idle_steps` / `_nudged` 兜着，但那是有话就 nudge 的**泛化**启发式，
  不看任务到底要求了什么）。
* 模型调了 3 个工具、第 3 个失败了 → 照样收尾 → 「已完成」结论里混着失败。
* 用户要「生成视频**并保存到 D 盘**」，模型生成了视频但没存盘 → 收尾，
  交付物清单里有产物吗？没有，但没人查。

本模块给主循环一个**结构化的任务账本**，让「做完了吗」变成可判定的事：

    TaskState
      ├─ required_tools   用户点名/明确要求的工具（硬要求）
      ├─ used_tools       本轮实际调用过的工具
      ├─ artifacts        待验证产物（路径 + 是否真实落盘）
      ├─ failed_tools     调用失败的工具
      └─ current_step     当前步号（供状态栏/日志）

    is_complete() / pending() → 未达标清单 → agent 注入一轮定向 nudge。

**关键纪律：不改变「模型停下就收尾」这个主语义。**
本模块只在满足下面**全部**条件时才介入（gate 严）：
  1. 本轮确实有硬要求（用户点名了工具，或声明了产物）；
  2. 存在未满足项；
  3. 步数预算**还有余量**；
  4. 本轮**还没注入过**本模块的 nudge（只补一轮，防死循环）；
否则一律 `pending()` 返回空 → agent 完全按旧路径收尾（零行为变化）。

fail-open：任何异常 → `record_*` / `pending` 返回安全值，绝不阻断 Agent 主循环。
"""
import logging
import os

log = logging.getLogger("dsdesktop")

VERSION = "v4.244.0"

# ============================================================
# 会产出实物的工具（按工具名 → 产物是否应当落盘到磁盘）
# ============================================================
# 值为 True = 该工具成功后应有磁盘产物；False = 该工具本身不产文件
# （如 web_search 只是拿数据）。用于「待验证产物」检查。
_FILE_PRODUCING = {
    "write_file": True,
    "video_gen": True,
    "image_gen": True,
    "chart_gen": True,
    "download_file": True,
    "rag_index": True,
}

# 反向：会「改外部状态」但明确不产文件的工具。
# 排除它们是为了不把它们误当产物等待（等一个永远不会出现的文件 = 卡死）。
_NO_FILE_TOOLS = frozenset((
    "web_search", "web_fetch", "read_file", "run_python", "run_command",
    "sys_info", "remember", "search_memory", "analyze_image", "use_skill",
    "send_email", "db_query", "log_query", "context_compress",
    "context_summary", "webhook_start", "webhook_stop", "webhook_events",
    "list_automation", "create_automation", "delete_automation",
    "create_skill", "legion_board", "legion_find_asset", "legion_get_output",
    "legion_get_sources", "legion_list_outputs", "legion_read_log",
    "legion_report_issue", "schedule", "run_workflow",
    "process_kill", "app_kill", "app_close", "clean_recycle_bin",
    "system_info", "clipboard_read", "clipboard_write",
))

_ST_OK = "ok"
_ST_PENDING = "pending"
_ST_FAILED = "failed"
_ST_SKIPPED = "skipped"


def required_from_text(text):
    """从用户原话里提取**字面点名**的工具名，作为任务的硬要求。

    为什么只认「字面点名」，不认关键词命中
    --------------------------------------
    `intent.Intent.requested_tools` 是**关键词**命中来的，语义上宽得多：
    「帮我写一段口播文案」会命中 video_gen（因为词表里有「口播」），
    但用户要的是一段文字，**没让你生视频**。拿它当硬要求 =
    逼模型为一个不存在的产物去调视频工具 = 制造新事故。

    所以口径收紧到「用户在话里**直接把工具名当词写出来**」：
      「用 video_gen 生成」「调web_search 查一下」→ 硬要求
      「帮我写口播文案」「生成个视频」→ 不是硬要求（那是意图，不是点名）

    返回 [(tool_name, 用户原文里的写法), ...]，去重保序。
    """
    import re
    t = str(text or "")
    if not t:
        return []
    low = t.lower()
    out = []
    try:
        import intent as _intent_mod
        names = [r["name"] for r in _intent_mod.ROUTE_REGISTRY]
    except Exception:
        names = []
    for nm in sorted(set(list(_NO_FILE_TOOLS) + names)):
        # 词边界：工具名带下划线，直接 in 可能命中更长标识符的一部分
        #（如 image_gen 命中 my_image_genner）。用 (?<![A-Za-z0-9_]) 包住。
        pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(nm.lower())
                         + r"(?![A-Za-z0-9_])")
        m = pat.search(low)
        if m:
            out.append((nm, t[m.start():m.end()]))
    return out


# ============================================================
# v4.227（P2-3）：把「只认字面点名」这条设计决定登记成**可检出的事实**
# ============================================================
# 背景：v4.218《Agent 能力审查报告》P2 有一条「任务状态机只认工具名，
# 不解析自然语言目标」—— 报告把它当缺陷。核实结论：**这是刻意设计，不是缺陷**。
#
# 为什么刻意：换成关键词/意图命中，看起来更聪明，实际会制造新事故。
# 典型：用户说「帮我写一段口播文案」，`intent.Intent.requested_tools` 会因为
# 词表里有「口播」而命中 video_gen —— 但用户要的是一段文字，没让你生视频。
# 拿它当硬要求 = 逼模型为一个不存在的产物去调视频工具。
# 「只认字面点名」宁可漏（漏了顶多少收尾 nudge），不可错（错了逼出假产物）。
#
# 为什么登记成常量而不是只留注释：注释不会被人 grep 到，判据也钉不住。
# 登记成数据后，①判据能断言它在 ②日后有人想「顺手改成关键词命中」时，
# 改动会立刻被这条登记 + 对应判据 + 对应扰动三处夹住。
# —— 与 v4.226 P1-1 给 `agent_node._AllowDecision` 钉设计决定是同一手法。

# 硬要求的判定口径：只认「用户在话里把工具名当词写出来」。
HARD_REQUIREMENT_POLICY = "literal_tool_name_only"

# 刻意不采用的口径，及各自理由（防止日后「为什么不用 X」时重新拍脑袋）
REJECTED_REQUIREMENT_POLICIES = {
    "intent_keyword": "关键词命中会把「写口播文案」误判成硬要求 video_gen，"
                      "逼模型生成用户没要的产物（宁可漏，不可错）",
    "fuzzy_match": "模糊匹配把不确定当确定，模型会为一个猜出来的目标调工具",
    "llm_extract": "多一次模型调用即多一个失败点，且提取结果无法静态验证",
}


def requirement_policy_report():
    """给判据与排障用：当前口径 + 被否决的口径及理由。"""
    return {
        "active": HARD_REQUIREMENT_POLICY,
        "rejected": dict(REJECTED_REQUIREMENT_POLICIES),
    }


class TaskState(object):
    """v4.225 任务账本（轻量、无外部依赖、可单测）。"""

    __slots__ = ("goal", "required_tools", "used_tools", "artifacts",
                 "failed_tools", "succeeded_tools", "current_step", "nudge_injected", "steps",
                 "workflow_incomplete")

    def __init__(self, goal="", required_tools=()):
        self.goal = goal or ""
        # 去重且保序：required_tools 的顺序 = 用户话里提到的顺序，
        # 补 nudge 时按这个顺序提示，读起来符合用户的心智模型。
        seen = set()
        self.required_tools = []
        for t in (required_tools or ()):
            t = str(t or "").strip()
            if t and t not in seen:
                seen.add(t)
                self.required_tools.append(t)
        self.used_tools = []
        self.artifacts = []       # [(path, exists_bool, tool)]
        self.failed_tools = []    # [tool]
        self.succeeded_tools = []  # [tool]（与 failed 对称：先败后成时归正）
        self.current_step = 0
        self.nudge_injected = False
        self.steps = 0
        self.workflow_incomplete = None  # v4.233 断点 E：任务图未完整达成的节点集合（None=完整）

    # --------------------------------------------------------
    # 记账
    # --------------------------------------------------------
    def record_tool(self, name, args=None, result=None, ok=True, step=0):
        """一次工具调用后记账。

        `ok` 由调用方判定（agent 已有 `_tool_result_looks_failed` 这类判据，
        这里不重复判定，只接收结论）。产物路径优先从 args 取（write_file 的
        path / video_gen 的 output_path 之类），取不到就不记产物 —— 宁可
        少记不可错记（错记会让 pending 永远不为空 → 无休止 nudge）。
        """
        name = str(name or "").strip()
        if not name:
            return
        self.steps = int(step or self.steps)
        if name not in self.used_tools:
            self.used_tools.append(name)
        if not ok and name not in self.failed_tools:
            self.failed_tools.append(name)
        if ok:
            if name not in self.succeeded_tools:
                self.succeeded_tools.append(name)
            if name in self.failed_tools:  # 先败后成：状态归正，避免遗留失败标记
                self.failed_tools.remove(name)
        if ok and name in _FILE_PRODUCING:
            p = self._artifact_path(name, args)
            if p and not any(a[0] == p for a in self.artifacts):
                self.artifacts.append((p, self._exists(p), name))

    @staticmethod
    def _artifact_path(name, args):
        """从参数里提取产物路径（**闭集**，只认这几个键）。

        刻意不做「扫全部参数找像路径的值」——开集扫描会把 prompt/描述里的
        路径字样也当成产物，等一个不存在的文件 → 永远 pending → 卡死。
        """
        if not isinstance(args, dict):
            return None
        for k in ("path", "output_path", "file_path", "save_path", "out_path",
                  "dest", "filepath", "filename", "output", "target_path"):
            v = args.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    @staticmethod
    def _exists(path):
        try:
            return bool(path) and os.path.exists(path)
        except Exception:
            return False

    def note_workflow_incomplete(self, failed, skipped):
        """v4.233 断点 E：任务图内部有 failed/skipped 节点时，由 _run_workflow 回写。

        仅记录未完整达成的节点集合（fail-open：异常也不影响主流程），作为
        「工作流未完整达成」的可见标记。不动 gate 判定、不触发 nudge——
        可见性修复（轻档），重档（把失败节点工具/产物回写 record_tool(ok=False)）
        留作后续。
        """
        try:
            self.workflow_incomplete = {
                "failed": list(failed or []),
                "skipped": list(skipped or []),
            }
        except Exception:
            pass

    def note_artifact(self, path, tool=""):
        """外部路径登记（agent 已算出绝对交付物时调用）。"""
        p = str(path or "").strip()
        if p and not any(a[0] == p for a in self.artifacts):
            self.artifacts.append((p, self._exists(p), str(tool or "")))

    # --------------------------------------------------------
    # 判定
    # --------------------------------------------------------
    def has_requirements(self):
        """本轮是否有「硬要求」——没有就完全不介入（gate 条件 1）。"""
        return bool(self.required_tools)

    def missing_tools(self):
        """点名了但从没调过的工具。"""
        used = set(self.used_tools)
        return [t for t in self.required_tools if t not in used]

    def missing_artifacts(self):
        """声明了产物但磁盘上不存在的。"""
        return [(p, t) for (p, ok, t) in self.artifacts if not ok]

    def is_complete(self):
        """全部硬要求满足。"""
        return not self.missing_tools() and not self.missing_artifacts()

    def pending(self):
        """返回未达标清单（人类可读字符串列表）；空列表 = 达标。"""
        out = []
        for t in self.missing_tools():
            out.append("还没调用工具 %s（用户点名要它）" % t)
        for (p, tool) in self.missing_artifacts():
            out.append("产物还没落地：%s（%s 声称成功但文件不存在）"
                       % (p, tool or "工具"))
        # v4.231 A 断点修复：失败的"点名工具"也算未达标，否则账本会
        # 把"调过但失败"误判成"已完成"（静默收尾、不 nudge、不重试）。
        # 限定 required_tools 且排除已 succeeded（先败后成归正）的，避免对非点名
        # 的自主失败误 nudge；防死循环由 should_nudge 的 injected+max_steps 闸覆盖。
        for t in self.failed_tools:
            if t in self.required_tools and t not in self.succeeded_tools:
                out.append("工具 %s 上次调用失败，需要重试或换方案" % t)
        return out

    # --------------------------------------------------------
    # 相C：前置校验前置化（把「漏落盘」从收尾救回提前到事中 nudge）
    # --------------------------------------------------------
    def _precond_artifact_ok(self, tool):
        """相C：前置工具是否已「成功且产物落盘」。

        口径（稳健优先，避免凭路径缺失误判）：
          · 非文件产出工具（web_search 等）→ 无需产物，恒 True；
          · 有已记录产物且 exists=True → True；
          · 有已记录产物但 exists=False（明确失败）→ False；
          · 该工具成功但没记下产物路径（agent 未捕获 path）→ 信任成功，True。
        """
        if tool not in _FILE_PRODUCING:
            return True
        found_ok = False
        found_fail = False
        for (p, ok, t) in self.artifacts:
            if t == tool:
                if ok:
                    found_ok = True
                else:
                    found_fail = True
        if found_ok:
            return True
        if found_fail:
            return False
        return True  # 没记到产物（信任成功）

    def precond_check(self, dag):
        """相C 前置校验前置化：把「漏落盘」从收尾救回提前到事中 nudge。

        遍历 dag 节点，对每个声明了 precond_tool 的节点，核对该前置工具是否
        已被 record_tool 记成功（succeeded_tools）且产物已落盘（_precond_artifact_ok）；
        特别地，write_file 节点要求 write_file 自身已被调用（用户明确要存盘）。

        返回未满足项清单：[(node_id, tool, precond_tool, kind), ...]
          kind ∈ {"precond_not_called", "precond_no_artifact", "write_not_called"}
        空列表 = 全部满足，不该介入。

        fail-open：dag 为 None / 无 nodes / 异常 → 返回 []（退回原行为，不阻断主循环）。
        不污染 required_tools：本方法只读既有事实，绝不改写硬要求口径。
        """
        try:
            if dag is None or not hasattr(dag, "nodes"):
                return []
            nodes = getattr(dag, "nodes", ())
            if not nodes:
                return []
            missing = []
            for n in nodes:
                pt = getattr(n, "precond_tool", "") or ""
                if not pt:
                    continue
                tool = getattr(n, "tool", "")
                nid = getattr(n, "id", "")
                called = pt in self.succeeded_tools
                artifact_ok = self._precond_artifact_ok(pt)
                if not called:
                    missing.append((nid, tool, pt, "precond_not_called"))
                    continue
                if not artifact_ok:
                    missing.append((nid, tool, pt, "precond_no_artifact"))
                    continue
                # 前置已满足：若本节点是 write_file，自身必须被调用
                if tool == "write_file" and "write_file" not in self.succeeded_tools:
                    missing.append((nid, tool, pt, "write_not_called"))
            return missing
        except Exception:
            return []

    # --------------------------------------------------------
    # nudge 文案
    # --------------------------------------------------------
    def should_nudge(self, step, max_steps, already_injected=None):
        """是否该注入补做提示。四个 gate 全过才 True。

        `already_injected` 覆盖本对象记录的 nudge_injected（调用方更清楚
        本轮是否已注入过）。**只补一轮** —— 多轮会与 _idle_steps / _nudged
        那两个护栏叠加成死循环。
        """
        injected = self.nudge_injected if already_injected is None else already_injected
        try:
            if injected:
                return False
            if not self.has_requirements():
                return False
            if not self.pending():
                return False
            # 步数预算必须有余量，否则注入提示也没有下一轮可跑。
            if max_steps and int(step) >= int(max_steps):
                return False
            return True
        except Exception:
            return False

    def nudge_instruction(self):
        """生成补做提示（追加到 messages 作为一条 user 侧纠正指令）。

        措辞要点：说清**缺什么**（不是泛泛「继续努力」），并明确「不要复述
        已完成的部分」——弱模型最常见的反应是把已做的事重述一遍当作交差。
        """
        items = self.pending()
        if not items:
            return ""
        lines = [
            "【任务未完成检查】你在给最终结论，但以下要求尚未满足：",
        ]
        for i, it in enumerate(items, 1):
            lines.append("  %d) %s" % (i, it))
        lines.append(
            "请补做上面未完成的部分（真实调用工具，不要只在文字里声称做过）。"
            "不要重复已经完成的内容，也不要输出空泛的『我已完成』——"
            "若因客观原因确实做不到，请直接说明哪一条做不到、为什么。")
        return "\n".join(lines)

    def dag_nudge_instruction(self, missing):
        """相C 定向 nudge 文案（基于 precond_check 的未满足项）。

        措辞要点：说清「前置已完成但后续没做」，并明确要调哪个工具，
        弱模型常见反应是复述已做内容充当交差 → 明确「真实调用工具」。
        """
        if not missing:
            return ""
        lines = ["【任务前置检查】检测到动作已完成、但后续步骤未执行："]
        for (nid, tool, pt, kind) in missing:
            if kind == "write_not_called":
                lines.append("  · 检测到「%s」已完成，但「%s」未执行，请先调用 %s 将产物保存到磁盘。"
                             % (pt, tool, tool))
            elif kind == "precond_not_called":
                lines.append("  · 前置动作「%s」尚未执行，无法继续「%s」，请先完成前置。"
                             % (pt, tool))
            else:  # precond_no_artifact
                lines.append("  · 前置动作「%s」虽已调用但产物未落盘，请确认「%s」可继续。"
                             % (pt, tool))
        lines.append("请补做上面的步骤（真实调用工具，不要只在文字里声称做过）。"
                     "若因客观原因确实做不到，请直接说明哪一条做不到、为什么。")
        return "\n".join(lines)

    def mark_nudged(self):
        self.nudge_injected = True

    # --------------------------------------------------------
    def summary(self):
        """一行状态（状态栏/日志用）。"""
        return ("目标=%s | 步%d | 已用%s | 缺工具%s | 产物%d(缺%d)"
                % (self.goal[:30] or "-", self.current_step,
                   ",".join(self.used_tools[:4]) or "-",
                   ",".join(self.missing_tools()) or "无",
                   len(self.artifacts), len(self.missing_artifacts())))

    def to_dict(self):
        return {
            "goal": self.goal,
            "required_tools": list(self.required_tools),
            "used_tools": list(self.used_tools),
            "failed_tools": list(self.failed_tools),
            "artifacts": [(p, ok, t) for (p, ok, t) in self.artifacts],
            "current_step": self.current_step,
            "nudge_injected": self.nudge_injected,
            "status": _ST_FAILED if self.failed_tools else (
                _ST_PENDING if self.pending() else _ST_OK),
        }


__all__ = ["VERSION", "TaskState", "required_from_text",
           "_FILE_PRODUCING", "_NO_FILE_TOOLS",
           "_ST_OK", "_ST_PENDING", "_ST_FAILED", "_ST_SKIPPED"]