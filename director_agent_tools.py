"""v4.106 对话框导演工具：让聊天 Agent 能指挥导演台（最小闭环）。

设计：
- 本模块 Qt-free，只做参数校验 + 经 dispatcher 跨线程调导演台（dispatcher 由
  director_panel.install_agent_bridge 在 UI 线程注册）。
- 开拍仍走导演台表单（用户点「开始导演」）；这里只覆盖：
  查状态 / 改分镜 / 重生成关键帧 / 重生成三视图 / 合成成片 /
  抽取关键道具场景资产(clues) / 改道具 / 版本回滚(含 clue)。
- 风险档：director_status=READ；其余=WRITE_LOCAL（与 image_gen/video_gen 同档，
  生成物写本地工作区）。
"""
import json

from tools import register_tool
from risk import RiskClass

_DISPATCH = None  # fn(cmd: dict, timeout: int) -> dict，由 director_panel 注册


def set_dispatcher(fn):
    global _DISPATCH
    _DISPATCH = fn


def _run(cmd, timeout=1200):
    if _DISPATCH is None:
        return {"ok": False, "msg": "导演台尚未加载（进入导演台页后才能用对话指挥）。"}
    try:
        return _DISPATCH(cmd, timeout)
    except Exception as e:
        return {"ok": False, "msg": f"导演台指令派发失败：{e}"}


def _fmt(res):
    return json.dumps(res, ensure_ascii=False, indent=1)


@register_tool("director_status", risk=RiskClass.READ)
def _h_director_status(cfg, app_dir, args, progress=None):
    """查询导演台当前项目状态：第几步、每个分镜/关键帧/片段状态、角色列表。"""
    return (_fmt(_run({"action": "status"}, timeout=20)), [], None)


@register_tool("director_revise_clip", risk=RiskClass.WRITE_LOCAL)
def _h_director_revise_clip(cfg, app_dir, args, progress=None):
    """按修改意见重生成某一个分镜视频片段（等价于导演台「修改这镜」按钮）。"""
    idx = args.get("idx")
    note = (args.get("note") or "").strip()
    if not idx:
        return ("缺少分镜号 idx（从 1 数）。可先调 director_status 查看各镜状态。", [], None)
    res = _run({"action": "revise_clip", "idx": idx, "note": note,
                "replace": bool(args.get("replace"))},
               timeout=int(args.get("timeout") or 1500))
    return (_fmt(res), [], None)


@register_tool("director_revise_keyframe", risk=RiskClass.WRITE_LOCAL)
def _h_director_revise_keyframe(cfg, app_dir, args, progress=None):
    """按修改意见只重生成某一个分镜的关键帧图片（其他镜/场景图不动）。"""
    idx = args.get("idx")
    note = (args.get("note") or "").strip()
    if not idx:
        return ("缺少分镜号 idx（从 1 数）。可先调 director_status 查看各镜状态。", [], None)
    res = _run({"action": "revise_keyframe", "idx": idx, "note": note},
               timeout=int(args.get("timeout") or 600))
    return (_fmt(res), [], None)


@register_tool("director_revise_character", risk=RiskClass.WRITE_LOCAL)
def _h_director_revise_character(cfg, app_dir, args, progress=None):
    """按修改意见只重生成某一个角色的三视图，并同步刷新角色锁定描述。"""
    idx = args.get("idx")
    note = (args.get("note") or "").strip()
    if idx is None:
        return ("缺少角色序号 idx（从 1 数）。可先调 director_status 查看角色列表。", [], None)
    res = _run({"action": "revise_character", "idx": idx, "note": note},
               timeout=int(args.get("timeout") or 600))
    return (_fmt(res), [], None)


@register_tool("director_merge", risk=RiskClass.WRITE_LOCAL)
def _h_director_merge(cfg, app_dir, args, progress=None):
    """把已生成的分镜片段合成为成片（等价于导演台「合成成片」按钮）。"""
    res = _run({"action": "merge"}, timeout=int(args.get("timeout") or 1500))
    return (_fmt(res), [], None)


@register_tool("director_rollback", risk=RiskClass.WRITE_LOCAL)
def _h_director_rollback(cfg, app_dir, args, progress=None):
    """把某一镜/关键帧/角色/道具 回滚到上一版本（等价于 ArcReel 的版本回滚）。
    kind=clip|keyframe|character|clue；idx 从 1 数；version 默认 -1（上一版）。"""
    kind = (args.get("kind") or "clip").strip()
    if kind not in ("clip", "keyframe", "character", "clue"):
        return ("kind 必须是 clip / keyframe / character / clue 之一。", [], None)
    idx = args.get("idx")
    if idx is None:
        return ("缺少序号 idx（从 1 数）。可先调 director_status 查看各镜/角色/道具状态。", [], None)
    res = _run({"action": f"rollback_{kind}", "idx": idx,
                "version": int(args.get("version", -1))},
               timeout=int(args.get("timeout") or 60))
    return (_fmt(res), [], None)


@register_tool("director_gen_clues", risk=RiskClass.WRITE_LOCAL)
def _h_director_gen_clues(cfg, app_dir, args, progress=None):
    """抽取并生成跨镜复用的「关键道具 / 场景资产」参考图（ArcReel 的 P0 clues 机制）。
    抽取后画面里这些道具/资产会被锁定成逐镜一致。note 可填额外风格意见（可选）。"""
    note = (args.get("note") or "").strip()
    res = _run({"action": "gen_clues", "note": note},
               timeout=int(args.get("timeout") or 1200))
    return (_fmt(res), [], None)


@register_tool("director_revise_clue", risk=RiskClass.WRITE_LOCAL)
def _h_director_revise_clue(cfg, app_dir, args, progress=None):
    """按修改意见重生成某一件道具/场景资产的参考图（不影响其他件）。
    idx 从 1 数（可先调 director_status 看 clues 列表）。"""
    idx = args.get("idx")
    note = (args.get("note") or "").strip()
    if not idx:
        return ("缺少道具序号 idx（从 1 数）。可先调 director_status 查看 clues 列表。", [], None)
    res = _run({"action": "revise_clue", "idx": idx, "note": note},
               timeout=int(args.get("timeout") or 600))
    return (_fmt(res), [], None)
