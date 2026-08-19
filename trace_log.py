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
