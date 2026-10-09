# -*- coding: utf-8 -*-
"""agent_loop.py —— v4.226 计划-执行-验证-总结 四阶段循环（审查报告 P2）

为什么要有它
------------
v4.225 给主循环装了**任务账本**（task_state）：能回答「用户点名的东西做了没」。
但账本只解决了**判定**，没解决**时序**。主循环至今仍是「一步一问答」：

    step1 模型 → 有 tool_calls？ → 执行 → step2 模型 → …… → 纯文本 → 收尾

也就是说：

* **没有计划**：模型不知道全局要几步，通常想到哪做到哪，中途才发现漏了前置
  步骤（典型：用户要「用 video_gen 生成并用 write_file 存到 D 盘」，模型先生成、
  收尾时才发现没存盘 —— 这正是 v4.225 收尾闸门要补一轮才能救回来的场景，
  **但那时用户已经等完了全程**）。
* **验证与执行混在同一步**：工具调完就信了，验证发生在「模型自己说完了之后」，
  而不是发生在「每批工具执行完之后」。
* **总结靠模型自觉**：最终结论里「做了什么 / 证据是什么 / 什么没做成」全靠
  模型自己写，而模型恰恰是幻觉最容易出现的位置（本项目已有 `_looks_like_fake_tool_call`
  这一整道防线就是为此）。

本模块把主循环显式分成四个阶段，并给出**每一阶段的准入门**：

    PLAN ──(计划注入，且仅一次)──▶ EXECUTE ──(有工具调用)──▶ VERIFY
                                                              │
                                            ┌─────────────────┘
                                            ▼
                                        SUMMARIZE（机器生成小结，不信模型自述）

字段与不变式
------------
1. **阶段不驱动行为，只标注行为**。PLAN/VERIFY/SUMMARIZE 三段都不是「新的
   模型轮次」，而是给既有动作加**前置条件**与**机器可核的结论**。
2. **VERIFY 的判定仍然在 task_state**（那里有 126 项判据）。本模块只做
   「把账本的结论**固定成一份可读的核验记录**」，不复制任何判定逻辑 ——
   两处判据漂移是本项目吃过亏的坑（见 MEMORY.md 二·3）。
3. **SUMMARIZE 的内容由机器生成，不由模型生成**。工具调过哪些、产物落没落盘、
   哪条要求没达成 —— 这些全是 `TaskState` 里的**事实**，模型看不到也改不了。
   这是本模块存在的最大价值：把「结论」从模型的自由发挥里抢回来。
4. **fail-open**：任何函数抛异常都返回安全值（不介入 / 空串 / False），
   绝不阻断主循环。

对外只暴露四类东西：阶段常量、`LoopState`、五个门函数（`should_plan` /
`plan_instruction` / `verify_report` / `should_summarize` / `build_summary`）。
接线在 `agent_loop_mixin.py`，agent.py 里只有一行调用点。
"""
import logging

log = logging.getLogger("dsdesktop")

VERSION = "v4.240.0"

# ============================================================
# 阶段常量
# ============================================================
PHASE_PLAN = "plan"
PHASE_EXECUTE = "execute"
PHASE_VERIFY = "verify"
PHASE_SUMMARIZE = "summarize"

PHASE_ALL = (PHASE_PLAN, PHASE_EXECUTE, PHASE_VERIFY, PHASE_SUMMARIZE)

# 阶段推进顺序（advance 只沿这个方向走，到末阶段饱和 —— 不许回退，
# 因为回退意味着「验证完又回去执行」，那正是本项目最恨的打转模式）
PHASE_ORDER = (PHASE_PLAN, PHASE_EXECUTE, PHASE_VERIFY, PHASE_SUMMARIZE)


def next_phase(cur):
    """沿 PHASE_ORDER 推进一阶段；已是末阶段则原地不动（饱和，不回退）。

    fail-open：未知阶段 → 视作 EXECUTE（主循环的中枢态）。
    """
    try:
        i = PHASE_ORDER.index(str(cur))
        return PHASE_ORDER[i + 1] if i + 1 < len(PHASE_ORDER) else PHASE_ORDER[i]
    except Exception:
        return PHASE_EXECUTE


# ============================================================
# LoopState —— 极轻量阶段账（无外部依赖，可单测）
# ============================================================
class LoopState(object):
    """四阶段循环的阶段状态（不含判定，判定全在模块级函数里）。

    刻意与 `task_state.TaskState` 分开：那个记「做了什么」，这个记「走到哪个
    阶段了」。合并成一个类会让两种生命周期（一步一变 vs 一轮一变）互相污染。
    """

    __slots__ = ("goal", "phase", "history", "plan", "plan_injected",
                 "verification", "verified", "summary_emitted", "step")

    def __init__(self, goal=""):
        self.goal = goal or ""
        self.phase = PHASE_PLAN
        # history: [(phase, step), ...] 只记发生过转换，不记每步流水
        self.history = [(PHASE_PLAN, 0)]
        self.plan = []              # [子目标字符串]
        self.plan_injected = False
        self.verification = []      # [核验结论字符串]（VERIFY 阶段固化下来）
        self.verified = False       # 账本是否全部达标
        self.summary_emitted = False
        self.step = 0

    def enter(self, phase, step=0):
        """切到某阶段并记一笔。非法阶段名忽略（fail-open，不静默改状态）。"""
        try:
            if phase in PHASE_ALL:
                self.phase = phase
                self.history.append((phase, int(step or 0)))
        except Exception:
            pass
        return self.phase

    def advance(self, step=0):
        """沿顺序推进一阶段（饱和，不回退）。"""
        return self.enter(next_phase(self.phase), step)

    def to_dict(self):
        return {
            "goal": self.goal,
            "phase": self.phase,
            "history": [(p, s) for (p, s) in self.history],
            "plan": list(self.plan),
            "plan_injected": bool(self.plan_injected),
            "verification": list(self.verification),
            "verified": bool(self.verified),
            "summary_emitted": bool(self.summary_emitted),
            "step": int(self.step or 0),
        }

    def summary(self):
        return ("阶段=%s 步%d 计划%d项 核验%s"
                % (self.phase, self.step, len(self.plan),
                   "通过" if self.verified else ("未过" if self.verification else "未做")))


# ============================================================
# PLAN —— 计划阶段
# ============================================================
# 触发门槛：**硬要求 ≥ 2 项**。
#
# 为什么不是「有硬要求就计划」：单硬要求（「用 video_gen 生成视频」）步骤唯一，
# 列计划纯属浪费一轮 prompt；≥2 项时才真正存在**顺序与依赖**问题
# （先搜后写、先生成后落盘），计划才有意义。
#
# 为什么门槛只数「用户字面点名的工具」：那是唯一无歧义的硬要求（task_state 的
# 口径）。用关键词命中（intent.requested_tools）会为不存在的产物编计划。
PLAN_MIN_REQUIREMENTS = 2


def should_plan(required_tools, step=1):
    """是否该在开头注入计划。门槛见 PLAN_MIN_REQUIREMENTS 说明。

    fail-open：任何异常 → False（不注入，等于旧行为）。
    """
    try:
        if int(step or 1) != 1:
            return False   # 只在第一步注入一次
        return len(list(required_tools or ())) >= PLAN_MIN_REQUIREMENTS
    except Exception:
        return False


def build_plan(goal, required_tools):
    """把「目标 + 硬要求」摊成有序子目标清单。

    刻意**只列硬要求与目标**，不臆造步骤（不写「第一步…第二步…」这类模型
    自己就能编的东西）—— 计划的价值在于「不许漏掉用户点名的那几件」，
    不是替模型做决策。
    """
    out = []
    try:
        g = str(goal or "").strip()
        if g:
            if len(g) > 120:
                g = g[:120] + "…"
            out.append("总目标：%s" % g)
        seen = set()
        for t in (required_tools or ()):
            t = str(t or "").strip()
            if t and t not in seen:
                seen.add(t)
                out.append("必须完成：真实调用工具 %s" % t)
    except Exception:
        return []
    return out


def plan_instruction(plan):
    """计划阶段注入的指令文本（一条 system 侧内部消息）。

    措辞纪律：
      * 说清「这是清单，不是让你复述」—— 弱模型最爱把清单原样念一遍当交差；
      * 明确「按需排序，不必先声明计划」—— 不逼它多花一轮说话；
      * 保留「做不完要说清楚」—— 否则它会硬凑。
    """
    try:
        items = [str(x) for x in (plan or ()) if str(x).strip()]
        if not items:
            return ""
        lines = ["【任务计划】用户点名了以下要求，本轮必须逐条落实（这是给你的"
                 "核对清单，**不要**把它复述一遍当完成）："]
        for i, it in enumerate(items, 1):
            lines.append("  %d) %s" % (i, it))
        lines.append(
            "请在心里按依赖排好顺序直接开工（不必先向我口头汇报计划）。"
            "若某条确实做不到，必须在最终结论里**明确写出是哪一条、为什么**，"
            "不许含糊带过。")
        return "\n".join(lines)
    except Exception:
        return ""


# ============================================================
# VERIFY —— 验证阶段
# ============================================================
# 验证的**判定**全部来自 TaskState（账本），本模块只做两件事：
#   ① 把结论固化成可读记录（供总结阶段引用，也便于日志归因）；
#   ② 决定要不要拦一道「先别急着收尾」。
#
# 拦的那一道（未达标 → 补做）v4.225 已经在收尾闸门做了，本模块**不重复注入**：
# 多打一道同义指令只会让模型收到两份几乎一样的纠正，反而稀释注意力。
# 所以 should_verify 只回答「要不要进 VERIFY 阶段并固化记录」，
# 「要不要补做一轮」的唯一真源仍是 task_state.should_nudge。

def verify_report(tstate):
    """把账本结论固化成核验记录（**只读**，不改账本任何状态）。

    返回 `[结论字符串, ...]`；账本为空/异常 → `[]`（= 没核验，也不拦）。
    """
    out = []
    try:
        if tstate is None:
            return []
        missing = list(tstate.missing_tools() or ())
        artifacts = list(tstate.missing_artifacts() or ())
        used = list(getattr(tstate, "used_tools", None) or ())
        if not used and not missing and not artifacts:
            return []
        if used:
            out.append("已真实调用工具：%s" % "、".join(used[:8]))
        for t in missing:
            out.append("未满足：用户点名的工具 %s 从未被调用" % t)
        for (p, tool) in artifacts:
            out.append("未满足：产物 %s 未在磁盘上落地（%s 声称成功）"
                       % (p, tool or "工具"))
        if not missing and not artifacts and used:
            out.append("账本核对：点名要求已全部落实，产物已落地")
    except Exception:
        return []
    return out


def should_verify(tstate, plan_injected=False):
    """要不要进 VERIFY 阶段固化记录。

    门槛：账本里**有过事实**（调过工具 或 有未达标项）。纯咨询、零工具调用
    的会话没有可核的东西，进 VERIFY 只会白记一条空记录。
    """
    try:
        rep = verify_report(tstate)
        if not rep:
            return False
        if plan_injected:
            return True
        return True   # 有核验记录就固化；是否**补做一轮**由 task_state 决定
    except Exception:
        return False


def is_verified(tstate):
    """账本是否**全部达标**（无未调用点名工具 + 无未落地产物）。

    为什么要有这个函数、而不在接线处直接写 `not (missing_tools() or
    missing_artifacts())`：那条判定一旦写在 mixin 里，就出现了**第二个**
    「什么叫达标」的真源 —— 与 `TaskState.is_complete()` 并存，日后改一处
    忘了另一处，模型会被告知「已达标」而账本仍有未达标项。
    收在这里 = 判定只有一处（本函数内部转调账本，不复制逻辑）。
    """
    try:
        if tstate is None:
            return False
        if not verify_report(tstate):
            return False        # 没有任何事实 → 不构成「已核验通过」
        return bool(tstate.is_complete())
    except Exception:
        return False


# ============================================================
# SUMMARIZE —— 总结阶段（机器生成，不信模型自述）
# ============================================================
# 只在**用户点名过工具**的任务上追加小结：纯问答不该多一段「本轮执行小结」。
_SUMMARY_MARK = "── 本轮执行小结 ──"


def should_summarize(tstate, emitted=False):
    """要不要追加机器小结。

    gate 全过才追加：
      1. 没追加过（每轮最多一次）；
      2. 账本存在且**有硬要求**（用户确实点名过东西）—— 纯问答不该多一段
         「本轮执行小结」；
      3. 产出的文本非空。

    ⚠️ **刻意没有**「有工具调用」这一道 gate：它会被 `build_summary` 内部的
    「无事实 → 空串」早返回兜住，等于一条永不改变结论的装饰 —— 而装饰性
    判据的失效方式恰恰是「删掉也没人发现」（本项目记过的坑：登记了却没被
    消耗）。宁可少一道。
    """
    try:
        if emitted:
            return False
        ts = tstate
        if ts is None:
            return False
        if not ts.has_requirements():
            return False
        return bool(build_summary(ts))
    except Exception:
        return False


def build_summary(tstate):
    """生成机器小结（纯事实，来自账本）。

    三段固定结构：
      做了什么   —— 真实调用过的工具（账本事实，模型改不了）
      证据       —— 落地的产物路径（只列**确认存在**的）
      未完成     —— 未调用的点名工具 / 未落地的产物 / 调用失败的工具

    空账本或无事实 → 返回 ""（不产出空标题）。
    """
    try:
        ts = tstate
        if ts is None:
            return ""
        used = list(getattr(ts, "used_tools", None) or ())
        if not used:
            return ""
        artifacts = list(getattr(ts, "artifacts", None) or ())
        landed = [a[0] for a in artifacts if len(a) >= 3 and a[0] and a[1]]
        missing_tools = list(ts.missing_tools() or ())
        missing_arts = list(ts.missing_artifacts() or ())
        failed = list(getattr(ts, "failed_tools", None) or ())

        lines = [_SUMMARY_MARK]
        lines.append("做了什么：真实调用了 %s" % "、".join(used[:8]))
        lines.append("证据：产物已落地 %d 项%s"
                     % (len(landed),
                        ("（%s）" % "、".join(str(p) for p in landed[:3])) if landed else ""))
        unmet = []
        for t in missing_tools:
            unmet.append("工具 %s 未被调用" % t)
        for (p, tool) in missing_arts:
            unmet.append("产物 %s 未落地" % p)
        for t in failed:
            unmet.append("工具 %s 执行失败" % t)
        lines.append("未完成：%s" % ("；".join(unmet[:6]) if unmet else "无"))
        lines.append("（以上由系统按真实工具调用记录生成，与你的自述可能不一致；"
                     "如有出入以本段为准。）")
        return "\n".join(lines)
    except Exception:
        return ""


__all__ = [
    "VERSION", "PHASE_PLAN", "PHASE_EXECUTE", "PHASE_VERIFY", "PHASE_SUMMARIZE",
    "PHASE_ALL", "PHASE_ORDER", "PLAN_MIN_REQUIREMENTS", "_SUMMARY_MARK",
    "LoopState", "next_phase", "should_plan", "build_plan", "plan_instruction",
    "verify_report", "should_verify", "is_verified",
    "should_summarize", "build_summary",
]