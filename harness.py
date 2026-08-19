# -*- coding: utf-8 -*-
"""harness_notes：可自我 refine + 版本回滚的操作经验库（借鉴 Prime-Agent 的 Continual Harness 范式）。

设计原则：
- 基础系统提示（self.cfg["system_prompt"]）【锁死不可改】，本模块只提供一层 supplemental 经验状态。
- 把「图生视频流程」「Agnes 口播必填」等易忘操作铁律沉淀为：可检索、可版本化、可回滚的条目，
  注入到系统提示的补充节，让模型每次跑相关任务前都先参考，避免重复踩坑。
- 不暴露任何底层命令给非技术用户；refine（upsert）/回滚由代码侧按需触发（本期 v1 人工灌入，
  未来可由 remember/任务轨迹提炼调用，仍走 agent 后台，不暴露命令）。

与现有 remember 工具的区别：remember 存的是「用户事实/长期记忆」；harness_notes 存的是
「操作要领/流程铁律」（偏工具使用层面）。二者互补，互不冲突。
"""
import os
import json
import copy
import datetime


# v1 默认经验库：把历史热修沉淀的操作铁律固化进来（人工灌入，带版本快照可回滚）
DEFAULT_HARNESS = {
    "version": 1,
    "updated": "2026-08-12",
    "entries": [
        {
            "id": "video_image_to_video_flow",
            "title": "图生视频 / 文生视频：一步调用，工具内部自动提交+轮询+下载",
            "body": "调用 video_gen 时，【只需调用一次】即完成：画面描述填 prompt；源图填 image（本地图片路径会自动转 base64 注入，URL/data URI 原样透传，无需图床）；需要人物说话/口播/带货时必填 dialogue（Agnes 内置中文口播，自动合成人声并对口型，无需后期配音）。工具内部已做『异步提交→轮询至完成→下载到工作区』全流程，禁止自己写轮询循环、禁止改走 edge-tts 后期配音。",
            "tags": ["video_gen", "agnes", "图生视频"],
            "created": "2026-08-12",
            "updated": "2026-08-12",
            "source": "hotfix10",
        },
        {
            "id": "agnes_video_voiceover",
            "title": "Agnes 视频口播：dialogue 必填才会出声",
            "body": "Agnes 视频模型(agnes-video-v2.0)支持内置中文口播。只有把台词填进 dialogue 参数，视频才会带真人中文语音+对口型；不填则纯画面无声（这不是 Agnes 的缺陷，是没传台词）。做人物说话/口播/带货视频，务必先写好中文台词再调 video_gen。",
            "tags": ["agnes", "video_gen", "口播", "dialogue"],
            "created": "2026-08-12",
            "updated": "2026-08-12",
            "source": "hotfix9",
        },
        {
            "id": "video_default_portrait",
            "title": "视频默认竖版（抖音/视频号/小红书）",
            "body": "video_gen 的 aspect 默认竖版 portrait(768x1152)。做竖屏平台内容时保持默认，不要传 landscape。横版仅用于特殊宽屏需求。",
            "tags": ["video_gen", "aspect"],
            "created": "2026-08-12",
            "updated": "2026-08-12",
            "source": "hotfix7",
        },
        {
            "id": "intent_route_video_priority",
            "title": "意图路由：说'生成视频'优先 video_gen，不要调 sys_info",
            "body": "用户原话含『视频/做视频/生成视频』等，且未明确要求自检能力时，第一步直接调 video_gen，禁止调 sys_info 死循环。点名 Agnes/图生视频也直接路由 video_gen。",
            "tags": ["意图路由", "video_gen"],
            "created": "2026-08-12",
            "updated": "2026-08-12",
            "source": "hotfix6",
        },
        {
            "id": "writing_short_story_ending",
            "title": "短篇创作必须完结（开头→发展→高潮→结局）",
            "body": "写短篇时，故事必须走到『结局』才收尾，禁止停在中段/烂尾。循环写到目标字数约 60% 后应主动切到『写完整结局』，且全篇只发一次结局，不要重复结尾。",
            "tags": ["小说", "短篇", "完结"],
            "created": "2026-08-12",
            "updated": "2026-08-12",
            "source": "hotfix5",
        },
        {
            "id": "vision_backend_autostart",
            "title": "识图后端随 APP 自启（无需手动拉）",
            "body": "analyze_image 依赖的 free-api-gateway 已配置为随 APP 启动自动拉起（config gateway_autostart=true）。若仍报连接被拒(WinError 10061)，请提示用户确认该后端是否启动，而非断定功能不可用。",
            "tags": ["识图", "gateway", "analyze_image"],
            "created": "2026-08-12",
            "updated": "2026-08-12",
            "source": "hotfix10",
        },
    ],
}


def _default_path(cfg):
    if isinstance(cfg, dict) and cfg.get("harness_notes_path"):
        return cfg["harness_notes_path"]
    return os.path.join(os.path.expanduser("~"), "Documents", "AgentDesktop", "harness_notes.json")


def _history_path(cfg):
    return _default_path(cfg) + ".history.json"


def ensure_harness_notes(cfg):
    """首次运行创建默认经验库（v1）。已存在则不动。"""
    p = _default_path(cfg)
    if os.path.exists(p):
        return
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(copy.deepcopy(DEFAULT_HARNESS), f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_harness_notes(cfg):
    """读取经验库；文件缺失/损坏时优雅降级：尝试确保默认，再失败则返回空结构（绝不抛异常拖垮主程序）。"""
    p = _default_path(cfg)
    try:
        if not os.path.exists(p):
            ensure_harness_notes(cfg)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "entries" in data:
                return data
    except Exception:
        pass
    return {"version": 0, "entries": []}


def _load_history(cfg):
    hp = _history_path(cfg)
    if os.path.exists(hp):
        try:
            with open(hp, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_harness_notes(cfg, data, snapshot=True):
    """写回经验库；snapshot=True 时把旧版本推入 history 链以实现回滚（最多保留最近 10 个快照）。"""
    p = _default_path(cfg)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if snapshot and os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    old = json.load(f)
                hist = _load_history(cfg)
                hist.append(old)
                hist = hist[-10:]
                with open(_history_path(cfg), "w", encoding="utf-8") as f:
                    json.dump(hist, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def upsert_note(cfg, id_, title, body, tags=None, source="agent_refine"):
    """新增或更新一条经验（refine 核心）。返回 (ok, version)。"""
    if not id_ or not title or not body:
        return False, 0
    data = load_harness_notes(cfg)
    today = datetime.date.today().isoformat()
    entries = data.setdefault("entries", [])
    found = None
    for e in entries:
        if e.get("id") == id_:
            found = e
            break
    if found:
        found["title"] = title
        found["body"] = body
        if tags:
            found["tags"] = tags
        found["updated"] = today
        if source:
            found["source"] = source
    else:
        entries.append({
            "id": id_, "title": title, "body": body,
            "tags": tags or [], "created": today, "updated": today,
            "source": source,
        })
    data["version"] = data.get("version", 0) + 1
    data["updated"] = today
    ok = save_harness_notes(cfg, data)
    return ok, data.get("version", 0)


def rollback_harness(cfg, version=None):
    """回滚到某个历史快照。version=None 时回滚到上一个快照。"""
    hist = _load_history(cfg)
    if not hist:
        return False
    if version is None:
        target = hist[-1]
    else:
        target = None
        for h in hist:
            if h.get("version") == version:
                target = h
                break
        if target is None:
            return False
    return save_harness_notes(cfg, target, snapshot=True)


def harness_section_text(cfg, compact=False):
    """拼成注入系统提示的补充节文本（操作经验库）。无条目返回空串。

    v4.87 省 token：
    compact=True（系统提示常驻，L1 概览）——只列经验标题，不展开 body，
    让模型知道「有哪些沉淀经验」但不每轮平铺全部铁律全文；
    compact=False（诊断/按需展开用）——保留原完整 title：body 全文。
    """
    data = load_harness_notes(cfg)
    entries = data.get("entries", [])
    if not entries:
        return ""
    if compact:
        lines = ["【操作经验库 harness_notes（调用相关工具前若命中可自动展开完整铁律）】"]
        lines.append("已沉淀操作经验标题：")
        for e in entries:
            lines.append(f"- {e.get('title', '')}")
        return "\n".join(lines)
    lines = ["【操作经验库 harness_notes（可自我沉淀、出错可回滚）】"]
    lines.append("以下是从历史任务中沉淀的操作铁律，调用相关工具前请先参考，避免重复踩坑：")
    for e in entries:
        lines.append(f"- {e.get('title', '')}：{e.get('body', '')}")
    return "\n".join(lines)
