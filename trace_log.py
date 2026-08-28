# -*- coding: utf-8 -*-
"""trace_log：长任务（小说一条龙编排）轨迹记忆（借鉴 Prime-Agent 的 Continual Harness 范式之 D 项）。

设计原则：
- 把每次「成功跑通」的长任务路径（主题/平台/类型/目标字数/选定切入点/各阶段耗时/重试次数/成稿字数）
  沉淀为结构化轨迹，存到独立文件 task_traces.json（数据日志，不与热修11 的 curated 经验库 harness_notes 混淆）。
- 下次同类任务（平台+类型+主题重叠）启动时，检索 Top-N 相似成功轨迹，拼成 few-shot 参考注入系统提示，
  让模型沿「已验证过的路径」走，少踩坑、少白烧钱。
- 与 harness_notes 的关系：轨迹是「跑通过的数据」，经验库是「人工/agent 提炼的操作铁律」。本模块提供
  auto_refine_harness（默认关闭，由 config.orch_trace_auto_refine 控制），仅在达到阈值时保守地向上游经验库
  写入去重笔记，实现「越多跑通→经验库越准」的闭环（见热修11 备忘录「未来可接任务轨迹自动 refine」）。
- 纯标准库、无 Qt 依赖，便于离线单测；所有读写全程 try 包裹，文件缺失/损坏静默降级，绝不拖垮主程序。
"""
import os
import re
import json
import uuid
import datetime


DEFAULT_DIR_NAME = "task_traces"


def _dir(cfg):
    if isinstance(cfg, dict) and cfg.get("task_trace_dir"):
        return cfg["task_trace_dir"]
    base = os.path.join(os.path.expanduser("~"), "Documents", "AgentDesktop")
    return os.path.join(base, DEFAULT_DIR_NAME)


def _path(cfg):
    return os.path.join(_dir(cfg), "task_traces.json")


def _ensure_dir(cfg):
    try:
        os.makedirs(_dir(cfg), exist_ok=True)
    except Exception:
        pass


def load_traces(cfg):
    """读取全部轨迹；文件缺失/损坏时返回空列表（绝不抛异常）。"""
    p = _path(cfg)
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("traces"), list):
                return d["traces"]
    except Exception:
        pass
    return []


def capture_trace(cfg, trace, max_keep=None):
    """追加一条轨迹；带 id/ts；超过 max_keep 时裁掉最旧。返回新记录 id。"""
    if not isinstance(cfg, dict):
        cfg = {}
    max_keep = max_keep or cfg.get("orch_trace_max", 200)
    _ensure_dir(cfg)
    traces = load_traces(cfg)
    rec = dict(trace) if isinstance(trace, dict) else {}
    rec["id"] = rec.get("id") or (datetime.datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8])
    rec["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    traces.append(rec)
    if max_keep and len(traces) > max_keep:
        traces = traces[-max_keep:]
    try:
        with open(_path(cfg), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "traces": traces}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return rec.get("id")


def _kw(text):
    """抽取可比较的 token：ascii 词 + 中文 bigram（轻量相似度信号）。"""
    if not text:
        return set()
    text = str(text).lower()
    toks = set(re.findall(r"[a-z0-9]{2,}", text))
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        for j in range(len(seg) - 1):
            toks.add(seg[j:j + 2])
    return toks


def retrieve_similar(cfg, topic="", platform="", length_type="", top_n=2, only_success=True):
    """按 平台(3) + 类型(2) + 主题bigram重叠(≤5) 打分，返回 Top-N 相似成功轨迹。"""
    traces = load_traces(cfg)
    if only_success:
        traces = [t for t in traces if t.get("outcome") == "success"]
    if not traces:
        return []
    qkw = _kw(topic)
    scored = []
    for t in traces:
        # 平台是「同类任务」的硬门槛：跨平台的小说创作（网文 vs 短视频脚本）差异巨大，
        # 不注入无关平台的轨迹，避免带偏风格/结构。
        if platform and t.get("platform") != platform:
            continue
        score = 0
        if length_type and t.get("length_type") == length_type:
            score += 2
        tkw = _kw(t.get("topic", ""))
        # 主题相似需 ≥2 个 shared bigram 才算数（单字误匹配视为噪音，不计分）
        if qkw and tkw:
            overlap = len(qkw & tkw)
            if overlap >= 2:
                score += min(overlap, 5)
        if score <= 0:
            continue
        scored.append((score, t))
    scored.sort(key=lambda x: (x[0], x[1].get("ts", "")), reverse=True)
    return [t for _, t in scored[:top_n]]


def build_fewshot_for(cfg, topic, platform, length_type, top_n=2):
    """拼成可注入系统提示的 few-shot 参考文本；无相似轨迹返回空串。"""
    sim = retrieve_similar(cfg, topic, platform, length_type, top_n=top_n)
    if not sim:
        return ""
    lines = ["【历史成功轨迹参考（同类任务已跑通，可作路径参考，不照搬结论）】"]
    for idx, t in enumerate(sim, 1):
        bits = []
        bits.append(f"主题「{t.get('topic', '')}」")
        bits.append(f"平台{t.get('platform', '')}/{t.get('length_type', '')}")
        if t.get("target_words"):
            bits.append(f"目标{t.get('target_words')}字")
        cd = (t.get("chosen_direction") or "")[:120]
        if cd:
            bits.append(f"切入点：{cd}")
        sd = t.get("stage_durations") or {}
        if sd:
            dur = " ".join(f"{k}{v}s" for k, v in sd.items())
            bits.append(f"各阶段耗时[{dur}]")
        if t.get("retry_count"):
            bits.append(f"重试{t.get('retry_count')}次后成功")
        if t.get("final_words"):
            bits.append(f"成稿{t.get('final_words')}字")
        lines.append(f"{idx}. " + "；".join(bits))
    return "\n".join(lines)


def auto_refine_harness(cfg, min_count=3):
    """保守自动提炼（默认关闭）：对 (平台,类型) 组合，成功轨迹达 min_count 时，
    向 harness_notes 写入/更新一条去重『常见成功路径』笔记（按 id 去重，不重复创建）。
    返回被创建的 note id 列表。需 config.orch_trace_auto_refine=True 才会被调用方触发。
    """
    traces = [t for t in load_traces(cfg) if t.get("outcome") == "success"]
    groups = {}
    for t in traces:
        key = (t.get("platform", ""), t.get("length_type", ""))
        groups.setdefault(key, []).append(t)
    created = []
    for (plat, ltype), items in groups.items():
        if len(items) < min_count:
            continue
        n = len(items)
        dirs = [t.get("chosen_direction", "")[:60] for t in items if t.get("chosen_direction")]
        dirs_sample = "；".join(dict.fromkeys(d for d in dirs if d))[:200]
        note_id = f"trace_pattern__{plat}__{ltype}"
        title = f"轨迹经验：{plat} 的 {ltype} 已成功跑通 {n} 次"
        body = (f"基于 {n} 次成功轨迹自动提炼（task_traces）。该平台/类型常见切入点参考：{dirs_sample}。"
                f"新一轮同类任务已作为 few-shot 轨迹注入参考，可优先沿用验证过的方向。")
        try:
            import harness
            ok, _ = harness.upsert_note(
                cfg, note_id, title, body,
                tags=["轨迹记忆", "auto_refine", plat or "", ltype or ""],
                source="trace_auto_refine")
            if ok:
                created.append(note_id)
        except Exception:
            pass
    return created


def prune(cfg, max_keep=None):
    """裁剪到 max_keep 条（保留最新）。"""
    if not isinstance(cfg, dict):
        cfg = {}
    max_keep = max_keep or cfg.get("orch_trace_max", 200)
    traces = load_traces(cfg)
    if len(traces) > max_keep:
        traces = traces[-max_keep:]
        _ensure_dir(cfg)
        try:
            with open(_path(cfg), "w", encoding="utf-8") as f:
                json.dump({"version": 1, "traces": traces}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


# ==================== v4.102 fix12：Agent 任务级轨迹写回 ====================
# 与 harness refine 的边界（互补，不重叠）：
#   harness refine    → 技能级经验「这个技能该怎么改」，进待审核队列，人工通过才生效
#   任务轨迹（本节）  → 任务级经验「这类任务怎么跑 / 踩过什么坑」，直接落盘可检索
# 一句话：refine 优化「工具」，trajectory 优化「决策」。
#
# 存储刻意用**独立文件 agent_traces.json**，不与小说编排的 task_traces.json 共享
# 200 条配额——否则日常 agent 对话会迅速把小说编排的成功轨迹挤掉。

AGENT_TRACES_FILE = "agent_traces.json"
AGENT_TRACE_MAX = 200


def _agent_path(cfg):
    return os.path.join(_dir(cfg), AGENT_TRACES_FILE)


def load_agent_traces(cfg):
    """读取 Agent 任务轨迹；文件缺失/损坏返回空列表（绝不抛异常）。"""
    p = _agent_path(cfg)
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("traces"), list):
                return d["traces"]
    except Exception:
        pass
    return []


def append_task_trajectory(cfg, task, outcome="success", pattern=None, pitfall=None,
                           tools=None, tokens=None, steps=None, duration_s=None,
                           model=None, max_keep=None):
    """Agent 任务退出时统一写回一条「任务级」轨迹。

    由 agent.py 在所有退出路径（正常完成 / token 熔断 / 用户停止 / 超时 / 步数耗尽）
    统一调用，实现「踩过的坑下次不再踩」的闭环。

    入参：
      task       : 任务目标（goal 摘要）
      outcome    : success | token_budget | stopped | timeout | max_steps | error
      pattern    : 成功模式（值得复用的做法）
      pitfall    : 踩坑（下次规避）
      tools      : 本轮实际调用过的工具名列表
      tokens     : 本轮累计 token
      steps      : 本轮步数
      duration_s : 本轮耗时（秒）
      model      : 实际使用的模型名
    返回：写入的 trace id（失败返回 ""）。
    """
    if not isinstance(cfg, dict):
        cfg = {}
    max_keep = max_keep or cfg.get("agent_trace_max", AGENT_TRACE_MAX)
    _ensure_dir(cfg)
    traces = load_agent_traces(cfg)
    rec = {
        "id": (datetime.datetime.now().strftime("%Y%m%d_%H%M%S_")
               + uuid.uuid4().hex[:8]),
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "task": str(task or "")[:200],
        "outcome": outcome,
        "success_pattern": (str(pattern)[:400] if pattern else "") or None,
        "pitfall": (str(pitfall)[:400] if pitfall else "") or None,
        "tools_used": [str(t) for t in (tools or [])][:30],
        "tokens": int(tokens or 0),
        "steps": int(steps or 0),
        "duration_s": round(float(duration_s or 0), 1),
        "model": str(model or ""),
        "source": "agent_task",
    }
    traces.append(rec)
    if max_keep and len(traces) > max_keep:
        traces = traces[-max_keep:]
    try:
        with open(_agent_path(cfg), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "traces": traces}, f,
                      ensure_ascii=False, indent=2)
    except Exception:
        pass
    return rec.get("id")


def retrieve_agent_traces(cfg, task="", top_n=2, outcomes=None):
    """按任务关键词相似度检索同类 Agent 任务轨迹（复用 _kw 的 bigram 打分）。

    outcomes：限定结局（如 ["success", "token_budget"]），为空则不限。
    返回 Top-N 轨迹列表（最新优先）。
    """
    traces = load_agent_traces(cfg)
    if not traces:
        return []
    if outcomes:
        traces = [t for t in traces if t.get("outcome") in outcomes]
    qkw = _kw(task)
    scored = []
    for t in traces:
        tkw = _kw(t.get("task", ""))
        score = 0
        if qkw and tkw:
            overlap = len(qkw & tkw)
            # ≥2 个 shared bigram 才算同类（单词命中视为噪音，避免误注入）
            if overlap >= 2:
                score += min(overlap, 5)
        if score <= 0:
            continue
        scored.append((score, t))
    scored.sort(key=lambda x: (x[0], x[1].get("ts", "")), reverse=True)
    return [t for _, t in scored[:top_n]]


def build_agent_fewshot(cfg, task, top_n=2, max_chars=600):
    """把同类 Agent 任务经验拼成可注入系统提示的「避坑提醒」。

    只取 pitfall / success_pattern 的简短摘要，控制长度避免反向烧 token。
    无相似轨迹或无可提炼经验时返回空串（调用方可安全无条件拼接）。
    """
    sim = retrieve_agent_traces(cfg, task, top_n=top_n,
                                outcomes=["success", "token_budget", "max_steps"])
    if not sim:
        return ""
    lines = ["【同类任务历史经验（仅供避坑参考，不要照搬结论）】"]
    for idx, t in enumerate(sim, 1):
        bits = [f"任务「{t.get('task', '')[:40]}」（{t.get('outcome', '')}）"]
        pf = (t.get("pitfall") or "").strip()
        sp = (t.get("success_pattern") or "").strip()
        if pf:
            bits.append(f"踩坑：{pf[:150]}")
        if sp:
            bits.append(f"有效做法：{sp[:150]}")
        lines.append(f"{idx}. " + "；".join(bits))
    out = "\n".join(lines)
    return out[:max_chars]
