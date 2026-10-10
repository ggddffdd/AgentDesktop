# -*- coding: utf-8 -*-
"""DeepSeek 桌面助手 — 工具执行模块"""

import sys
import os
import html as html_mod
import re
import json
import time
import subprocess
# v4.125 M-14：windowed 打包下调子进程不闪黑窗
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
import shutil
import logging
import inspect
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from datetime import datetime

import glob
from config import (APP_DIR, TOOL_READ_LIMIT, TOOL_RESULT_LIMIT, PRODUCTS_DIR,
                    USER_DATA_DIR, WORKSPACE_DIR)
import search as search_mod
from system_control_tools import SYSTEM_CONTROL_TOOL_TABLE
from software_control_tools import SOFTWARE_CONTROL_TOOL_TABLE
from browser_control_tools import BROWSER_CONTROL_TOOL_TABLE
from skill_installer_tools import SKILL_INSTALLER_TOOL_TABLE
from memory_store import append_memory, search_memory as _search_memory
from structured_logger import get_logger
try:
    import route_log   # v4.110：旁路埋点（路由/用量/技能），只写不读
except Exception:      # 旁路模块缺失绝不能拖垮工具模块（冻结环境 import 失败必炸）
    route_log = None
# chart_generator（matplotlib 重库，含查中文字体 ~0.9s）v4.122.1 改为延迟加载：
# 不再在模块级 import+实例化，首次真正生成图表时才初始化，避免冷启动连坐 matplotlib。
from context_manager import get_context_manager
from database_tools import DatabaseTools
db_tools = DatabaseTools()
# v4.220：工具调用统一契约（结构化返回 + 脱敏 + 影响范围）
from tool_contract import (ToolResult, _mask_sensitive, _mask_recursive,
                           compute_impact_scope,
                           validate_for_tool,  # v4.223：统一参数校验入口
                           apply_post_verification)  # v4.224：执行后验证

# v4.227（P2-2）：执行后验证的首批硬验 + 全量分档登记。
# 必须在这里 import —— register_verifier / register_verification_tier 都是
# **导入期**副作用：模块不被导入，注册就不会发生，验证链等于空转。
# 放在 tool_contract 之后（本模块要从它拿注册函数），且在 exec_tool 定义之前。
import tool_verifiers_227  # noqa: F401,E402


# v4.155 fix2：图生视频轮询超时上限（默认 240s，< Agent 回合上限 445s，刻意留余量）。
# 经 XIAOCHOU_VIDEO_TIMEOUT 可覆盖；替换原先写死的 120（原本只管 submit，轮询仍落 1800 默认）。
VIDEO_POLL_TIMEOUT = int(os.getenv("XIAOCHOU_VIDEO_TIMEOUT", "240"))


def _trunc_args(args, limit=500):
    """把工具入参安全截断到 limit 字符，避免超长入参撑爆 agent_logs 表（v4.155 fix1）。"""
    try:
        s = json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args
    except Exception:
        s = str(args)
    s = s or ""
    if len(s) > limit:
        s = s[:limit] + f"...(截断，共{len(s)}字符)"
    return s


_chart_gen = None


def _get_chart_gen():
    """懒加载 ChartGenerator 单例（ChartGenerator.__init__ 会查中文字体，很贵）。"""
    global _chart_gen
    if _chart_gen is None:
        from chart_generator import ChartGenerator
        _chart_gen = ChartGenerator()
    return _chart_gen

log = logging.getLogger("dsdesktop")


# ============ 交付物标准化工具 ============

def _guess_kind(path):
    """根据文件扩展名猜测交付物类型"""
    ext = os.path.splitext(str(path))[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
        return "image"
    if ext in (".mp4", ".avi", ".mov", ".mkv", ".webm"):
        return "video"
    return "file"


def _safe_relpath(path, base):
    """v4.108 H-10：跨盘符安全的 relpath——os.path.relpath 在不同盘（C:↔D:）会抛
    ValueError('path is on mount ... start on mount ...')，冻结 exe 装在非 C 盘时
    产物路径跨盘即崩。ValueError 时回退返回绝对路径（仍可展示/定位文件）。"""
    try:
        return os.path.relpath(path, base).replace("\\", "/")
    except ValueError:
        return os.path.abspath(path).replace("\\", "/")


# ---- v4.129：产物分层落盘 ----
# 最近一次 exec_tool 的 cfg。落盘函数（_save_gen_image / _save_gen_video 等）签名里
# 没有 cfg，历史上是模块级直接用 PRODUCTS_DIR；这里缓存一份让它们也能读到
# products_layout 开关，避免给十几个调用点逐个加参数。
_LAST_CFG = {}

# product_layout 导入失败时的兜底目录名（只覆盖本机实际用到的几种）
_KIND_DIR_FALLBACK = {"image": "图片", "video": "视频", "screenshot": "截图",
                      "code": "脚本", "md": "文档", "cover": "封面图"}


def _products_dir(cfg=None, kind=None, project=None):
    """v4.129：算产物落盘目录（必要时创建），永不抛异常。

    dated（默认）→ 产物/YYYY-MM-DD/<项目>/<类型>/；flat → 产物/<类型>/（旧行为）。
    """
    kd = _KIND_DIR_FALLBACK.get(str(kind or "").lower(), "其他")
    try:
        import product_layout
        c = cfg or _LAST_CFG or {}
        layout = "dated" if product_layout.is_dated_layout_enabled(c) else "flat"
        return product_layout.product_dir(PRODUCTS_DIR, kind=kind,
                                          project=project, layout=layout)
    except Exception:
        d = os.path.join(PRODUCTS_DIR, kd)
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d


def _normalize_deliverable(d, app_dir):
    """将交付物标准化为 (rel_path, kind, name) 三元组。
    支持字符串（路径）或元组两种输入格式。
    """
    if isinstance(d, tuple):
        return d
    path_str = str(d)
    rel = _safe_relpath(path_str, app_dir)
    return (rel, _guess_kind(path_str), os.path.basename(path_str))


# ============ exec_tool 统一路由 ============

# ============ v4.31 统一工具注册中心 ============
# 每个工具一处声明（handler + 危险等级），exec_tool 优先查此 dispatch。
# schema 仍在 config.TOOL_DEFS（供 LLM），config.get_all_tools 启动时一致性校验防漏。
TOOL_REGISTRY = {}  # name -> {"handler": fn, "dangerous": bool}

def register_tool(name, dangerous=False, risk=None):
    """装饰器：注册工具 handler。handler 签名 (cfg, app_dir, args) -> (result, deliverables, schedule)。

    risk: 可选，风险等级（RiskClass 或 'read'/'write_local'/'exec'/'external' 字符串）。
          声明后立即写入 risk.RISK_MAP，作为权限引擎的单一事实来源。
          不声明则靠 risk.classify 前缀兜底；若兜底为 EXTERNAL，启动时 config.get_all_tools 会 error 告警。
    dangerous: 历史遗留死参数（从未被任何地方读取），已被 risk 取代，保留仅为向后兼容。
    """
    def deco(fn):
        TOOL_REGISTRY[name] = {"handler": fn, "dangerous": dangerous}
        if risk is not None:
            try:
                from risk import RiskClass as _RC, RISK_MAP as _RM
                _rc = risk if isinstance(risk, _RC) else _RC(str(risk).lower())
                _RM[name] = _rc
            except Exception as _e:
                log.error("工具 %s 风险等级声明无效: %s", name, _e)
        return fn
    return deco

def _register_extension_tools():
    """把 4 个扩展模块的 *_TOOL_TABLE 注册进 registry（它们已是字典表，handler 签名统一）。
    P2-5 修：原实现一个 try 包全部——任一模块 import 失败或任一 handler 签名不可探测
    （inspect 对 C 函数/partial 抛 TypeError），四张表**全部**注册失败（软件控制/
    浏览器/系统/技能安装集体消失，只剩一条 warning）。现按三层隔离：坏一个模块
    只丢一张表、坏一个 handler 只跳一项、签名不可探测按『不透传扩展参数』兜底。"""
    _tables = [
        ("system_control_tools", "SYSTEM_CONTROL_TOOL_TABLE"),
        ("software_control_tools", "SOFTWARE_CONTROL_TOOL_TABLE"),
        ("browser_control_tools", "BROWSER_CONTROL_TOOL_TABLE"),
        ("skill_installer_tools", "SKILL_INSTALLER_TOOL_TABLE"),
    ]
    for _mod_name, _table_name in _tables:
        try:
            _table = getattr(__import__(_mod_name), _table_name)
        except Exception as _e:
            log.warning("扩展模块 %s 加载失败（跳过该表，不影响其余表）: %s", _mod_name, _e)
            continue
        for _name, _handler in _table.items():
            _danger = _name.startswith("browser_") or _name == "skill_install"
            def _wrap(h=_handler):
                import inspect as _inspect
                try:
                    _hparams = _inspect.signature(h).parameters
                except (TypeError, ValueError):
                    _hparams = None  # 签名不可探测：按不接收扩展参数处理（调用时兜底）
                def _w(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
                    # ②-B.2：把进度/停止信号透传给声明了对应参数的底层 handler
                    # （仅 software_control 的 tool_app_* 需要；其余扩展 handler 不接收，避免 TypeError）
                    _kw = {"cfg": cfg, "app_dir": app_dir, "args": args}
                    if _hparams is not None:
                        if "progress" in _hparams:
                            _kw["progress"] = progress
                        if "stop_event" in _hparams:
                            _kw["stop_event"] = stop_event
                        if "should_stop" in _hparams:
                            _kw["should_stop"] = should_stop
                    return h(**_kw)
                return _w
            try:
                TOOL_REGISTRY[_name] = {"handler": _wrap(), "dangerous": _danger}
            except Exception as _e:
                log.warning("扩展工具 %s 注册失败（跳过该项）: %s", _name, _e)

_register_extension_tools()


# ============ v4.50 权限：风险分类（已迁移到 risk.py，借鉴 andrewyng/openworker）============
# 等级 / 风险档定义见 risk.py（RiskClass + RISK_MAP + classify + tier_of + grouped_tools）。
# 这里仅做向后兼容再导出，避免散落各处的 tools.tier_of / tools.TOOL_TIER 引用失效。
from risk import RiskClass, RISK_MAP, classify, tier_of, grouped_tools


def __getattr__(name):
    """模块级惰性属性（PEP 562）——只用于向后兼容 `tools.TOOL_TIER`。

    v4.171.0：`TOOL_TIER` 原来是**导入时算好的一份 dict 快照**
    （`{n: tier_of(n) for n in RISK_MAP}`）。但 `RISK_MAP` 是可以被
    `@tool(risk=...)` **动态写入**的（见本文件 `_register_extension_tools`），
    所以快照必然与真实策略**失同步** —— 又是一份"第二来源"，
    正是本轮"工具策略表合并"要根治的东西（跟 `_TIER_OVERRIDE` 同一类隐患）。

    改成按需现算：名字还在、语义不变（name -> 等级），而且永远跟策略表一致。
    """
    if name == "TOOL_TIER":
        from risk import RISK_MAP as _RM, tier_of as _to
        return {n: _to(n) for n in _RM}
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Agnes 2.5 生视频参考图硬上限（实测传 6 张报 400）—— v4.127 多参考图截断用
MAX_REF_IMAGES = 5


# === 核心 25 工具注册到 registry（v4.31）===
# handler 签名统一 (cfg, app_dir, args) -> (result_str, deliverables, schedule)
@register_tool("web_search")
def _h_web_search(cfg, app_dir, args, progress=None):
    # v4.148.6：把 app_dir 透下去 —— 搜索链全空时的「浏览器搜索兜底」要靠它定位 profile
    return (tool_web_search(cfg, args.get("query", ""), app_dir=app_dir), [], None)

@register_tool("web_fetch")
def _h_web_fetch(cfg, app_dir, args, progress=None):
    return (tool_web_fetch(args.get("url", "")), [], None)

@register_tool("read_file")
def _h_read_file(cfg, app_dir, args, progress=None):
    return (tool_read_file(app_dir, args.get("path", ""),
                           offset=args.get("offset", 0), limit=args.get("limit")), [], None)


# ---- 军团调度三件套 v4.122：项目经理的「眼睛」----
# 数据来自 legion 模块内的运行时状态仓（执行器边跑边写），不落 legion.json。
_LEGION_NO_RUN = "当前没有军团执行记录（这三个工具只在「⚔️ Agent 军团」运行时有数据）。"


def _legion_run_safe(run_id=None):
    """取执行状态；取不到返回 None（调用方统一提示，不让工具抛异常打断 agent 循环）。"""
    try:
        import legion as _lg
        return _lg.get_run(run_id)
    except Exception as _e:
        log.warning("读取军团执行状态失败: %s", _e)
        return None


@register_tool("legion_list_outputs", risk="read")
def _h_legion_list_outputs(cfg, app_dir, args, progress=None):
    run = _legion_run_safe(args.get("run_id") or None)
    if not run:
        return (_LEGION_NO_RUN, [], None)
    preview = int(args.get("preview_chars") or 200)
    outs = run.get("outputs") or []
    if not outs:
        return (f"执行批次 {run['run_id']}（{run['project']}）目前还没有任何成员产出。"
                f"可能本波还没跑完，或成员全部失败 —— 用 legion_read_log 查执行过程。", [], None)
    lines = [f"执行批次 {run['run_id']} · 项目「{run['project']}」· 任务：{run['task'][:60]}"]
    lines.append(f"共 {len(outs)} 份产出：")
    for i, o in enumerate(outs, 1):
        txt = (o.get("text") or "").replace("\n", " ")
        pv = txt[:preview] + ("…" if len(txt) > preview else "")
        lines.append(
            f"\n[{i}] 第{o.get('wave')}波 · 第{o.get('attempt')}次 · {o.get('role')} "
            f"· {o.get('chars')}字\n    摘要：{pv or '（空）'}")
    lines.append("\n读全文请用 legion_get_output(role=『成员名』或 wave=波次序号)。")
    return ("\n".join(lines), [], None)


@register_tool("legion_get_output", risk="read")
def _h_legion_get_output(cfg, app_dir, args, progress=None):
    run = _legion_run_safe(args.get("run_id") or None)
    if not run:
        return (_LEGION_NO_RUN, [], None)
    max_chars = int(args.get("max_chars") or 6000)
    role_q = (args.get("role") or "").strip()
    wave_q = args.get("wave")
    outs = run.get("outputs") or []
    if not outs:
        return ("本次执行还没有任何成员产出，无法读取。", [], None)

    hits = []
    if role_q:
        hits = [o for o in outs if role_q in (o.get("role") or "")]
    elif wave_q is not None:
        try:
            hits = [o for o in outs if int(o.get("wave")) == int(wave_q)]
        except (TypeError, ValueError):
            hits = []
    else:
        hits = outs
    if not hits:
        avail = "、".join(sorted({o.get("role") or "?" for o in outs}))
        return (f"没有匹配到产出（查询条件 role={role_q!r} wave={wave_q!r}）。"
                f"现有成员：{avail}", [], None)

    parts = []
    for o in hits:
        txt = (o.get("text") or "").strip()
        if len(txt) > max_chars:
            txt = txt[:max_chars] + f"\n…(已截断，原文 {o.get('chars')} 字)"
        parts.append(f"### 第{o.get('wave')}波 · 第{o.get('attempt')}次 · {o.get('role')}\n\n"
                     f"{txt or '（该成员没有产出内容）'}")
    return ("\n\n---\n\n".join(parts), [], None)


@register_tool("legion_read_log", risk="read")
def _h_legion_read_log(cfg, app_dir, args, progress=None):
    run = _legion_run_safe(args.get("run_id") or None)
    if not run:
        return (_LEGION_NO_RUN, [], None)
    n = int(args.get("lines") or 80)
    log_lines = run.get("log") or []
    if not log_lines:
        return (f"执行批次 {run['run_id']} 暂无日志。", [], None)
    tail = log_lines[-n:]
    head = (f"执行批次 {run['run_id']} · 项目「{run['project']}」· 状态 {run['status']}\n"
            f"共 {len(log_lines)} 行，以下是最近 {len(tail)} 行：\n\n")
    return (head + "\n".join(tail), [], None)


@register_tool("legion_get_sources", risk="read")
def _h_legion_get_sources(cfg, app_dir, args, progress=None):
    """查本波成员的**抓取留痕**：搜了哪些词、抓了哪些页面、抓回来多少字。

    验收数据类产出（研究员/竞品分析师/选品官…）时**先查这个再看正文**：
    搜索词跑偏了，正文写得再像样也是编的。留痕里一条抓取都没有，
    说明成员压根没联网就下了结论 —— 直接判定 FAIL。
    """
    run = _legion_run_safe(args.get("run_id") or None)
    if not run:
        return (_LEGION_NO_RUN, [], None)
    try:
        import legion as _lg
    except Exception as e:
        return (f"军团模块加载失败：{e}", [], None)
    wave = args.get("wave")
    try:
        limit = int(args.get("limit") or 30)
    except (TypeError, ValueError):
        limit = 30
    blk = _lg.sources_block(run["run_id"], wave=wave, limit=limit)
    if not blk:
        where = f"第 {wave} 波" if wave is not None else "本次执行"
        return (f"执行批次 {run['run_id']} · {where}："
                "**没有任何抓取记录**（成员没调用 web_search / web_fetch 就交了结论）。\n"
                "这是编造数据的最强信号 —— 数据类角色出现这种情况请直接判定 FAIL，"
                "打回指令写明「必须先用 web_search 实搜，再把搜到的来源逐条标注」。",
                [], None)
    return (blk, [], None)


@register_tool("legion_find_asset", risk="read")
def _h_legion_find_asset(cfg, app_dir, args, progress=None):
    """查资产库：找已产出的图片/视频/剧本/数据（三视图、关键帧、成片等）。

    资产是以前跑军团/导演台留下的**存货**——同类任务直接复用别重造。
    """
    try:
        import asset_store
    except Exception as e:
        return (f"资产库模块加载失败：{e}", [], None)
    query = (args.get("query") or "").strip()
    kind = (args.get("kind") or "").strip() or None
    if kind and kind not in asset_store.KIND_LABELS:
        return ("kind 不合法。可选：" + "、".join(
                    f"{k}（{v}）" for k, v in asset_store.KIND_LABELS.items()),
                [], None)
    if not query:
        n, by = asset_store.asset_stats()
        if not n:
            return ("资产库是空的——还没有任何已登记的存货。", [], None)
        cat = asset_store.asset_catalog(top=20)
        return (cat or f"资产库共 {n} 件。", [], None)
    hits = asset_store.search_assets(query, kind=kind, top=int(args.get("top") or 10))
    if not hits:
        n, _by = asset_store.asset_stats()
        return (f"没搜到「{query}」相关资产（库里共 {n} 件）。"
                f"换关键词试试，或不带 query 看全库清单。", [], None)
    lines = [f"「{query}」命中 {len(hits)} 件资产："]
    for a in hits:
        k = asset_store.KIND_LABELS.get(a.get("kind"), a.get("kind") or "?")
        missing = "" if os.path.exists((a.get("path") or "")) else " ⚠️文件丢失"
        lines.append(f"\n- 【{k}】{a.get('name')}（{a.get('ts')}）{missing}\n"
                     f"  路径：{a.get('path')}\n"
                     f"  来源：{(a.get('project') or '')[:30]} / {(a.get('task') or '')[:60]}")
    return ("\n".join(lines), [], None)


@register_tool("legion_report_issue", risk="read")
def _h_legion_report_issue(cfg, app_dir, args, progress=None):
    """v4.131-F：成员上报通道 —— 发现上游数据不可信 / 缺依赖 / 指令矛盾时用。

    以前成员遇到「研究员给的市场规模查不到出处」只能硬编一个数字交差，
    或者空着被判未交付。现在上报即可：项目经理验收本波时**必须逐条回应**，
    大哥在授权弹窗里也看得见。上报完**继续做你确认得了的部分**，别停着等。
    """
    import legion
    kind = (args.get("kind") or "其他").strip()
    text = (args.get("text") or "").strip()
    upstream = (args.get("upstream") or "").strip()
    if not text:
        return ("上报失败：必须写 text —— 说清你质疑什么"
                "（例：研究员给的「市场规模 3 亿」在留痕里查不到出处）。", [], None)
    ok, msg = legion.report_issue(kind, text, upstream)
    kinds = " / ".join(legion._REPORT_KINDS)
    if not ok:
        return (f"{msg}\n（可选 kind：{kinds}）", [], None)
    return (msg, [], None)


@register_tool("legion_board", risk="read")
def _h_legion_board(cfg, app_dir, args, progress=None):
    """读项目的**共享任务板**：各节点干到哪了、最近发生了什么。

    任务板挂在**项目**上，不挂在某个角色身上 —— 换成员、换项目经理、重启程序
    都不丢。项目经理靠它知道每个成员干到哪了，才能调度。
    """
    import legion
    pid = (args.get("project_id") or "").strip()
    if not pid:
        return ("缺少 project_id。任务板按项目 ID 存；不知道 ID 时可先跑一次军团，"
                "或从执行日志里找 project_id。", [], None)
    board = legion.board_get(pid)
    nodes = board.get("nodes") or {}
    n_events = int(args.get("events") or 30)
    if not nodes:
        return (f"任务板 {pid} 还是空的（本项目尚未执行过，或任务板被清空）。", [], None)

    lines = [f"任务板 · 项目「{board.get('project_name') or pid}」（{pid}）",
             f"共 {len(nodes)} 个节点："]
    for key in sorted(nodes):
        nd = nodes[key] or {}
        status = nd.get("status") or "?"
        role = nd.get("role") or ""
        wave = nd.get("wave")
        summ = (nd.get("summary") or "").replace("\n", " ")[:80]
        seg = f"  · {key}"
        if role:
            seg += f" [{role}]"
        if wave:
            seg += f" 第{wave}波"
        seg += f" → {status}"
        if nd.get("pm_verdict"):
            seg += f"（PM 建议 {nd['pm_verdict']}）"
        if summ:
            seg += f"  {summ}"
        lines.append(seg)

    evs = (board.get("events") or [])[-n_events:]
    if evs:
        lines.append(f"\n最近 {len(evs)} 条事件：")
        for e in evs:
            lines.append(f"  {e.get('time','--:--:--')} {e.get('node')} → "
                         f"{e.get('status') or e.get('pm_verdict') or ''}")
    lines.append("\n状态说明：pending 待跑 / running 执行中 / done 已完成 / "
                 "error 失败 / await_approval 等你授权 / approved 已放行 / rejected 已打回")
    return ("\n".join(lines), [], None)

@register_tool("write_file", dangerous=True)
def _h_write_file(cfg, app_dir, args, progress=None):
    p = args.get("path", "")
    r = tool_write_file(app_dir, p, args.get("content", ""))
    # 审计修复 E3：仅写入成功才登记交付物。原实现只要 path 非空就挂号，
    # "写入失败：…"/"拒绝：…"/"未提供路径" 也会生成假文件卡片；
    # 顺带把交付物路径解析为真实落盘的绝对路径（tool_write_file 内部同规则），可点击打开。
    if p and r.startswith("已写入"):
        # v4.164.0：解析基准必须与 tool_write_file 一致（相对路径落 WORKSPACE_DIR），
        # 否则登记出来的交付物路径会指向不存在的位置、点不开。
        return (r, [(os.path.abspath(os.path.join(WORKSPACE_DIR, p)), "file",
                     os.path.basename(p))], None)
    return (r, [], None)

@register_tool("run_command", dangerous=True)
def _h_run_command(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    return (tool_run_command(app_dir, args.get("command", ""),
                             cwd=args.get("cwd"), env=args.get("env"),
                             progress=progress, stop_event=stop_event,
                             should_stop=should_stop), [], None)

@register_tool("run_python", dangerous=True)
def _h_run_python(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    r, d = tool_run_python(app_dir, args.get("code", ""), cfg=cfg,
                           progress=progress, stop_event=stop_event, should_stop=should_stop)
    return (r, d, None)

def _asset_register_deliverable(res, prompt, kind):
    """v4.125 ④：工具产出（图/视频）自动登记进资产库，军团可复用。

    res 是 (path, kind_hint, name) 之类的交付物元组；登记失败静默（不影响工具返回）。
    """
    try:
        import asset_store
        p = str(res[0] or "")
        if p and os.path.isfile(p):
            name = (str(res[2]) if len(res) > 2 and res[2] else
                    os.path.splitext(os.path.basename(p))[0])[:60]
            asset_store.register_asset(
                name, kind, p,
                tags=[kind, "agent产出"],
                project="Agent", task=(prompt or "")[:100],
                meta={"prompt": (prompt or "")[:200]})
    except Exception:
        pass


@register_tool("image_gen")
def _h_image_gen(cfg, app_dir, args, progress=None):
    # v4.108 M-16：模型传的 size（"WxH"）必须透传给后端——此前被 handler 丢弃，
    # 模型以为指定了尺寸实际永远走默认。
    _prompt = args.get("prompt", "")
    res = tool_image_gen(cfg, app_dir, _prompt,
                         size=args.get("size"), progress=progress)
    if isinstance(res, tuple):
        _asset_register_deliverable(res, _prompt, "image")
        return (res[0], [res], None)
    return (res, [], None)

@register_tool("video_gen")
def _h_video_gen(cfg, app_dir, args, progress=None):
    _prompt = args.get("prompt", "")
    res = tool_video_gen(cfg, app_dir, _prompt,
                         duration=args.get("duration"), aspect=args.get("aspect"),
                         image=args.get("image"),
                         first_frame=args.get("first_frame"),
                         last_frame=args.get("last_frame"),
                         images=args.get("images"),
                         ref_images=args.get("ref_images"),   # v4.127 多参考图
                         dialogue=args.get("dialogue"),
                         progress=progress)
    if isinstance(res, tuple):
        _asset_register_deliverable(res, _prompt, "clip")
        return (f"视频已生成并保存到：{res[0]}", [res], None)
    return (res, [], None)

@register_tool("schedule")
def _h_schedule(cfg, app_dir, args, progress=None):
    r, s = tool_schedule(args)
    return (r, [], s)


def _norm_time(s):
    """归一化时间字符串为 HH:MM，兼容 '9:00'/'09:00'/'9点'/'9时30分'/'9:00:00'。"""
    import re as _re
    s = str(s or "").strip().replace("点", ":").replace("时", ":").replace("分", "")
    m = _re.search(r"(\d{1,2}):(\d{2})", s)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    m = _re.search(r"(\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):02d}:00"
    return "09:00"


def _norm_weekday(w):
    """归一化星期参数为 0(周一)~6(周日)。支持 0-6、'一'~'日'、'周一' 等。"""
    if w is None:
        return 0
    if isinstance(w, int):
        return w % 7
    wd = str(w).strip().replace("星期", "").replace("周", "")
    _map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    if wd in _map:
        return _map[wd]
    try:
        return int(wd) % 7
    except Exception:
        return 0


@register_tool("create_automation")
def _h_create_automation(cfg, app_dir, args, progress=None):
    """创建自动化任务（定时提醒 / 定时执行 Agent 任务）。Agent 对话里即可建，落盘后调度器 1 秒内感知。"""
    import automation as auto
    name = (args.get("name") or args.get("task_name") or "").strip()
    message = (args.get("message") or args.get("instruction")
               or args.get("prompt") or args.get("content") or "").strip()
    if not name:
        return ("未提供任务名称（name）", [], None)
    if not message:
        return ("未提供任务内容（message：提醒内容或执行指令）", [], None)
    # 动作归一化
    action = str(args.get("action") or "run")
    if action in ("remind", "提醒", "notification", "通知", "弹窗"):
        action = auto.ACT_REMIND
    else:
        action = auto.ACT_RUN
    # 调度方式归一化
    st_raw = str(args.get("schedule_type") or args.get("schedule") or "daily")
    _st_map = {
        "once": auto.SCHED_ONCE, "一次性": auto.SCHED_ONCE, "指定时间": auto.SCHED_ONCE,
        "daily": auto.SCHED_DAILY, "每天": auto.SCHED_DAILY, "每日": auto.SCHED_DAILY,
        "weekly": auto.SCHED_WEEKLY, "每周": auto.SCHED_WEEKLY,
        "interval": auto.SCHED_INTERVAL, "间隔": auto.SCHED_INTERVAL, "每": auto.SCHED_INTERVAL,
    }
    st = _st_map.get(st_raw, auto.SCHED_DAILY)
    # v4.245.0（审查报告 T-8）：每天/每周任务缺时间时不再静默补 09:00，反问用户。
    # 用户说「每天早上提醒我喝水」却没给时刻时，替用户默认 09:00 是擅自决定。
    _has_time = bool(args.get("at_time") or args.get("time"))
    if not _has_time and st in (auto.SCHED_DAILY, auto.SCHED_WEEKLY):
        return ("请补充任务执行时间（at_time，格式 HH:MM，如 09:00）。"
                "「每天/每周」任务需要指定具体时刻，我无法替你默认。", [], None)
    at_time = _norm_time(args.get("at_time") or args.get("time") or "09:00")
    at_date = args.get("at_date") or args.get("date") or ""
    weekday = _norm_weekday(args.get("weekday"))
    try:
        interval_minutes = max(1, int(args.get("interval_minutes") or 60))
    except Exception:
        interval_minutes = 60
    store = auto.AutomationStore()
    t = auto.new_task(name, action, message, st, at_time=at_time, at_date=at_date,
                      weekday=weekday, interval_minutes=interval_minutes, enabled=True)
    store.add(t)
    act_label = "定时提醒" if action == auto.ACT_REMIND else "执行任务"
    return (f"已创建自动化任务「{name}」：{auto.schedule_summary(t)}，动作={act_label}。"
            f"任务已保存，调度器约 1 秒内感知。", [], None)


@register_tool("list_automation")
def _h_list_automation(cfg, app_dir, args, progress=None):
    import automation as auto
    store = auto.AutomationStore()
    tasks = store.list_all()
    if not tasks:
        return ("当前没有自动化任务。可用 create_automation 创建。", [], None)
    lines = ["当前自动化任务："]
    for t in tasks:
        act = "提醒" if t.get("action") == auto.ACT_REMIND else "执行"
        en = "启用" if t.get("enabled", True) else "停用"
        lines.append(f"- [{t.get('id')}] {t.get('name')}（{act}，{auto.schedule_summary(t)}，{en}）")
    return ("\n".join(lines), [], None)


@register_tool("delete_automation")
def _h_delete_automation(cfg, app_dir, args, progress=None):
    import automation as auto
    tid = args.get("id") or args.get("task_id") or ""
    name = (args.get("name") or "").strip()
    store = auto.AutomationStore()
    target = None
    if tid:
        target = store.get(tid)
    elif name:
        for t in store.list_all():
            if t.get("name") == name:
                target = t
                break
    if not target:
        return ("未找到要删除的任务。先用 list_automation 查看任务 id 或名称。", [], None)
    store.delete(target["id"])
    return (f"已删除自动化任务「{target.get('name')}」", [], None)

@register_tool("rag_index")
def _h_rag_index(cfg, app_dir, args, progress=None):
    r, _, _ = tool_rag_index(cfg, app_dir, args)
    return (r, [], None)

@register_tool("rag_search")
def _h_rag_search(cfg, app_dir, args, progress=None):
    r, _, _ = tool_rag_search(cfg, app_dir, args)
    return (r, [], None)

@register_tool("use_skill")
def _h_use_skill(cfg, app_dir, args, progress=None):
    return (tool_use_skill(cfg, app_dir, args.get("skill_name", "")), [], None)

@register_tool("analyze_image")
def _h_analyze_image(cfg, app_dir, args, progress=None):
    return (tool_analyze_image(cfg, args), [], None)

@register_tool("remember", dangerous=True)
def _h_remember(cfg, app_dir, args, progress=None):
    return (tool_remember(cfg, app_dir, args), [], None)

@register_tool("search_memory")
def _h_search_memory(cfg, app_dir, args, progress=None):
    return (tool_search_memory(cfg, app_dir, args), [], None)

@register_tool("run_workflow")
def _h_run_workflow(cfg, app_dir, args, progress=None):
    """v4.60：触发工作流引擎执行多Agent协作任务。Agent 不直接执行，而是通知主线程启动工作流。"""
    wf_type = (args or {}).get("type", "research_write")
    task = (args or {}).get("task", "")
    return (f"工作流「{wf_type}」已提交，任务: {task[:200]}。请等待结果，不要重复提交。", [], None)

@register_tool("log_query")
def _h_log_query(cfg, app_dir, args, progress=None):
    rows = get_logger().query(level=args.get("level"), module=args.get("module"),
                              start_time=args.get("start_time"), end_time=args.get("end_time"),
                              limit=args.get("limit", 20))
    lines = [f"[{r['timestamp']}] [{r['level']}] {r['module'] or '-'}: {r['message']}" for r in rows]
    return ("\n".join(lines) if lines else "（无匹配日志）", [], None)

@register_tool("chart_gen")
def _h_chart_gen(cfg, app_dir, args, progress=None):
    res = _get_chart_gen().generate(chart_type=args.get("chart_type"), data=args.get("data", {}),
                             title=args.get("title", "图表"), palette=args.get("palette", "default"),
                             output_path=args.get("output_path"))
    if res.get("status") == "success":
        p = res["path"]
        return (f"图表已生成：{p}", [(p, "image", os.path.basename(p))], None)
    return (f"图表生成失败：{res.get('message')}", [], None)

@register_tool("sys_info")
def _h_sys_info(cfg, app_dir, args, progress=None):
    """v4.60：自省工具——返回系统运行时真实状态，消除模型幻觉。"""
    return (tool_sys_info(cfg, app_dir), [], None)

@register_tool("context_compress")
def _h_context_compress(cfg, app_dir, args, progress=None):
    return ("上下文压缩完成：\n" + get_context_manager().compress_with_llm(cfg), [], None)

@register_tool("context_summary")
def _h_context_summary(cfg, app_dir, args, progress=None):
    ctx = get_context_manager().get_compressed_context()
    recent, info, sums = ctx.get("recent_messages", []), ctx.get("key_info", {}), ctx.get("summaries", [])
    lines = [f"最近消息：{len(recent)} 条", f"关键实体：{info.get('entities', [])}",
             f"待办：{info.get('todos', [])}", f"历史摘要：{len(sums)} 条"]
    for i, s in enumerate(sums[-5:], 1):
        if "summary" in s:
            lines.append(f"  摘要{i}[{s.get('method')}]: {s['summary'][:300]}")
        else:
            lines.append(f"  摘要{i}[{s.get('method')}]: {s.get('period')}")
    return ("\n".join(lines), [], None)

@register_tool("db_query")
def _h_db_query(cfg, app_dir, args, progress=None):
    rows = db_tools.query(table=args.get("table", "notes"), where=args.get("where"), limit=args.get("limit", 50))
    return (json.dumps(rows, ensure_ascii=False, indent=2) if rows else "（无匹配记录）", [], None)

@register_tool("db_insert")
def _h_db_insert(cfg, app_dir, args, progress=None):
    return (json.dumps(db_tools.insert(table=args.get("table", "notes"), data=args.get("data", {})), ensure_ascii=False), [], None)

@register_tool("db_update")
def _h_db_update(cfg, app_dir, args, progress=None):
    return (json.dumps(db_tools.update(table=args.get("table", "notes"), record_id=args.get("record_id"), data=args.get("data", {})), ensure_ascii=False), [], None)

@register_tool("db_delete")
def _h_db_delete(cfg, app_dir, args, progress=None):
    return (json.dumps(db_tools.delete(table=args.get("table", "notes"), record_id=args.get("record_id")), ensure_ascii=False), [], None)

@register_tool("webhook_start")
def _h_webhook_start(cfg, app_dir, args, progress=None):
    from webhook_server import webhook_start
    port = args.get("port", 9000)
    # v4.108 M-28：带共享 token 启动（空则回环保护）；token 为空时自动生成并持久化
    token = (cfg or {}).get("webhook_token") or ""
    if not token:
        import uuid
        token = uuid.uuid4().hex[:16]
        cfg["webhook_token"] = token
        try:
            from config import save_config
            save_config(cfg)
        except Exception as e:
            # v4.125 P3：token 持久化失败要留痕——否则重启后旧 token 全部 401 无从排查。
            log.warning("webhook token 持久化失败（重启后需重新生成）: %s", e)
    r = webhook_start(port, token=token)
    return (f"Webhook 服务器已启动（端口 {port}，仅本机 127.0.0.1 可访问，"
            f"请求需带 X-Webhook-Token: {token}）" if r is True
            else f"Webhook 启动失败：{r}", [], None)

@register_tool("webhook_stop")
def _h_webhook_stop(cfg, app_dir, args, progress=None):
    from webhook_server import webhook_stop
    return ("Webhook 服务器已停止" if webhook_stop() else "Webhook 服务器未运行", [], None)

@register_tool("webhook_events")
def _h_webhook_events(cfg, app_dir, args, progress=None):
    from webhook_server import webhook_recent_events
    evs = webhook_recent_events(args.get("limit", 20))
    return (json.dumps(evs, ensure_ascii=False, indent=2) if evs else "（暂无 webhook 事件）", [], None)


@register_tool("send_email")
def _h_send_email(cfg, app_dir, args, progress=None):
    return (tool_send_email(cfg, args.get("to", ""), args.get("subject", ""), args.get("body", "")), [], None)


# ---- v4.131：抓取留痕 ----
# 病根：军团成员搜了什么词、抓了哪些页面，此前**完全不留痕**。PM 验收只能看
# 成品正文，正文写得像模像样就放行 —— 实际上成员可能一个字都没搜、纯靠模型
# 记忆编（实测：研究员交 4006 字网页堆砌、竞品分析师交 881 字无关搜索结果，
# 都是「搜偏了还硬交」）。留痕后 PM 能先查「搜的词对不对」，再判内容。
_FETCH_TRACE_TOOLS = ("web_search", "web_fetch", "browser_read")


def _trace_fetch(name, args, result, ok=None):
    """把 web_search / web_fetch 的调用记进军团抓取留痕仓（非军团运行时自动空转）。"""
    if name not in _FETCH_TRACE_TOOLS:
        return
    try:
        import legion as _lg
        if not hasattr(_lg, "record_source"):
            return
    except Exception:
        return
    try:
        target = args.get("query") or args.get("url") or ""
        txt = str(result or "")
        if ok is None:
            ok = bool(txt.strip()) and ("未找到搜索结果" not in txt[:80]) \
                and ("未提供" not in txt[:20]) and not txt.startswith("抓取失败") \
                and not txt.startswith("未知工具")
        _lg.record_source(name, target, txt, ok=bool(ok))
    except Exception as e:
        log.warning("抓取留痕失败: %s", e)


def _permission_gate(name, args, perm_ctx):
    """exec_tool 最终权限闸门（审查报告 P1 #4）。

    调用方必须携带合法权限决策；未携带时按风险分类 fail-closed：
      · RiskClass.READ → 放行（保向后兼容，只读不拦）
      · 其它（WRITE_LOCAL / EXEC / EXTERNAL / 副作用 / 未登记）→ 拒绝
    未登记工具 classify 默认 EXTERNAL，故天然被拒（符合「未授权不执行」）。
    perm_ctx 为 permissions.Decision 时信任其 .allowed（不重复弹窗）；
    否则按上述分类判定。用鸭子类型识别 Decision，避免 tools<->permissions 循环依赖。
    """
    # 携带合法决策对象：信任其结论
    if perm_ctx is not None and hasattr(perm_ctx, "allowed"):
        if not perm_ctx.allowed:
            return (False, f"权限决策拒绝执行 {name}：{getattr(perm_ctx, 'reason', '')}")
        # v4.245.0（审查报告 T-5）：需要用户确认但未取得确认 → 拒绝（第二道锁）。
        # 调用方完成确认后必须置 confirmed=True，否则这里 fail-closed，
        # 杜绝「某调用方漏检 needs_user 就静默跳过确认」。
        if getattr(perm_ctx, "needs_user", False) and not getattr(perm_ctx, "confirmed", False):
            return (False, f"该操作需要用户确认但未取得确认（{name}）")
        return (True, None)
    # 无上下文：按风险分类 fail-closed
    try:
        _risk = classify(name)
    except Exception:
        _risk = RiskClass.EXTERNAL
    if _risk == RiskClass.READ:
        return (True, None)
    return (False,
            f"exec_tool 需要权限上下文：未携带合法授权即拒绝执行 {name} "
            f"（风险类={_risk.name}，审查报告 P1 #4）。"
            f"请通过主 Agent / 军团权限适配器调用。")


def exec_tool(cfg, app_dir, name, args, progress=None, allowed_tools=None,
              stop_event=None, should_stop=None, perm_ctx=None):
    """统一工具路由，返回 (result_str, deliverables, schedule)。

    result_str: 工具执行结果文本
    deliverables: [(rel_path, kind, name), ...] 新生成的交付物
    schedule: (message, delay_seconds) 定时提醒，无则为 None

    v4.125 M-04：allowed_tools（可选集合）= 执行端白名单。此前白名单只过滤
    发给模型的 tools schema，执行端按名字直查注册表——模型幻觉/注入诱导出
    白名单外的工具（run_python/send_email 等）仍会被真实执行。军团成员
    调用时必须传角色 tools，双层防线。
    """
    def _reject(msg, ok=False):
        _m = _mask_sensitive(msg) if isinstance(msg, str) else msg
        return ToolResult(ok=ok, msg=_m)

    if allowed_tools is not None and name not in allowed_tools:
        log.warning("工具 %s 不在执行端白名单内，已拒绝（M-04）", name)
        return _reject(f"工具 {name} 不在本角色可用工具列表内，已拒绝执行。"
                       "请只使用角色卡声明的工具。")
    # P1 #4（v4.219）：exec_tool 成为不可绕过的最终权限闸门。
    # 未携带合法权限决策时，写入/执行/外发/桌面控制类操作一律拒绝。
    _gate_ok, _gate_msg = _permission_gate(name, args, perm_ctx)
    if not _gate_ok:
        log.warning("exec_tool 最终闸门拒绝 %s：%s", name, _gate_msg)
        return _reject(_gate_msg)
    # v4.129：缓存 cfg，供无 cfg 参数的落盘函数读 products_layout 开关
    global _LAST_CFG
    try:
        if cfg:
            _LAST_CFG = cfg
    except Exception:
        pass
    # v4.223：统一参数校验（副作用前先拦）。未登记 schema 的工具原样放行，
    # 已登记的按 schema 校验：类型/必填/范围/枚举 + 路径标准化/长度上限/超时上限。
    if isinstance(args, dict):
        _va, _ve = validate_for_tool(name, args)
        if _ve:
            log.warning("工具 %s 参数校验失败：%s", name, _ve)
            _trace_fetch(name, args, f"参数校验未通过：{_ve}", ok=False)
            return _reject(f"工具 {name} 参数校验未通过：{_ve}")
        args = _va
    deliverables = []
    schedule = None
    # v4.31 统一注册中心：优先查 registry（扩展模块已注册；核心工具逐步迁移中）
    _entry = TOOL_REGISTRY.get(name)
    if _entry:
        try:
            get_logger().info(f"执行工具: {name}", module="tools", extra={"tool": name})
            # ②-A.3：仅向声明了 stop_event/should_stop 的 handler 透传（run_command/run_python），
            # 其余 handler 签名不含这两个参数，避免 TypeError。
            _hk = {"progress": progress}
            _sig = inspect.signature(_entry["handler"])
            if "stop_event" in _sig.parameters:
                _hk["stop_event"] = stop_event
            if "should_stop" in _sig.parameters:
                _hk["should_stop"] = should_stop
            _r = _entry["handler"](cfg, app_dir, args, **_hk)
            # v4.223：传 name/args，让已登记结局契约的工具按真实信号判定成败
            _tr = ToolResult.from_legacy(_r, name=name, args=args)
            _trace_fetch(name, args, _tr.msg, ok=_tr.ok)
            _tr.msg = _mask_sensitive(_tr.msg)
            _tr.data = _mask_recursive(_tr.data)
            _tr.evidence = _mask_recursive(_tr.evidence)
            # v4.224：执行后验证（查副作用是否真生效；只降级不升级）
            try:
                apply_post_verification(_tr, name, args)
            except Exception as _ve:
                # v4.245.0（审查报告 T-6）：验证器坏掉不留痕 = 无法区分「没验证」和「验证器崩了」
                log.warning("后验验证异常 %s: %s", name, _ve)
            return _tr
        except Exception as e:
            log.warning("工具 %s 执行异常: %s", name, e)
            try:
                # v4.155 fix1：异常分支补 ERROR 级落库，便于事后排查工具失败原因
                get_logger().error(
                    f"工具执行失败: {name} | args={_trunc_args(args)}",
                    module="tools", exc_info=True)
            except Exception:
                pass
            _trace_fetch(name, args, f"工具执行异常：{e}", ok=False)
            return _reject(f"工具执行异常：{e}")

    try:
        get_logger().info(f"执行工具: {name}", module="tools", extra={"tool": name})
        result_str = _try_mcp_tool(name, args)
    except Exception as e:
        try:
            # v4.155 fix1：MCP 工具异常分支同样补 ERROR 级落库
            get_logger().error(
                f"工具执行失败(MCP): {name} | args={_trunc_args(args)}",
                module="tools", exc_info=True)
        except Exception:
            pass
        result_str = f"工具执行异常：{e}"

    _tr = ToolResult.from_legacy((result_str, deliverables, schedule),
                                 name=name, args=args)
    _trace_fetch(name, args, _tr.msg, ok=_tr.ok)
    _tr.msg = _mask_sensitive(_tr.msg)
    # v4.224：执行后验证（查副作用是否真生效；只降级不升级）
    try:
        apply_post_verification(_tr, name, args)
    except Exception as _ve:
        # v4.245.0（审查报告 T-6）：验证器坏掉不留痕 = 无法区分「没验证」和「验证器崩了」
        log.warning("后验验证异常 %s: %s", name, _ve)
    return _tr


def _try_mcp_tool(name, args):
    """尝试在已连接的 MCP 客户端中查找并调用工具"""
    import config as config_mod
    for client in config_mod.mcp_clients:
        for t in client.tools:
            if t.get("function", {}).get("name") == name:
                log.info("路由 MCP 工具 [%s] -> MCP 服务器 [%s]", name, client.name)
                try:
                    return client.call_tool(name, args)
                except Exception as e:
                    return f"MCP 工具 [{name}] 调用异常：{e}"
    return f"未知工具：{name}"


# ============ 各工具函数 ============

# 内容平台 → 最优搜索引擎（实测：中文内容/平台类搜狗质量碾压百度/Bing；
# 百度对平台类反爬返回0条、Bing把『小红书』拆成『小』字匹配出小游戏垃圾）
PLATFORM_ENGINE = {
    "小红书": "sogou", "抖音": "sogou", "快手": "sogou", "知乎": "sogou",
    "微博": "sogou", "视频号": "sogou", "公众号": "sogou", "b站": "sogou",
    "bilibili": "sogou",
}
# 内容平台多角度子查询后缀（不含年份，年份运行时从用户意图抽取，避免写死）
_PLATFORM_SUFFIXES = [
    "爆款 趋势 报告",
    "用户画像 品类 增长 数据",
    "爆款 内容 策略 方法论",
    "高增长 赛道 行业 洞察",
]


def _detect_year(query):
    """从用户 query 抽取目标年份；命中 20xx 直接用，含『上半年/下半年/最新』或无年份默认今年。"""
    import datetime
    m = __import__("re").search(r"(20\d\d)", query)
    if m:
        return m.group(1)
    if "上半年" in query or "下半年" in query or "最新" in query or "今年" in query:
        return str(datetime.date.today().year)
    return str(datetime.date.today().year)  # 默认今年（确保不回到旧的 2025 写死）


# v4.131：搜索结果尾部统一带的「抓取纪律」。
# 实测病根：成员搜到「抖音国际版/百度经验」这种明显不相干的条目，照样整段抄进
# 产出凑字数 —— 它不知道「不相干就该换词重搜」，也不知道「搜不到可以明说」。
_SEARCH_DISCIPLINE = (
    "\n\n（🔴 抓取纪律：上面这些是**原料，不是产出**。"
    "禁止把本段原文/摘要直接交回当成你的交付物，"
    "必须加工成约定的结构化结果（表格/清单/结论）；也不许把本段纪律文字抄进交付物。"
    "每条硬结论都要标【来源 URL + 采集日期】。"
    "逐条核对平台/地区/时间是否对得上，对不上就**换关键词重搜**；"
    "确实搜不到就明确写「未获取到，待补」。"
    "禁止拿不相干条目凑数，禁止凭记忆编数据。）"
)


def _is_junk_search_result(query, results):
    """v4.157.0：判定直连引擎返回的是否为与 query 不沾边的模板页（万年历 / 节假日通知 /
    黄页 / 百科定义 / 旅游攻略），命中即视为失效，让 tool_web_search 跳过该 provider、
    导流浏览器 VPN 通道（见《三项修复方案》方案四）。

    直连引擎（Bing/百度/搜狗）对「天气 / 新闻 / 今日热点」等时效查询常吐模板化无关页
    （实测：昆明天气→旅游攻略/百科；「2026年9月17日 AI 新闻」→ 万年历/国务院节假日通知/
    Britannica「什么是AI」），旧逻辑「有结果即 return」把它们当真数据返回，浏览器可靠通道
    因此永不触发，自动化任务白烧 2-3 轮。

    判据（命中任一即失效）：
      · 万年历 / 黄历 / 宜忌 / 节假日安排 / 国务院节假日 / 法定节假日 / 放假安排 → 日历模板页；
      · 黄页 / 企业名录 / 114查号 / 查号台 / 工商注册号 → 黄页；
      · 带时效信号（新闻/天气/今日/最新…）且结果含 Britannica / 维基百科 / 多条「什么是」→ 百科定义页；
      · 天气类 query 但结果里完全找不到任何天气信号词（如返回旅游攻略/百科）→ 主题错配。

    安全闸：用户本就在查 日历/黄历/放假/黄页/企业/电话/什么是 时不判垃圾，避免误杀真实需求。
    """
    if not results:
        return False
    _SELF_TOPIC = ("万年历", "日历", "农历", "黄历", "放假", "节假日", "黄页",
                   "企业名录", "电话查询", "什么是")
    if any(k in query for k in _SELF_TOPIC):
        return False
    text = " ".join(
        ((r.get("title") or "") + " " + (r.get("snippet") or ""))
        for r in results
    ).lower()
    qlow = (query or "").lower()

    # ① 日历 / 节假日模板页（强信号）
    _CAL = ("万年历", "黄历", "宜忌", "宜：", "忌：", "节假日安排", "国务院节假日",
            "法定节假日", "放假安排", "2026年放假", "2025年放假", "法定节日")
    # ② 黄页（强信号）
    _YP = ("黄页", "企业名录", "114查号", "查号台", "工商注册号")
    # ③ 百科定义（仅当 query 带时效信号时才算垃圾）
    _ENC = ("britannica", "维基百科", "百科词条")
    _ENC_SOFT = ("什么是",)

    hits = []
    for m in _CAL:
        if m in text:
            hits.append("cal:" + m)
            break
    else:
        # 黄历「宜X忌Y」配对（未必带冒号）+ 农历 同现 → 日历模板页强信号
        if "农历" in text and "宜" in text and "忌" in text:
            hits.append("cal:宜忌pair")
    for m in _YP:
        if m in text:
            hits.append("yp:" + m)
            break
    _TIME = ("新闻", "日报", "今日", "今天", "本周", "本月", "最新", "热点", "趋势",
             "热榜", "天气", "trending", "news", "实时", "现在", "最近", "2026年", "2025年")
    time_sig = any(k in qlow for k in _TIME)
    if time_sig:
        for m in _ENC:
            if m in text:
                hits.append("enc:" + m)
                break
        if any(k in text for k in _ENC_SOFT):
            enc_count = sum(
                1 for r in results
                if any(k in ((r.get("title") or "") + (r.get("snippet") or "")).lower()
                       for k in _ENC_SOFT)
            )
            if enc_count >= 2:
                hits.append("enc:什么是(x%d)" % enc_count)

    # ④ 天气类 query 主题错配：query 含天气词但结果无任何天气信号 → 大概率是攻略/百科
    _WX_INTENT = ("天气", "气温", "温度", "预报", "下雨", "降雨", "气象", "气候",
                  "降温", "升温", "湿度", "风力", "紫外线", "体感", "空气质量", "雾霾", "台风")
    _WX_SIGNAL = ("天气", "气象", "气温", "温度", "预报", "降雨", "下雨", "雨", "雪", "多云",
                  "晴", "风力", "湿度", "摄氏度", "℃", "气候", "体感", "空气", "雾霾",
                  "pm2.5", "风向", "转阴", "雷阵雨")
    if any(w in qlow for w in _WX_INTENT):
        if not any(s in text for s in _WX_SIGNAL):
            hits.append("wx_mismatch")

    return bool(hits)


def tool_web_search(cfg, query, app_dir=None):
    """v4.148.6：新增可选 app_dir —— 只用于「浏览器搜索兜底」（定位 CDP profile 与日志），
    不传也能跑（走默认 9222 + %LOCALAPPDATA% 下的专属 profile）。"""
    if not query:
        return "未提供搜索词"
    # 内容平台识别：命中则用最优引擎并自动展开多角度子查询（一次聚合多来源高质量结果）
    platform = None
    for p in PLATFORM_ENGINE:
        if p in query:
            platform = p
            break
    if platform:
        engine = PLATFORM_ENGINE[platform]
        year = _detect_year(query)
        top_k = 3
        blocks = []
        seen = set()
        total = 0
        time_mod = __import__("time")
        brave_key = cfg.get("brave_api_key", "")
        serper_key = cfg.get("serper_api_key", "")
        _SEARCH_BUDGET = 60  # v4.62：单次搜索整体预算（秒），超时返回已拿到的部分结果，不空转
        t_start = time_mod.time()
        for i, suf in enumerate(_PLATFORM_SUFFIXES[:3], 1):
            # 整体预算保护：累计超时立即收尾，返回已有结果（防免费引擎被反爬拖死）
            if time_mod.time() - t_start > _SEARCH_BUDGET:
                break
            subq = f"{platform} {year} {suf}"
            results = []
            # 主路径优先级：Brave key → Serper key（未来填了国外 API 才走）→ 国内免费引擎（Bing/百度/搜狗）→ DuckDuckGo 境外兜底
            if brave_key:
                results = search_mod.search_brave(subq, brave_key, top_k)
            elif serper_key:
                results = search_mod.search_serper(subq, serper_key, top_k)
            if not results:
                # 兜底：串行优先搜狗（质量最好）；反爬偶发返回验证页→0条，重试 2 次并错峰 1s 降低封锁
                for attempt in range(2):
                    try:
                        raw = search_mod.http_get(search_mod.search_url(engine, subq, top_k), timeout=10)
                    except Exception:
                        raw = None
                    results = search_mod.parse_search(raw, engine, top_k) or []
                    if results:
                        break
                    time_mod.sleep(1.0)
                # 搜狗仍空（被封）→ 回落百度/Bing（质量差但兜底，避免全空）
                if not results:
                    for prov in ["baidu", "bing"]:
                        try:
                            raw = search_mod.http_get(search_mod.search_url(prov, subq, top_k), timeout=10)
                        except Exception:
                            raw = None
                        results = search_mod.parse_search(raw, prov, top_k) or []
                        if results:
                            break
            if not results:
                # 境外兜底：英文 query 走 en Accept-Language（v4.124.4）
                results = search_mod.search_ddg(subq, top_k, lang="en" if search_mod._is_english_query(subq) else "zh")
            if not results:
                continue
            blocks.append(f"\n【角度{i}：{suf.strip()}】")
            for r in results[:top_k]:
                if r["url"] in seen:
                    continue
                seen.add(r["url"])
                total += 1
                blocks.append(f"· {r['title']}\n  {r['snippet']}\n  {r['url']}")
            time_mod.sleep(0.5)  # 子查询之间轻微错峰
        if blocks:
            if brave_key:
                src = "Brave Search API"
            elif serper_key:
                src = "Serper（Google 结果）"
            else:
                src = "国内免费引擎（Bing/百度/搜狗）+ DuckDuckGo 境外兜底"
            head = (f"已针对「{platform}」用{src}展开多角度搜索，"
                    f"共 {total} 条真实行业文章/报告（趋势/用户画像/爆款策略/赛道数据）：")
            tail = ("\n（提示：以上为检索到的真实内容，含来源链接；做数据分析时优先引用带具体数字、"
                    "年份、平台名的条目，禁止编造数据。）")
            return head + "\n".join(blocks) + tail
    # 普通搜索（事实类/未命中平台）：Brave key → Serper key → DuckDuckGo 零注册 → 免费引擎链
    top_k = cfg.get("search_top_k", 5)
    brave_key = cfg.get("brave_api_key", "")
    serper_key = cfg.get("serper_api_key", "")
    if brave_key:
        _r = search_mod.search_brave(query, brave_key, top_k)
        if _r:
            lines = [f"搜索「{query}」结果（来源：Brave Search API）："]
            for i, r in enumerate(_r[:top_k], 1):
                lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
            return "\n".join(lines) + _SEARCH_DISCIPLINE
    if serper_key:
        _r = search_mod.search_serper(query, serper_key, top_k)
        if _r:
            lines = [f"搜索「{query}」结果（来源：Serper / Google）："]
            for i, r in enumerate(_r[:top_k], 1):
                lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
            return "\n".join(lines) + _SEARCH_DISCIPLINE
    # 无付费 key：先走 query 语言感知路由（v4.124.4：英文 query 自动切到英文优先链
    # 'duckduckgo_en→bing_en→wikipedia_en'，避免 baidu 把 'pet hair' 拆成塑料瓶/达州时差，
    # 详见 search.py:_is_english_query / provider_chain 说明）。
    chain = search_mod.provider_chain(cfg.get("search_provider", "auto"), query=query)
    # v4.147：境外主题（TikTok Shop / 马来西亚 / 跨境…）无 VPN + 无 key 时，DuckDuckGo 被墙；
    # 若回落到百度/搜狗等国内引擎，对境外主题零覆盖，会吐模板化无关垃圾（研究员曾误当真数据）。
    # 故境外查询剔除国内引擎，只留 DDG 系 + bing_en + wikipedia_en（国内可直连），宁少给不给假。
    if search_mod._is_offshore_query(query):
        _domestic = {"bing", "baidu", "sogou"}
        chain = [p for p in chain if p not in _domestic]
        if "wikipedia_en" not in chain:
            chain.append("wikipedia_en")
        _offshore_mode = True
    else:
        _offshore_mode = False
    # v4.148.6：**境外主题优先走浏览器通道**。
    #
    # 为什么不能等链路全空才兜底（2026-09-14 实测）：境外模式下 http_get 链路里
    # `bing_en` 是唯一直连可用的引擎，但它对境外主题**总能返回一堆低质结果**
    # （实测「TikTok Shop Malaysia seller commission fee policy 2026」→ TikTok 官网
    # 首页、微软商店下载页、TikTok Academy 首页），于是「有结果」直接 return，
    # 兜底**永不触发** —— 军团照旧拿不到一手来源。
    # 而浏览器通道（走 VPN 的 DDG/Google）同一句实测 9 秒拿到 inseller.my 费率详解、
    # seller-my.tiktok.com 官方卖家中心政策页、费率计算器，全是带 URL 的真源。
    # 故：境外主题先给浏览器通道一次机会，不可用/无结果再回落原链路（不会更差）。
    if _offshore_mode:
        _bo = _browser_search_fallback(cfg, query, app_dir, top_k, offshore=True)
        if _bo:
            return _bo
    last_err = ""
    for provider in chain:
        try:
            raw = search_mod.http_get(search_mod.search_url(provider, query, top_k), timeout=10)
        except Exception as e:
            last_err = str(e)
            log.warning("工具搜索 %s 失败: %s", provider, e)
            continue
        results = search_mod.parse_search(raw, provider, top_k)
        if results:
            # v4.157.0：直连引擎「有结果就 return」会吞掉无关模板页（万年历/黄页/百科/
            # 旅游攻略），导致浏览器可靠通道永不触发（见《三项修复方案》方案四）。
            # 命中垃圾 → 跳过该 provider，继续试下一引擎；全链都垃圾/空才导流浏览器通道。
            if _is_junk_search_result(query, results):
                log.warning("工具搜索 %s 返回疑似无关模板页，跳过并导流浏览器通道", provider)
                continue
            lines = [f"搜索「{query}」结果（来源：{provider}）："]
            for i, r in enumerate(results[:top_k], 1):
                lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
            return "\n".join(lines) + _SEARCH_DISCIPLINE
    # 国内链全空 → 境外 DuckDuckGo 最后兜底（英文 query 走 en Accept-Language）
    _r = search_mod.search_ddg(query, top_k, lang="en" if search_mod._is_english_query(query) else "zh")
    if _r and not _is_junk_search_result(query, _r):
        lines = [f"搜索「{query}」结果（来源：DuckDuckGo 境外兜底）："]
        for i, r in enumerate(_r[:top_k], 1):
            lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
        return "\n".join(lines) + _SEARCH_DISCIPLINE
    # v4.148.6：常规链路全空 → **浏览器搜索兜底**（唯一吃浏览器 VPN 的通道）。
    _b = _browser_search_fallback(cfg, query, app_dir, top_k, offshore=_offshore_mode)
    if _b:
        return _b
    if _offshore_mode:
        return ("⚠️ 境外主题搜索无法获取：当前未开 VPN 且未配置 Serper/Brave key，DuckDuckGo 被墙、"
                "国内引擎已禁用（避免返回无关垃圾）。已在浏览器通道再试一次也没取到 —— "
                "请确认调试浏览器里 VPN 扩展已点「连接」，或在 config.json 加 brave_api_key / "
                "serper_api_key 后重试。")
    return f"未找到搜索结果（最后错误：{last_err}）" if last_err else "未找到搜索结果"


def _browser_search_fallback(cfg, query, app_dir, top_k, offshore=False):
    """v4.148.6：常规搜索链全空时的浏览器兜底。返回拼好的文案；不可用/无结果返回 ""。

    为什么必须有它：`search.py:http_get` 是小臭**自己的 Python 网络栈直连**（纯 urllib、
    零代理），**不吃浏览器 VPN** —— 这就是「VPN 一直连着、搜索却全空或只回噪声」的真相，
    也是成员反复硬撞境外站超时的底层原因。唯一吃梯子的通道是 browser_*（接管真实 Edge）。
    此前只在角色卡里写了「JS 页请用 browser」，四轮实测**没人照做**；兜底写进工具层
    比靠提示倒逼可靠。

    可用开关：config.json 里 `"web_search_browser_fallback": false` 可关闭（默认开）。
    """
    try:
        if not (cfg or {}).get("web_search_browser_fallback", True):
            return ""
    except Exception:
        pass
    try:
        import browser_control_tools as _bct
    except Exception as e:            # 模块缺失/导入失败都不该拖垮搜索
        log.warning("浏览器搜索兜底跳过（导入 browser_control_tools 失败）: %s", e)
        return ""
    # 引擎顺序（2026-09-14 真机实测）：DDG html 版最可靠（结果 href 是 `uddg=` 跳转链、
    # 可解码还原真实 URL）→ Google（多为真实 URL）→ Bing（结果链接已加密成 `p=`，
    # 客户端解不出，只作最后手段）。
    engines = ["duckduckgo", "google", "bing"]
    try:
        res, note = _bct.browser_search(cfg, query, app_dir=app_dir,
                                        top_k=top_k, engines=engines)
    except Exception as e:
        log.warning("浏览器搜索兜底失败: %s", e)
        return ""
    if not res:
        log.warning("浏览器搜索兜底无结果：%s", note)
        return ""
    lines = [f"搜索「{query}」结果（来源：{note}）："]
    for i, r in enumerate(res[:top_k], 1):
        lines.append(f"{i}. {r['title']}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
        if r.get("redirect"):
            lines.append(f"   {r['url']}")
            lines.append("   ⚠️ 上面这条是搜索引擎加密链，**不可直接读取**（打开会返回 400）："
                         "只当标题线索用，别拿它去 browser_read")
        else:
            lines.append(f"   {r['url']}　← 可直接 browser_read")
    lines.append("（说明：本通道摘要可能不完整；**正文请用 browser_read 打开上面标「可直接 browser_read」"
                 "的 URL 取回**，再按交付格式加工成表格/清单，每条带【来源 URL】【采集日期】。）")
    return "\n".join(lines) + _SEARCH_DISCIPLINE


def _is_sensitive_file(path):
    """v4.90 安全加固：判断是否敏感文件（含 API key / 密钥），Agent 禁止读取。

    覆盖：用户数据目录下的主配置 config.json（含所有 api_key）、.env、私钥、凭据等。
    v4.108.1：清理 v4.100 开源脱敏时留在本机源码里的 AgentDesktop 残串——
    该残串匹配不到真实路径，拦截实际由下方 endswith("config.json") 兜底，故行为未变。
    """
    if not path:
        return False
    p = str(path).lower().replace("\\", "/")
    for marker in (".env", "id_rsa", "id_dsa", "credentials", "credential",
                   "secret", "api_key", "apikey", "access_token", "passwd"):
        if marker in p:
            return True
    if p.rstrip("/").endswith("config.json"):
        return True
    return False


# 抓取正文抽取的噪音词（短句命中才砍 —— 长正文里出现不误杀）
_FETCH_NAV_WORDS = (
    "skip to content", "skip to main", "toggle navigation", "all rights reserved",
    "privacy policy", "terms of service", "cookie", "sign in", "log in", "sign up",
    "subscribe", "newsletter", "breadcrumb", "jump to", "share this", "read more",
    "首页", "登录", "注册", "版权所有", "京公网安备", "意见反馈", "关于我们",
    "联系我们", "网站地图", "免责声明", "手机版", "电脑版", "扫码", "下载app",
    "上一篇", "下一篇", "返回顶部", "相关推荐", "热门推荐",
)


def _html_main_text(raw):
    """v4.131：从 HTML 抽**正文主体**，砍掉导航/页脚/版权/重复块。

    旧实现整页去标签后直接返回，导航菜单、页脚备案、cookie 提示全混进正文
    —— 成员拿到这种大杂烩只能整段贴进产出（实测：研究员 4006 字产出通篇是
    Malaysia.travel / MCMC 的导航与正文混排，等于把抓取素材当结论交了）。
    """
    t = re.sub(r'<script.*?</script>', ' ', raw, flags=re.S | re.I)
    t = re.sub(r'<style.*?</style>', ' ', t, flags=re.S | re.I)
    t = re.sub(r'<!--.*?-->', ' ', t, flags=re.S)
    t = re.sub(r'<(nav|header|footer|aside)\b[^>]*>.*?</\1>', ' ', t, flags=re.S | re.I)
    # 块级标签 → 换行：保住段落结构，才谈得上「按行过滤」
    t = re.sub(r'<(?:br|/p|/div|/li|/tr|/h[1-6]|/section|/article|/td|/p)\b[^>]*>',
               '\n', t, flags=re.I)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = html_mod.unescape(t)
    lines, seen = [], {}
    for ln in t.split('\n'):
        s = re.sub(r'\s+', ' ', ln).strip()
        if len(s) < 8:
            continue
        low = s.lower()
        if len(s) < 40 and any(w in low for w in _FETCH_NAV_WORDS):
            continue
        if s.count('http') >= 2 and len(re.sub(r'http\S+', '', s)) < 12:
            continue
        k = s[:24]
        seen[k] = seen.get(k, 0) + 1
        # 短块（菜单/页脚话术）出现第 2 次即丢；长块（可能是真正文）第 3 次才丢，
        # 免得把 legit 的重复段落（表格行、并列条目）误杀。
        if seen[k] > (1 if len(s) < 60 else 2):
            continue
        lines.append(s)
    return re.sub(r'\n{2,}', '\n', '\n'.join(lines)).strip()


# v4.147：JS 重渲染 / 反爬域名 —— web_fetch 服务器侧拿不到正文，必须走 browser_open/browser_read。
# 实测：seller-my.tiktok.com 等 TikTok 官方域 web_fetch 直接 fetch failed；淘宝/京东/抖音/小红书
# 等前端 JS 渲染，http_get 只拿空壳。识别后给明确改道提示，避免成员误判「通道断」。
_JS_HEAVY_DOMAINS = (
    "seller-my.tiktok.com", "seller.tiktokglobalshop.com", "seller.tiktokshopglobalselling.com",
    "tiktok.com", "taobao.com", "tmall.com", "jd.com", "pinduoduo.com", "yangkeduo.com",
    "douyin.com", "xiaohongshu.com", "xhslink.com", "weixin.qq.com", "mp.weixin.qq.com",
    "weibo.com", "zhihu.com",
    # v4.147.4：电商数据/情报站同样是前端 JS 渲染，web_fetch 只拿得到 title 空壳。
    # 实测：kalodata.com 经 web_fetch 仅返 17 字「商品排行榜 - 美国 TikTok」，
    # 成员误以为抓到了数据、把空壳当战绩上报（TK 马来团第 1 波 FAIL 的直接原因之一）。
    "kalodata.com", "fastmoss.com", "echotik", "shoplus", "sellersprite",
    "shopee.", "lazada.", "1688.com", "temu.com", "shein.com", "amazon.",
)


def _is_js_heavy(url):
    try:
        h = urllib.parse.urlparse(url).netloc.lower()
        return any(d in h for d in _JS_HEAVY_DOMAINS)
    except Exception:
        return False


def tool_web_fetch(url):
    if not url:
        return "未提供 URL"
    if _is_sensitive_file(url):
        return "已阻止：目标文件是敏感配置（可能含 API key），禁止读取。"
    try:
        raw = search_mod.http_get(url, timeout=20)
    except Exception as e:
        if _is_js_heavy(url):
            return ("抓取失败（该域为 JS 渲染/反爬，服务器侧拿不到正文，**非通道断**）："
                    "请用 browser_open 打开页面、再用 browser_read 取正文，不要依赖 web_fetch。"
                    "原始错误：%s" % e)
        return f"抓取失败：{e}"
    try:
        text = _html_main_text(raw)
    except Exception:
        text = ""
    if len(text) < 200:
        # 抽不出正文主体（纯 JS 渲染页 / 极短页）
        if _is_js_heavy(url):
            return ("该页面为 JS 渲染/反爬，web_fetch 抽不出正文（**非通道断**）："
                    "请用 browser_open + browser_read 取内容；禁止 web_fetch 失败即写「数据待确认」了事。")
        # 回退旧的整页文本，别把内容弄丢
        text = re.sub(r'<script.*?</script>', ' ', raw, flags=re.S | re.I)
        text = re.sub(r'<style.*?</style>', ' ', text, flags=re.S | re.I)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = html_mod.unescape(text)
        text = re.sub(r'\s+', ' ', text).strip()
    return text[:TOOL_RESULT_LIMIT] if text else "页面无可用文本"


def _extract_office_text(path):
    """v4.66：从 Office 文档（docx/xlsx/pptx 等 zip 包）抽取可读文本，零依赖。
    返回文本字符串；解析失败返回 None（交给上层按普通文件读）。"""
    import zipfile, re, html
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if any(n.startswith("word/") for n in names):       # docx
                xmls = [n for n in names if n.startswith("word/") and n.endswith(".xml")]
            elif any(n.startswith("ppt/") for n in names):       # pptx
                xmls = [n for n in names
                        if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
            elif any(n.startswith("xl/") for n in names):        # xlsx
                xmls = [n for n in names if n.startswith("xl/") and n.endswith(".xml")]
            else:
                xmls = [n for n in names if n.endswith(".xml")]
            parts = []
            for n in xmls:
                try:
                    data = z.read(n).decode("utf-8", "ignore")
                except Exception:
                    continue
                texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", data, re.S)  # docx 文本节点
                if texts:
                    parts.append(" ".join(texts))
                else:
                    parts.append(re.sub(r"<[^>]+>", " ", data))
            raw = "\n".join(parts)
            raw = html.unescape(raw)
            raw = re.sub(r"[ \t]+", " ", raw)
            raw = re.sub(r"\n\s*\n+", "\n", raw)
            return raw.strip() or None
    except Exception:
        return None


def _extract_pdf_text(path):
    """v4.66：尝试用 PyPDF2 / pdfminer 抽 PDF 文本；都没有则返 None。"""
    try:
        from PyPDF2 import PdfReader
        try:
            r = PdfReader(path)
            out = [ (pg.extract_text() or "") for pg in r.pages ]
            return "\n".join(out).strip() or None
        except Exception:
            pass
    except Exception:
        pass
    try:
        from pdfminer.high_level import extract_text
        t = extract_text(path)
        return t.strip() or None
    except Exception:
        pass
    return None


def _tool_roots(app_dir):
    """文件工具的允许根目录集合。

    v4.164.0：运行数据归口到 WORKSPACE_DIR 后，相对路径要以它为准（与 Agent 执行
    命令的 cwd 一致）；同时保留 app_dir（APP_DIR）以兼容历史遗留的绝对路径引用。
    PRODUCTS_DIR 在 WORKSPACE_DIR 之内，无需单列。
    """
    out = []
    for r in (WORKSPACE_DIR, app_dir):
        try:
            if r:
                a = os.path.abspath(r)
                if a not in out:
                    out.append(a)
        except Exception:
            pass
    return out


def _within_roots(p, roots):
    """p 是否落在 roots 之一内。是则返回该根，否则 None。"""
    try:
        ap = os.path.abspath(p)
    except Exception:
        return None
    for r in roots:
        if ap == r or ap.startswith(r + os.sep):
            return r
    return None


# ==================== v4.191.0 批⑥：引用纪律下放 tool result ====================
# 行业结论（grounding 工程）：写在 system prompt 里的「不知道就说不知道」只是推荐、
# **非确定性约束**；模型回答前最后看到的是工具结果，指令必须放在工具结果里才真正生效。
# 本项目实证（2026-09-30 小臭 CHANGELOG 编造事件）：纪律原挂在 AGENT_SYS_APPEND 与
# agent.py 的 _internal 注入（都在消息层），会被后续 tool result 冲淡。
#
# 版面选择：agent.py 回传时用 compress() 裁到 TOOL_RESULT_LIMIT（6000），compress 是
# 「保留前 65% + 后 35%」的双端保留策略，但极端情况会二次硬截断丢尾部，故**首尾各钉
# 一行**——任一端被裁，另一端仍在。
_READ_GROUNDING_HEAD = (
    "[读取纪律] 下面是 {name} 第 {a}~{b} 字符的原文（该文件共 {n} 字符）。"
    "凡未出现在下面原文中的内容，禁止凭你的记忆或参数知识补全——没有就说没有。\n\n"
)
_READ_TAIL_MORE = (
    "\n\n[文件共 {n} 字符，本次已读 {a}~{b} 段（{pct}%），还剩 {rest} 字符未读；"
    "用 offset={b} 继续读。未读完全文前禁止声称已读完整个文件。]"
)
_READ_TAIL_END = "\n\n[已读到文件末尾（共 {n} 字符，本次覆盖 {a}~{b}），全文已读全。]"
_READ_TAIL_ONESHOT = "\n\n[文件共 {n} 字符，本次已全部读入（一次读全）。]"
# 空结果/失败结果显式化（行业三大反例之首：返回空串或泛化错误，模型当成读到了然后脑补）
_READ_NOT_FOUND = (
    "\n\n[RESULT NOT FOUND] 本次读取没有取得任何文件内容。"
    "禁止用你的记忆或参数知识回答与该文件相关的问题——如实回答『没有读到』，"
    "或换一个确实可用的路径重新 read_file。凭记忆描述文件内容属于编造。"
)


def _read_fail(msg):
    """失败/空结果统一包装：原错误信息 + 显式 NOT FOUND 指令。

    **前缀保持不变**——ui.py 批③的 _READ_FAIL_PREFIX 靠前缀判定
    「读失败不作真值源」，追加在尾部不影响该判定。
    """
    return msg + _READ_NOT_FOUND


def tool_read_file(app_dir, path, offset=0, limit=None):
    if not path:
        return _read_fail("未提供路径")
    if _is_sensitive_file(path):
        return _read_fail("已阻止：目标文件是敏感配置（可能含 API key），禁止读取。")
    roots = _tool_roots(app_dir)
    # v4.164.0：相对路径优先按 WORKSPACE_DIR 解析（与 Agent cwd 一致），
    # 未命中再回退 app_dir（兼容历史相对路径）。
    p = os.path.abspath(os.path.join(WORKSPACE_DIR, path))
    if not os.path.isfile(p):
        _alt = os.path.abspath(os.path.join(app_dir, path))
        if os.path.isfile(_alt):
            p = _alt
    # v4.66：模型若只给文件名（不含斜杠），自动去 incoming/ 子目录找（附件都落那）
    if not os.path.isfile(p) and "/" not in path and "\\" not in path:
        _base = os.path.basename(path)
        for _r in roots:
            cand = os.path.abspath(os.path.join(_r, "incoming", _base))
            if os.path.isfile(cand):
                p = cand
                break
    if _within_roots(p, roots) is None:
        return _read_fail(f"拒绝：只能读取工作区目录内文件（{WORKSPACE_DIR}）")
    if not os.path.isfile(p):
        return _read_fail(f"文件不存在：{p}")
    ext = os.path.splitext(p)[1].lower()
    # v4.66：Office / PDF 抽出真实文本，否则按二进制读会是一堆乱码 zip
    text = None
    if ext in (".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt"):
        text = _extract_office_text(p)
    if text is None and ext == ".pdf":
        text = _extract_pdf_text(p)
    if text is None:
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception as e:
            return _read_fail(f"读取失败：{e}")

    # v4.93 分段读取：大文件不再一次性截断丢尾部，支持 offset/limit 续读
    try:
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        offset = 0
    if limit is None:
        limit = TOOL_READ_LIMIT
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = TOOL_READ_LIMIT

    total = len(text)
    if offset >= total:
        return _read_fail(f"offset={offset} 已超出文件长度（文件共 {total} 字符）。")
    seg = text[offset:offset + limit]
    end = min(offset + limit, total)
    _nm = os.path.basename(p) or p
    head = _READ_GROUNDING_HEAD.format(name=_nm, a=offset, b=end, n=total)
    if end < total:
        _pct = int(end * 100 / total) if total else 100
        tail = _READ_TAIL_MORE.format(n=total, a=offset, b=end,
                                      pct=_pct, rest=total - end)
    elif offset > 0:
        tail = _READ_TAIL_END.format(n=total, a=offset, b=end)
    else:
        # offset=0 一次读全的小文件：原先无任何标记，模型分不清「读全」与「被截断」
        tail = _READ_TAIL_ONESHOT.format(n=total)
    return head + seg + tail


def tool_write_file(app_dir, path, content):
    if not path:
        return "未提供路径"
    roots = _tool_roots(app_dir)
    # v4.164.0：相对路径写进 WORKSPACE_DIR（运行数据归口），不再落 app_dir（dist）
    p = os.path.abspath(os.path.join(WORKSPACE_DIR, path))
    if _within_roots(p, roots) is None:
        return f"拒绝：只能写入工作区目录内（{WORKSPACE_DIR}）"
    try:
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已写入 {len(content)} 字符到 {p}"
    except Exception as e:
        return f"写入失败：{e}"


# 危险命令预检（纵深防御）。
# 与 agent.py 的 `_confirm_text` 确认闸门互补：确认层负责「用户知情」，
# 本层负责「系统级毁灭性操作硬拦截」——即便确认被绕过或模型自作主张，
# 格式化磁盘 / 删系统盘 / 关机 / 动引导 等仍被底层闸住（授权铁律「默认最保守」）。
# 仅针对系统级毁灭性模式，不拦工作区内的正常删改（精准匹配系统盘 / 根 / 磁盘格式化）。
_DANGEROUS_CMD_PATTERNS = (
    (r"\bformat\s+[a-z]:", "格式化磁盘 (format X:)"),
    (r"\bshutdown\b", "关机/重启 (shutdown)"),
    (r"stop-computer|restart-computer", "关机/重启 (PowerShell)"),
    (r"\brm\s+-rf\s+(/|[a-z]:\\|/[a-z])", "递归删除根/系统盘 (rm -rf / 或 X:\\)"),
    (r"\bdel\s+(/[fsq]+\s+)*[a-z]:\\", "删除系统盘文件 (del X:\\)"),
    # 顺序无关：drive 与 -recurse 哪个在前都能命中（lookahead 双向校验）。
    (r"remove-item\s+(?=.*[a-z]:\\).*-recurse", "递归删除磁盘 (PowerShell Remove-Item -Recurse X:)"),
    (r"\brm\s+[a-z]:\\?\s.*-recurse", "递归删除磁盘 (PowerShell rm 别名 -Recurse X:)"),
    (r"\bdiskpart\b", "磁盘分区操作 (diskpart)"),
    (r"\breg\s+delete\s+(hklm|hkcr)", "删除注册表键 (reg delete HKLM/HKCR)"),
    (r"\btakeown\s+/f\s+[a-z]:\\windows", "夺取系统目录所有权 (takeown)"),
    (r"\bicacls\s+[a-z]:\\.*/grant", "篡改系统目录权限 (icacls /grant)"),
    (r"\bbcdedit\b|\bbootrec\b", "修改引导记录 (bcdedit/bootrec)"),
    (r"\bmkfs\b", "创建文件系统 (mkfs)"),
    # G4 第二步：终止系统关键进程。结构化工具（process_kill / app_kill）那一层已
    # 硬拒绝，这里堵同一条路的**文本入口** —— 否则一条
    # `run_command("taskkill /IM lsass.exe /F")` 就绕过了工具层名单。
    # 只拦**按镜像名**的形式（/IM、-Name）；`taskkill /PID <n>` 这种精确形式不拦
    # （不连带、也可能是用户明确要求的运维动作，如按 PID 重启任务栏）。
    # 诚实说：文本级检测天生可绕（run_python 里拼 argv 就绕过了），这层只负责
    # 「拦住最可能的误操作」，真正的兜底是工具层那两道。
    (r"\btaskkill\b[^\n]*\b(?:lsass|csrss|winlogon|wininit|smss|services|svchost|explorer|dwm)\.exe\b",
     "终止系统关键进程 (taskkill)"),
    (r"\bstop-process\b[^\n]*\b(?:lsass|csrss|winlogon|wininit|smss|services|svchost|explorer|dwm)\b",
     "终止系统关键进程 (Stop-Process)"),
)


def _dangerous_command_check(command):
    """返回 None 表示安全；返回拒绝字符串表示命中系统级危险模式。

    仅拦截明确毁灭性的系统操作，正常开发命令（删工作区文件、重启服务、
    跑脚本）不会被误伤。命中后由调用方直接 return，绝不执行。
    """
    c = (command or "").lower()
    for pat, label in _DANGEROUS_CMD_PATTERNS:
        if re.search(pat, c):
            return (
                "⛔ 命令已被底层安全闸拦截：检测到系统级危险操作「" + label + "」。\n"
                "命中片段：" + command.strip()[:200] + "\n"
                "该操作可能破坏系统或致数据不可恢复。如需执行，请改用更精准的"
                "路径与参数，或在确认对话框中明确授权后重试。"
            )
    return None


# ③-B：PowerShell 在重定向输出时会把进度/错误等流序列化成 CLIXML
# （以 `#< CLIXML` 开头的 <Objs>...</Objs> 块）。progress 类是噪声直接丢弃；
# error 类则提取 <S>/<AV>/<T>/<ToString> 内可读文本，避免把原始 XML 丢给 LLM。
# 注意 CLIXML 元素带序列化属性（如 <S S="Error">），正则须允许属性；
# 文本里的 _x000D_/_x000A_ 是 XML 转义的 \r/\n，需还原。
_CLIXML_BLOCK = re.compile(r"#<\s*CLIXML\s*<Objs[\s\S]*?</Objs>")
_CLIXML_TEXT = re.compile(r"<(?:S|AV|T|ToString)(?:\s[^>]*)?>(.*?)</(?:S|AV|T|ToString)>", re.S)


def _strip_clixml(text):
    def _repl(m):
        block = m.group(0)
        if 'S="progress"' in block:
            return ""  # 进度流噪声直接丢弃
        parts = _CLIXML_TEXT.findall(block)
        txt = " ".join(p.strip() for p in parts if p.strip())
        txt = txt.replace("_x000D_", "\r").replace("_x000A_", "\n")
        return txt
    return _CLIXML_BLOCK.sub(_repl, text)


def _kill_proc_tree(proc):
    """审计修复 E9：超时后杀掉整棵进程树。subprocess.run 的 timeout 只 kill
    直接子进程（powershell/sh），它拉起的 python/ffmpeg 等孙进程成孤儿，
    继续占 CPU/文件锁，原返回文案"已终止"属谎报。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=10,
                           creationflags=_NO_WINDOW)
        else:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ②-A.1 / ②-A.3：输出防御性截断上限 + 流式读取与停止支持
_RAW_OUTPUT_HARD_LIMIT = 4 * 1024 * 1024  # 4MB 原始字节硬上限：防超大输出撑爆内存/整段塞进 LLM


def _hard_truncate_decode(raw):
    """对 stdout+stderr 合并原始字节做防御性截断解码。

    返回 (text, truncated, approx_bytes)：
    - 超过 _RAW_OUTPUT_HARD_LIMIT 时先按上限切字节再 decode（ignore 容错切到多字节中间），
      truncated=True 并附原始字节数，避免一次性构造巨串；
    - 否则按原逻辑 utf-8 → gbk 兜底 → ignore。
    """
    if not raw:
        return "", False, 0
    n = len(raw)
    if n > _RAW_OUTPUT_HARD_LIMIT:
        head = raw[:_RAW_OUTPUT_HARD_LIMIT]
        try:
            text = head.decode("utf-8")
        except UnicodeDecodeError:
            text = head.decode("utf-8", "ignore")
        return text, True, n
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gbk")
        except Exception:
            text = raw.decode("utf-8", "ignore")
    return text, False, n


def _clip_output(out, hard_trunc, approx_bytes, limit=None):
    """P2-2 修：统一的回传裁剪——**超限必附标记**。
    原版只给 >4MB 硬截断附标记；6000 字符 < len ≤ 4MB 的中等输出被静默切到
    TOOL_RESULT_LIMIT，模型拿半截当全量继续推理。现在两档都标：
    - 硬截断档（>4MB）：标『原始约 N 字节』
    - 超长档（>6000 字符）：标『完整 N 字符』
    标记追加在裁剪之后，保证可见。"""
    if limit is None:
        limit = TOOL_RESULT_LIMIT
    if len(out) <= limit:
        return out
    clipped = out[:limit]
    if hard_trunc:
        return clipped + (f"\n…[输出过大已截断：原始约 {approx_bytes:,} 字节，"
                          f"仅回传前 {limit} 字符]")
    return clipped + f"\n…[输出超长已截断：完整 {len(out):,} 字符，仅回传前 {limit} 字符]"


def _stream_collect(proc, timeout, progress=None, should_stop=None, stop_event=None):
    """流式读取子进程 stdout/stderr（实时经 progress 回显），支持停止信号。

    返回 (stdout_bytes, stderr_bytes, outcome)，outcome ∈ {'done','timeout','stopped'}。
    v4.186.0（②-A.3）：替代一次性 communicate，长任务可即时看到中间输出、且能被用户停止。
    """
    import threading
    _so, _se = [], []

    def _reader(src, sink):
        try:
            for line in iter(src.readline, b""):
                sink.append(line)
                if progress and callable(progress):
                    try:
                        progress(line.decode("utf-8", "ignore"))
                    except Exception:
                        pass
        except Exception:
            pass

    t_out = threading.Thread(target=_reader, args=(proc.stdout, _so), daemon=True)
    t_err = threading.Thread(target=_reader, args=(proc.stderr, _se), daemon=True)
    t_out.start()
    t_err.start()
    t0 = time.time()
    try:
        while True:
            if proc.poll() is not None:
                break
            if should_stop and callable(should_stop) and should_stop():
                _kill_proc_tree(proc)
                return b"".join(_so), b"".join(_se), "stopped"
            if stop_event and stop_event.is_set():
                _kill_proc_tree(proc)
                return b"".join(_so), b"".join(_se), "stopped"
            if (time.time() - t0) > timeout:
                _kill_proc_tree(proc)
                return b"".join(_so), b"".join(_se), "timeout"
            time.sleep(0.05)
    finally:
        t_out.join(timeout=5)
        t_err.join(timeout=5)
    return b"".join(_so), b"".join(_se), "done"


def tool_run_command(app_dir, command, cwd=None, env=None, progress=None,
                     stop_event=None, should_stop=None):
    if not command or not command.strip():
        return "未提供命令"
    # 危险命令预检（纵深防御，与 agent 层确认闸门互补）
    _deny = _dangerous_command_check(command)
    if _deny:
        return _deny
    # v4.164.0：命令的工作目录由 app_dir（= exe 目录 = dist = 分发源）改为 WORKSPACE_DIR
    # ——Agent 用相对路径写出的文件（output/notes/pages/multi_platform…）此前全堆进
    # dist，会被连带打包分发。资源类查找仍走 app_dir，不受影响。
    try:
        _ws = WORKSPACE_DIR
        os.makedirs(_ws, exist_ok=True)
    except Exception:
        _ws = app_dir
    # ②-A.2：自定义工作目录（要求真实存在的目录；否则回退默认工作区并给提示）
    _run_cwd = _ws
    _cwd_warn = ""
    if cwd:
        try:
            if os.path.isdir(cwd):
                _run_cwd = cwd
            else:
                _cwd_warn = f"（指定的 cwd 不存在，已回退到默认工作区 {_ws}）"
        except Exception:
            _cwd_warn = f"（指定的 cwd 无效，已回退到默认工作区 {_ws}）"
    # ②-A.2：环境变量与当前进程环境合并（不全量替换，避免丢 PATH 等系统变量）
    _run_env = dict(os.environ)
    if env and isinstance(env, dict):
        _run_env.update({str(k): str(v) for k, v in env.items()})
    try:
        import platform, base64
        # v4.60：Windows 上强制走 PowerShell，避免 cmd.exe 不认识 Get-ChildItem 等命令
        if platform.system() == "Windows":
            # ③-B 健壮性（v4.185.0）：
            # 1) -EncodedCommand（UTF-16LE base64）取代 -Command，整条命令按字节传入，
            #    彻底规避引号/管道/特殊字符在 PowerShell 解析器里被二次转义导致的命令变形；
            # 2) 前缀强制 [Console]::OutputEncoding 为 UTF-8，使捕获到的字节恒为 UTF-8，
            #    消除「中文 GBK 字节恰好是合法 UTF-8」造成的静默乱码歧义；
            # 3) -NoProfile -NonInteractive 提速并避免配置/交互侧意外阻塞。
            _ps_setup = (
                "$ProgressPreference='SilentlyContinue';"
                "$OutputEncoding=[System.Text.Encoding]::UTF8;"
                "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
            )
            _enc = base64.b64encode((_ps_setup + command).encode("utf-16-le")).decode("ascii")
            proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-EncodedCommand", _enc],
                cwd=_run_cwd, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=_NO_WINDOW,
                env=_run_env,
            )
        else:
            # POSIX 维持 shell 既定语义（C1 只收敛 Windows 注入面）；
            # start_new_session 使子进程独立成组，超时可 killpg 杀全树。
            proc = subprocess.Popen(
                command, shell=True, cwd=_run_cwd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True, env=_run_env,
            )
    except Exception as e:
        return f"命令执行失败：{e}"
    # ②-A.3：流式读取（实时回显 + 可中断），替代一次性 communicate
    try:
        stdout, stderr, _outcome = _stream_collect(
            proc, 60, progress=progress,
            should_stop=should_stop, stop_event=stop_event)
    except Exception as e:
        return f"命令执行失败：{e}"
    if _outcome == "timeout":
        _kill_proc_tree(proc)
        return "命令执行超时（>60s），已连同其子进程一并终止"
    if _outcome == "stopped":
        _kill_proc_tree(proc)
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        return "⏹ 已停止（用户请求）"
    raw = (stdout or b"") + (stderr or b"")
    # ①②-A.1：先按硬上限做防御性截断解码，避免构造巨串
    out, _trunc, _approx = _hard_truncate_decode(raw)
    # ③-B：剔除 PowerShell 进度流序列化噪声、并把错误 CLIXML 还原为可读文本
    # （progress 类丢弃、error 类提取 <S>/<AV>/<T> 内文），详见 _strip_clixml。
    out = _strip_clixml(out)
    if not out.strip():
        out = f"（命令已执行，退出码 {proc.returncode}，无输出）"
        if proc.returncode != 0:
            out += "，可能执行失败"
    elif proc.returncode != 0:
        # ③-B：非零退出码显式标注，让 LLM/agent 连续失败护栏能正确识别失败
        out = out + f"\n[命令退出码 {proc.returncode}，可能执行失败]"
    if _trunc:
        # ②-A.1：原始字节超硬上限，标记已截断（先裁剪到 LIMIT 再追加标记，确保可见）
        out = _clip_output(out, True, _approx)
    else:
        # P2-2：4MB 以下但超回传上限的中等输出同样要标记（原版静默切 6000）
        out = _clip_output(out, False, 0)
    if _cwd_warn:
        out += "\n" + _cwd_warn
    return out




def snapshot_workspace(app_dir=None):
    """Capture all files in workspace as relative paths for before/after diff.

    v4.164.0：默认基准由 APP_DIR 改为 WORKSPACE_DIR（运行数据归口处）。
    """
    if app_dir is None:
        app_dir = WORKSPACE_DIR
    result = set()
    base = os.path.abspath(app_dir)
    for dirpath, _, filenames in os.walk(base):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = _safe_relpath(full, base)
            result.add(rel)
    return result


def classify_kind(rel):
    ext = os.path.splitext(rel)[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
        return "image"
    if ext in (".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".pdf"):
        return "doc"
    return "file"


# ---------- run_python 后端解释器探测（v4.165.0）----------
# 历史坑：v4.165.0 前这里硬编码了开发机的 Python 绝对路径 —— 换台机器必然失效，
# 且属于「机型耦合」（与本项目「脱离本机 / 可换机」的目标相反）。
# 现在按「配置指定 → PATH → 常见安装位置」探测；打包态不再把 sys.executable 当候选
# （frozen 时它指向 exe 自身，不是解释器）。
#
# 另一个坑：PATH 里第一个 python 未必是好用的那个（例如某个只跑脚本的隔离环境，
# 第三方库全缺）。所以候选要**逐个体检**，优先选「装了更多 run_python 常用库」的，
# 避免「做 PPT / 数据分析」这类任务因为选错解释器而报 ImportError。
_PY_OPTIONAL_LIBS = ("pptx", "docx", "pandas", "openpyxl", "matplotlib", "numpy")
# 一次探测同时拿回「真实解释器路径 + 缺失库清单」，避免每个候选跑两遍
_PY_PROBE_CODE = (
    "import sys, importlib.util as u;"
    "miss=[m for m in %r if u.find_spec(m) is None];"
    "sys.stdout.write(sys.executable + '||' + ' '.join(miss))"
) % (_PY_OPTIONAL_LIBS,)
_PY_STATUS_CACHE = {}


def _probe_python(exe):
    """探测解释器：返回 (是否可用, 真实解释器路径, 缺失库列表)。"""
    if not exe:
        return False, "", []
    try:
        r = subprocess.run([exe, "-c", _PY_PROBE_CODE], capture_output=True,
                           timeout=20, creationflags=_NO_WINDOW)
        if r.returncode != 0 or not r.stdout:
            return False, "", []
        out = r.stdout.decode("utf-8", "replace").strip()
        real, _, miss = out.partition("||")
        return True, (real.strip() or exe), [m for m in miss.split() if m]
    except Exception:
        return False, "", []


def _looks_like_python(exe):
    """该路径是否是可用的 Python 解释器。"""
    return _probe_python(exe)[0]


def _python_candidates(cfg=None):
    """run_python 后端解释器候选（优先级从高到低）。"""
    out = []

    def _add(p):
        if p and p not in out:
            out.append(p)

    # ① 配置里显式指定（解决非标准安装 / 多 Python 环境）
    try:
        custom = (cfg or {}).get("python_exe") or ""
        if isinstance(custom, str) and custom.strip() and os.path.isfile(custom.strip()):
            _add(custom.strip())
    except Exception:
        pass
    # ② PATH
    for name in ("python", "python3"):
        try:
            _add(shutil.which(name))
        except Exception:
            pass
    # ③ 常见安装位置（覆盖「装了但没加 PATH」）
    bases = []
    la = os.environ.get("LOCALAPPDATA")
    if la:
        bases.append(os.path.join(la, "Programs", "Python"))
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        b = os.environ.get(env)
        if b:
            bases.append(b)
    bases.append("C:" + os.sep)
    for base in bases:
        try:
            for d in sorted(glob.glob(os.path.join(base, "Python3*")), reverse=True):
                _add(os.path.join(d, "python.exe"))
        except Exception:
            continue
    # ④ 源码态 sys.executable 就是真解释器；打包态它是 exe 自身，必须排除
    if not getattr(sys, "frozen", False):
        _add(sys.executable)
    return out


def _resolve_python_exe(cfg=None, detail=False):
    """挑一个可用解释器：优先「装了更多常用库」的那个。

    detail=True 时返回 (exe, 缺失库列表)；否则只返回 exe（找不到为 None）。
    用户显式指定的解释器优先（配置里指定就该听他的），其余按缺库多少排序。
    """
    cands = _python_candidates(cfg)
    best, best_miss = None, None
    for idx, c in enumerate(cands):
        ok, real, miss = _probe_python(c)
        if not ok:
            continue
        if best is None or len(miss) < len(best_miss):
            best, best_miss = c, miss
            if idx == 0 and not miss:
                break          # 用户指定的解释器且库齐全，不必再找
        if not best_miss:
            break              # 找到库齐全的，就是最优
    if detail:
        return best, list(best_miss or [])
    return best


def python_runtime_status(cfg=None, refresh=False):
    """run_python 能力体检（供 UI 诚实标注，不假装可用）。

    返回 {"available": bool, "exe": str, "reason": str, "missing_libs": [...]}
    同进程内缓存一次；refresh=True 强制重探。
    """
    if not refresh and _PY_STATUS_CACHE:
        return dict(_PY_STATUS_CACHE)
    exe, miss = _resolve_python_exe(cfg, detail=True)
    if not exe:
        st = {"available": False, "exe": "", "missing_libs": [],
              "reason": "未找到 Python 解释器（未安装，或未加入 PATH）"}
    else:
        st = {"available": True, "exe": exe, "missing_libs": miss, "reason": ""}
    _PY_STATUS_CACHE.clear()
    _PY_STATUS_CACHE.update(st)
    return dict(st)


def tool_run_python(app_dir, code, cfg=None, progress=None,
                    stop_event=None, should_stop=None):
    """代码解释器：用真实 Python 解释器在独立工作区执行代码，可 import 任意已装库。

    历史：v4.50.1 前用 RestrictedPython 沙箱，刻意剔除 __import__ 且模块白名单极小，
    导致 `from pptx import ...` 直接报 ImportError: __import__ not found，"做PPT/数据分析"
    类任务彻底失效。改为子进程执行（与 run_command 同机制，已验证 pptx OK）后恢复能力；
    危险操作仍由权限引擎在执行前弹确认，安全边界不变。

    产物落在用户数据目录 workspace，避免污染 app 目录；上报绝对路径便于交付物打开。
    返回 (output_str, [(abs_path, kind, name), ...])。
    """
    if not code or not code.strip():
        return "未提供代码", []

    # P0（v4.186.0 审查 P0-1）：③-A 系统级毁灭硬拦截补齐到 run_python。
    # 原 `_dangerous_command_check` 只在 run_command 调用——会话信任后模型
    # 改走 run_python 就能零确认执行 format/shutdown/rm -rf（run_python 能力
    # superset 于 run_command，墙却只在 shell 路径）。Python 源码里的命令
    # 字符串（os.system("format d:") / subprocess...rm -rf /）同样过模式表。
    # 设计意图不变：本闸为 deny（确认也绕不过），正常开发命令不误伤。
    _deny = _dangerous_command_check(code)
    if _deny:
        return _deny, []

    exe = _resolve_python_exe(cfg)
    if not exe:
        st = python_runtime_status(cfg)
        return (f"代码执行不可用：{st['reason']}。"
                "可在「设置」里指定 Python 解释器路径后重试，或安装 Python 并加入 PATH。", [])

    # 独立工作区：产物默认落这里，不污染 app 目录
    # v4.108.1：v4.100 脱敏残留把 USER_DATA_DIR 写成了 AgentDesktop/workspace，
    # run_python 产物落进了空壳目录——改回真实用户数据目录（小臭玩AI）。
    ws = os.path.join(USER_DATA_DIR, "workspace")
    os.makedirs(ws, exist_ok=True)
    # 审计修复 E8：秒级文件名在同秒并发（首次确认后 trust_tool → _run_concurrent
    # 可并行两个 run_python）时互相覆盖，先完成者 finally 删脚本会删掉对方正在
    # 执行的文件 → "can't open file"。加毫秒+随机后缀（同 _save_gen_image v4.120 做法）。
    import uuid as _uuid
    fname = (f"run_{datetime.now().strftime('%Y%m%d%H%M%S%f')[:-3]}"
             f"_{_uuid.uuid4().hex[:6]}.py")
    fpath = os.path.join(ws, fname)
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(code)
    except Exception as e:
        return f"写临时文件失败：{e}", []

    before = snapshot_workspace(ws)
    try:
        # v4.184.0：注入 PYTHONIOENCODING=utf-8，避免子进程在 GBK 控制台下
        # print 中文抛 UnicodeEncodeError（与 browser_control_tools/release_check 同款范式）。
        _py_env = dict(os.environ)
        _py_env["PYTHONIOENCODING"] = "utf-8"
        # ③-B：改用 Popen + communicate（与 run_command 同机制），超时可直接拿到 pid
        # 杀整棵进程树，避免用户代码拉起的 ffmpeg / 子 python 成孤儿（run_command 的 E9 修复，
        # 此前 run_python 漏了，导致超时后后台进程残留）。
        proc = subprocess.Popen([exe, fpath], cwd=ws,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=_NO_WINDOW, env=_py_env)
    except Exception as e:
        return f"执行失败：{e}", []

    try:
        stdout, stderr, _outcome = _stream_collect(
            proc, 120, progress=progress,
            should_stop=should_stop, stop_event=stop_event)
        if _outcome == "timeout":
            _kill_proc_tree(proc)
            return "代码执行超时（>120s），已连同其子进程一并终止。可把任务拆小或分步执行。", []
        if _outcome == "stopped":
            _kill_proc_tree(proc)
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            return "⏹ 已停止（用户请求）", []
        raw = (stdout or b"") + (stderr or b"")
        # ②-A.1：先按硬上限做防御性截断解码
        out, _trunc, _approx = _hard_truncate_decode(raw)
        if not out.strip():
            out = f"（已执行，退出码 {proc.returncode}，无输出）"
        # P2-2：统一裁剪（>6000 字符必附标记；硬截断档标原始字节数）
        out = _clip_output(out, _trunc, _approx)
    except Exception as e:
        return f"执行失败：{e}", []
    finally:
        try:
            os.remove(fpath)
        except Exception:
            pass

    after = snapshot_workspace(ws)
    deliverables = []
    for nf in sorted(after - before):
        full = os.path.join(ws, nf)
        deliverables.append((full, classify_kind(nf), os.path.basename(nf)))

    return out, deliverables


# 保留旧函数供内部兼容
# 保留旧函数供内部兼容
def _tool_run_python_legacy(app_dir, code):
    """旧的子进程执行方式（保留兼容）"""
    if not code or not code.strip():
        return "未提供代码", []
    exe = _resolve_python_exe()
    if not exe:
        return "未找到 Python 解释器，请先安装 Python 并加入 PATH", []
    gen_dir = os.path.join(WORKSPACE_DIR, "gen")
    os.makedirs(gen_dir, exist_ok=True)
    # 审计修复 E8：同款秒级撞名问题（legacy 路径两处调用同秒也会互覆盖）
    import uuid as _uuid
    fname = (f"run_{datetime.now().strftime('%Y%m%d%H%M%S%f')[:-3]}"
             f"_{_uuid.uuid4().hex[:6]}.py")
    fpath = os.path.join(gen_dir, fname)
    frel = _safe_relpath(fpath, app_dir)
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(code)
    except Exception as e:
        return f"写临时文件失败：{e}", []
    before = snapshot_workspace(WORKSPACE_DIR)
    try:
        _ws2 = WORKSPACE_DIR
        try:
            os.makedirs(_ws2, exist_ok=True)
        except Exception:
            _ws2 = app_dir
        # v4.184.0：同主路径，注入 PYTHONIOENCODING=utf-8 根治中文 GBK 崩。
        _py_env = dict(os.environ)
        _py_env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run([exe, fpath], cwd=_ws2,
                               capture_output=True, timeout=60,
                               creationflags=_NO_WINDOW, env=_py_env)
    except subprocess.TimeoutExpired:
        return "代码执行超时（>60s），已终止", []
    except Exception as e:
        return f"执行失败：{e}", []
    raw = (proc.stdout or b"") + (proc.stderr or b"")
    try:
        out = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            out = raw.decode("gbk")
        except Exception:
            out = raw.decode("utf-8", "ignore")
    after = snapshot_workspace(WORKSPACE_DIR)
    py_deliverables = []
    for nf in sorted(after - before):
        if nf == frel:
            continue
        py_deliverables.append((nf, classify_kind(nf), os.path.basename(nf)))
    if not out.strip():
        out = f"（已执行，退出码 {proc.returncode}，无输出）"
    return out[:TOOL_RESULT_LIMIT], py_deliverables


def tool_rag_index(cfg, app_dir, args, progress=None):
    """RAG 索引工具：将文件或目录索引到知识库"""
    path = args.get("path", "")
    if not path:
        return "请提供要索引的文件或目录路径", [], []
    import config as cfg_mod
    if cfg_mod.rag_store is None:
        return "RAG 知识库未初始化", [], []
    p = Path(path)
    if not p.exists():
        return f"路径不存在: {path}", [], []
    results = []
    if p.is_file():
        results.append(cfg_mod.rag_store.index_file(str(p), force=True))
    else:
        for f in p.rglob("*"):
            if f.is_file() and f.suffix.lower() in ('.txt', '.md', '.py', '.pdf', '.docx'):
                results.append(cfg_mod.rag_store.index_file(str(f), force=True))
    return f"索引完成:\n" + "\n".join(f"  - {r}" for r in results if r), [], []


def tool_rag_search(cfg, app_dir, args, progress=None):
    """RAG 搜索工具：在知识库中搜索"""
    query = args.get("query", "")
    top_k = args.get("top_k", 5)
    if not query:
        return "请提供搜索查询", [], []
    import config as cfg_mod
    if cfg_mod.rag_store is None:
        return "RAG 知识库未初始化", [], []
    results = cfg_mod.rag_store.search(query, top_k)
    if not results:
        return "未找到相关内容", [], []
    lines = []
    for source, text, distance in results:
        preview = text[:300].replace('\n', ' ')
        lines.append(f"[{source}] (相似度: {1-distance:.2f})\n  {preview}...")
    return "检索结果:\n" + "\n\n".join(lines), [], []


def tool_screenshot(cfg, app_dir, args, progress=None):
    """截图工具：全屏或活动窗口截取"""
    mode = args.get("mode", "fullscreen")
    save_path = args.get("save_path", "")

    # v4.129：走分层目录（dated 时 产物/YYYY-MM-DD/<项目>/截图）
    outputs = Path(_products_dir(cfg, "screenshot"))
    outputs.mkdir(parents=True, exist_ok=True)

    try:
        from PIL import ImageGrab
    except ImportError:
        return "需要 Pillow: pip install Pillow", [], []

    img = ImageGrab.grab()
    if mode == "active_window":
        try:
            import pygetwindow as gw
            win = gw.getActiveWindow()
            if win:
                img = ImageGrab.grab(bbox=(win.left, win.top, win.right, win.bottom))
        except ImportError:
            pass  # 降级为全屏

    from datetime import datetime as dt
    ts = dt.now().strftime("%Y%m%d_%H%M%S_%f")
    if save_path:
        target = Path(save_path)
    else:
        target = outputs / f"screenshot_{ts}.png"

    img.save(str(target))
    return f"截图已保存: {target}", [str(target)], []


def tool_image_gen(cfg, app_dir, prompt, size=None, progress=None):
    """多后端生图：根据 cfg["image_gen_provider"] 选择后端。

    支持 gateway / deepseek / local_stability 三种模式。
    size 为可选 "WxH" 字符串（如 "1024x768"），不传则用 cfg["image_gen_size"]。
    成功返回 (rel_path, 'image', filename)，失败返回错误字符串。
    """
    if progress:
        progress("🖼 生成图片中…（可能需数十秒，可随时点停止）")
    if not prompt:
        return "未提供图片描述"

    provider = cfg.get("image_gen_provider", "gateway")
    model = cfg.get("image_gen_model", "agnes")
    if size is None:
        size = cfg.get("image_gen_size", "1024x768")

    if provider == "gateway":
        return _gen_gateway(cfg, app_dir, prompt, model, size)
    elif provider == "agnes":
        return _gen_agnes_image(cfg, app_dir, prompt, size, progress=progress)
    elif provider == "deepseek":
        return _gen_siliconflow(cfg, app_dir, prompt, size)
    elif provider == "local_stability":
        return _gen_local_sd(cfg, app_dir, prompt, size)
    else:
        return f"未知生图后端：{provider}（支持 gateway / agnes / deepseek / local_stability）"


def _save_gen_image(app_dir, data_or_path, is_bytes=False, cfg=None):
    """将生图结果存入产物目录「图片」子目录，返回 (rel, 'image', name)。

    v4.129：目录改由 product_layout 计算（dated 时 产物/YYYY-MM-DD/<项目>/图片）。
    """
    img_dir = _products_dir(cfg, "image")
    os.makedirs(img_dir, exist_ok=True)
    # v4.120：并发生图同秒撞名互相覆盖（实测两次 image_gen 同秒完成 →
    # 用户看到两张一样的图）——毫秒 + 4 位随机后缀保证唯一。
    import uuid as _uuid
    stamp = (datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]
             + "_" + _uuid.uuid4().hex[:4])
    fpath = os.path.join(img_dir, f"img_{stamp}.png")
    if is_bytes:
        with open(fpath, "wb") as f:
            f.write(data_or_path)
    elif data_or_path.startswith("http://") or data_or_path.startswith("https://"):
        raw = search_mod.download_bytes(data_or_path)
        with open(fpath, "wb") as f:
            f.write(raw)
    elif os.path.isfile(data_or_path):
        ext = os.path.splitext(data_or_path)[1] or ".png"
        fpath = os.path.join(img_dir, f"img_{stamp}{ext}")
        shutil.copyfile(data_or_path, fpath)
    else:
        raise ValueError(f"无法处理的生图结果：{data_or_path}")
    rel = _safe_relpath(fpath, app_dir)
    return (rel, "image", os.path.basename(rel))


def _gen_gateway(cfg, app_dir, prompt, model, size=None):
    """gateway 模式：调当前 base_url + /image 端点（保持原有逻辑）"""
    url = cfg["base_url"].rstrip("/") + "/image"
    payload = {
        "prompt": prompt,
        "model": model,
    }
    if size:
        payload["size"] = size
    payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    api_key = cfg.get("api_key", "")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as e:
        return f"生图请求失败（gateway）：{e}"
    content = (data.get("content") or "").strip()
    if not content or data.get("ok") is False:
        return f"生图失败：{content}"
    try:
        return _save_gen_image(app_dir, content)
    except Exception as e:
        return f"保存图片失败：{e}（原始返回：{content}）"


def _gen_siliconflow(cfg, app_dir, prompt, size=None):
    """deepseek 模式：尝试调硅基流动的图片生成接口。
    DeepSeek 官方没有生图 API，因此如果当前 base_url 指向硅基流动则调其生图接口，
    否则返回不支持提示。
    """
    base_url = cfg.get("base_url", "")
    if "siliconflow" not in base_url:
        return (
            "当前模型不支持生图。要使用生图功能，请：\n"
            "1) 将 image_gen_provider 设为 'gateway' 并启动免费网关；\n"
            "2) 或将模型切换为硅基流动，并将 image_gen_provider 设为 'deepseek'；\n"
            "3) 或使用本地 Stable Diffusion WebUI（image_gen_provider='local_stability'）。"
        )
    url = base_url.rstrip("/") + "/image/generations"
    model = cfg.get("image_gen_model", "stabilityai/stable-diffusion-xl-base-1.0")
    payload = {
        "model": model,
        "prompt": prompt,
    }
    if size:
        payload["size"] = size
    payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    api_key = cfg.get("api_key", "")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as e:
        return f"硅基流动生图请求失败：{e}"
    # 硅基流动返回格式：{"images": [{"url": "..."}]} 或 {"data": [{"url": "..."}]}
    images = data.get("images") or data.get("data") or []
    if not images:
        return f"硅基流动生图失败：{data.get('message', data)}"
    img_url = images[0].get("url", "")
    if not img_url:
        return f"硅基流动生图失败：未返回图片 URL"
    try:
        return _save_gen_image(app_dir, img_url)
    except Exception as e:
        return f"保存图片失败：{e}"


def _gen_local_sd(cfg, app_dir, prompt, size=None):
    """local_stability 模式：调用本地 Stable Diffusion WebUI API。
    默认地址 cfg["sd_webui_url"]，端点 /sdapi/v1/txt2img。
    """
    sd_url = cfg.get("sd_webui_url", "http://127.0.0.1:7860").rstrip("/")
    url = sd_url + "/sdapi/v1/txt2img"
    width, height = 512, 512
    if size and "x" in size:
        try:
            width, height = (int(x) for x in size.split("x", 1))
        except ValueError:
            width, height = 512, 512
    payload = json.dumps({
        "prompt": prompt,
        "steps": 20,
        "width": width,
        "height": height,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.URLError as e:
        return f"本地 SD WebUI 连接失败：{e}（请确认 SD WebUI 已启动并开启了 --api 参数，地址：{sd_url}）"
    except Exception as e:
        return f"本地 SD 生图请求失败：{e}"
    images = data.get("images", [])
    if not images:
        return f"本地 SD 生图失败：{data.get('error', '未返回图片')}"
    import base64
    try:
        img_bytes = base64.b64decode(images[0])
        return _save_gen_image(app_dir, img_bytes, is_bytes=True)
    except Exception as e:
        return f"保存本地 SD 图片失败：{e}"


_DLG_BRACKET_RE = re.compile(r"[（(\[【][^）)\]】]*[）)\]】]|\*[^*]{1,10}\*")
_DLG_LEAD_RE = re.compile(r"^\s*(?:台词|对白|旁白|line|dialogue)\s*[:：]\s*", re.I)
_DLG_JUNK_RE = re.compile(r"[\"'“”‘’*#`《》<>｜|]")


def _clean_dialogue(line, max_len=60):
    """把台词洗成「能安全念出来的纯中文单句」，洗不出来返回 ""。

    与 video_pipeline.clean_dialogue / core.agnes.clean_dialogue 同规则。
    剥掉会被照本宣科念出来的杂质：换行、（叹气）动作描写、引号、英文残留。
    """
    if not line:
        return ""
    d = str(line).strip()
    if not d:
        return ""
    d = d.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    d = _DLG_LEAD_RE.sub("", d)
    d = _DLG_BRACKET_RE.sub("", d)
    d = _DLG_JUNK_RE.sub("", d)
    d = re.sub(r"\s+", " ", d).strip(" 　:：，,。.、;；-—")
    if not d:
        return ""
    han = len(re.findall(r"[\u4e00-\u9fff]", d))
    if han < 2 or han / max(len(d), 1) < 0.20:
        return ""
    if len(d) > max_len:
        cut = d[:max_len]
        for p in ("。", "！", "？", "，", "、"):
            k = cut.rfind(p)
            if k >= max_len // 2:
                cut = cut[:k + 1]
                break
        d = cut
    return d


# v4.229.0：生视频面板「提示词内带台词」的拆分规则。
# 生视频面板本就没有台词输入框（口播/数字人归 digital_twin_panel 管），但大哥的实际
# 用法是把台词一起写进提示词框 —— 此前这些台词被当作**画面描述**送进 prompt，
# agnes-video-2.5-flash 不会念，只会把它理解成场景文字（甚至渲染成画面字幕）。
# tool_video_gen 的 dialogue= 参数本就能让内核配音（core 内唯一注入点），
# 面板却从未传过 —— 这就是「提示词里写台词不起作用」的根因。
_DLG_LINE_RE = re.compile(
    r"^\s*[\[【（(]?\s*(?:台词|对白|旁白|口播|line|dialogue)"
    r"\s*[】）)\]]?\s*[:：]?\s*(.+?)\s*$",
    re.I)
# 整行被中文引号包住 = 用户把它当成「说出来的话」而非画面（如 “大家好，今天聊聊…”）
_DLG_QUOTED_RE = re.compile(r'^\s*["“「『]\s*(.+?)\s*["”」』]\s*$')
# 这些是运镜/画面术语，即便被引号包住也不是台词
_DLG_SCENE_WORDS = ("镜头", "画面", "运镜", "特写", "中景", "近景", "全景", "俯拍", "跟拍",
                    "光线", "色调", "构图", "场景")


def extract_video_dialogue(prompt, max_len=60):
    """把视频提示词拆成「画面描述」+「要念出来的台词」。

    返回 (画面描述, 台词 or None)。无台词时原样返回输入、台词为 None。

    识别规则（保守优先，宁可漏也别把画面描述当台词念出来）：
      ① 前缀标记行：`台词：` / `【对白】` / `line:` / `dialogue:` 等 —— 明确声明
      ② 整行被中文引号包住、且不含运镜类术语 —— 用户在引号里写的是口播内容

    台词被摘出后**不再留在画面描述里**：prompt 里的中文若未被本处摘走，
    内核也只会当画面语义处理；留在两处反而让模型既念又把它当画面文字。
    """
    if not prompt or not str(prompt).strip():
        return "", None
    scene_lines, dlg_lines = [], []
    for raw in str(prompt).splitlines():
        line = (raw or "").strip()
        if not line:
            continue
        m = _DLG_LINE_RE.match(line)
        if m:
            body = m.group(1).strip()
            if body:
                dlg_lines.append(body)
            continue
        m = _DLG_QUOTED_RE.match(line)
        if m and not any(w in line for w in _DLG_SCENE_WORDS):
            dlg_lines.append(m.group(1).strip())
            continue
        scene_lines.append(line)
    if not dlg_lines:
        return str(prompt).strip(), None
    cleaned = _clean_dialogue("，".join(dlg_lines), max_len=max_len)
    if not cleaned:
        # 洗完什么都没剩（如写的是英文）→ 退回「没有台词」，别传空串给内核
        return str(prompt).strip(), None
    return "\n".join(scene_lines).strip(), cleaned


def _build_video_prompt(prompt, dialogue=None):
    """把口播/台词包进视频 prompt（逻辑与 video-agent/core/agnes._inject_dialogue 对齐）。

    agnes-video-2.5-flash 会念出括号里的中文元指令（如“用中文说”之类），
    因此所有非台词文字改用英文，中文台词仅放在引号内，避免控制语泄漏进画面文字。

    ⚠️ v4.126.1：台词注入全链路只允许一处。video_pipeline 已不再调用本函数
    （它走 dialogue= 参数由内核注入），这里保留给数字分身口播等直连场景。
    """
    if not dialogue:
        return prompt
    d = _clean_dialogue(dialogue)
    if not d:
        return prompt
    return (
        f"{prompt.rstrip('. ')}\n\n"
        f'Spoken line in Mandarin: "{d}"\n'
        f"Speak ONLY the Chinese text inside the quotation marks, word for word. "
        f"Do not translate it, do not add any introduction, do not read any other text. "
        f"Natural lip-synced mouth movement, clear spoken Mandarin voice."
    )


def _agnes_creds(cfg):
    """从配置中取 Agnes 通道的 base_url 与 api_key（独立于当前聊天模型，始终走 Agnes 直连）。"""
    prof = (cfg.get("model_profiles") or {}).get("Agnes") or {}
    base = prof.get("base_url") or cfg.get("base_url") or "https://apihub.agnes-ai.cn/v1"
    key = prof.get("api_key") or cfg.get("api_key") or ""
    return base.rstrip("/"), key


def _zhipu_key(cfg):
    """取智谱 key（视频兜底通道：Agnes 挂掉时 text 模式自动降级 CogVideoX-Flash）。"""
    prof = (cfg.get("model_profiles") or {}).get("智谱 GLM") or {}
    return prof.get("api_key") or ""


_AGNES_TIERS = ((3400, "4K"), (2500, "3K"), (1700, "2K"))
_AGNES_RATIOS = {
    "1:1": 1.0, "4:3": 4 / 3, "3:4": 3 / 4, "16:9": 16 / 9,
    "9:16": 9 / 16, "3:2": 3 / 2, "2:3": 2 / 3,
}
# 用户主动要文字时不加防加字约束
_WANT_TEXT_RE = re.compile(
    r"文字|字体|标题|文案|标语|写着|写上|写有|字幕|水印|logo|LOGO|slogan|caption|text|title",
    re.I,
)
_ANTI_TEXT_SUFFIX = (
    "。（强制约束：除非用户明确要求，否则画面中绝对不要出现任何文字、字母、数字、"
    "标题、水印、标语或乱码，保持纯视觉画面）"
)


def _size_to_tier_ratio(w, h):
    """把精确像素尺寸映射为 Agnes 2.5 支持的 (档位, 比例)。

    2.5 系列只认 size 档位（1K/2K/3K/4K）+ ratio（16:9 等），
    传精确像素会掉画质甚至报错。档位按最长边取，比例取最接近的常用值。
    """
    try:
        w, h = int(w), int(h)
    except (TypeError, ValueError):
        return "2K", "16:9"
    if w <= 0 or h <= 0:
        return "2K", "16:9"
    longest = max(w, h)
    tier = "1K"
    for thr, name in _AGNES_TIERS:
        if longest >= thr:
            tier = name
            break
    target = w / float(h)
    ratio = min(_AGNES_RATIOS.items(), key=lambda kv: abs(kv[1] - target))[0]
    return tier, ratio


def _letterbox_to_size(fpath, w, h):
    """把生成图对齐到用户要求的精确像素尺寸。

    比例差 <0.01 直接等比缩放（画面锐、不补边）；比例差较大才按「填满后居中裁切」
    处理，避免出现黑边。原地覆盖，失败不抛。
    """
    from PIL import Image
    with Image.open(fpath) as im:
        im = im.convert("RGB")
        sw, sh = im.size
        if (sw, sh) == (w, h):
            return (sw, sh)
        if abs(sw / float(sh) - w / float(h)) < 0.01:
            out = im.resize((w, h), Image.LANCZOS)
        else:
            scale = max(w / float(sw), h / float(sh))
            tmp = im.resize((max(1, int(sw * scale)), max(1, int(sh * scale))), Image.LANCZOS)
            left = max(0, (tmp.size[0] - w) // 2)
            top = max(0, (tmp.size[1] - h) // 2)
            out = tmp.crop((left, top, left + w, top + h))
        out.save(fpath)
    return (w, h)


def _gen_agnes_image(cfg, app_dir, prompt, size=None, progress=None):
    """agnes 模式：直连 Agnes 图像生成接口，不经过本地网关。"""
    base, key = _agnes_creds(cfg)
    model = cfg.get("image_gen_model", "agnes-image-2.5-flash")
    if model == "agnes":  # 兼容旧值
        model = "agnes-image-2.5-flash"
    if size is None:
        size = cfg.get("image_gen_size", "1024x768")
    is_25 = "2.5" in model
    # 2.5 爱自作主张往画面里加字，用户没主动要字就强约束
    if is_25 and prompt and not _WANT_TEXT_RE.search(prompt):
        prompt = prompt.rstrip("。.") + _ANTI_TEXT_SUFFIX
    url = base + "/images/generations"
    body = {"model": model, "prompt": prompt, "size": size}
    # v4.134.10：agnes-image-2.5-flash 必须带 extra_body.response_format='url'，
    # 否则默认可能返回 base64 而非 URL；_save_gen_image 无法处理裸 base64 串会保存失败。
    # 技能文档（agnes-ai/SKILL.md:76,96）实测确认：response_format 必须放 extra_body。
    body["extra_body"] = {"response_format": "url"}
    want_wh = None
    if is_25 and isinstance(size, str) and "x" in size:
        try:
            _w, _h = (int(x) for x in size.lower().split("x", 1))
            tier, ratio = _size_to_tier_ratio(_w, _h)
            body["size"], body["ratio"] = tier, ratio
            want_wh = (_w, _h)
            if progress:
                progress(f"🖼 Agnes {model} 生成中…（{tier} / {ratio} → {_w}x{_h}）")
        except ValueError:
            pass
    if progress and want_wh is None:
        progress(f"🖼 Agnes {model} 生成中…（{size}）")
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    # v4.134.11：Agnes 免费档生图队列常满（503 "queue is full, please retry
    # later"）或 5xx 瞬态抖动。服务端明确叫重试，这里自动退避重试，单张图的
    # 瞬态失败不再连累整组三视图。401/400 等永久错误不重试、立即返回真实原因。
    _MAX_TRIES = 4
    _BACKOFF = (3, 8, 16)  # 第 1/2/3 次重试前的等待秒数（指数退避）
    _RETRYABLE = (500, 502, 503, 504, 429)
    data = None
    for _attempt in range(1, _MAX_TRIES + 1):
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            break  # 成功，跳出重试
        except urllib.error.HTTPError as e:
            _code = e.code
            if _code in _RETRYABLE and _attempt < _MAX_TRIES:
                _wait = _BACKOFF[_attempt - 1] if _attempt - 1 < len(_BACKOFF) else _BACKOFF[-1]
                if progress:
                    progress(f"⏳ Agnes 生图队列繁忙({_code})，{_wait}s 后自动第{_attempt + 1}次重试…")
                time.sleep(_wait)
                continue
            _msg = ""
            try:
                _err = json.loads(e.read().decode("utf-8", "ignore"))
                if isinstance(_err, dict):
                    _em = _err.get("error")
                    _msg = _em.get("message", "") if isinstance(_em, dict) else str(_em)
            except Exception:
                pass
            return (f"生图请求失败（Agnes 直连）：HTTP {_code} {e.reason}"
                    + (f" —— {_msg}" if _msg else ""))
        except Exception as e:
            if _attempt < _MAX_TRIES:
                _wait = _BACKOFF[_attempt - 1] if _attempt - 1 < len(_BACKOFF) else _BACKOFF[-1]
                if progress:
                    progress(f"⏳ Agnes 生图请求异常（{type(e).__name__}），{_wait}s 后自动第{_attempt + 1}次重试…")
                time.sleep(_wait)
                continue
            return f"生图请求失败（Agnes 直连）：{e}"
    if data is None:
        return "生图请求失败（Agnes 直连）：重试后仍无有效响应"
    # 兼容多种返回：data[].url / images[].url / data[].b64_json / content(裸URL)
    images = data.get("data") or data.get("images") or []
    img_url = ""
    img_b64 = ""
    if images:
        img_url = images[0].get("url", "")
        img_b64 = images[0].get("b64_json", "")
    if not img_url and not img_b64:
        content = (data.get("content") or "").strip()
        if content.startswith("http"):
            img_url = content
    if not img_url and not img_b64:
        return f"生图失败：{data}"
    if progress:
        progress("⬇ 下载并保存图片…")
    try:
        if img_b64:
            import base64 as _b64
            res = _save_gen_image(app_dir, _b64.b64decode(img_b64), is_bytes=True)
        else:
            res = _save_gen_image(app_dir, img_url)
    except Exception as e:
        return f"保存图片失败：{e}（原始返回：{data}）"
    # 档位出图与用户要求的精确像素可能不同，落盘后对齐一次
    if want_wh and isinstance(res, tuple) and res:
        try:
            _letterbox_to_size(os.path.join(app_dir, res[0]), want_wh[0], want_wh[1])
        except Exception as e:
            log.warning("生图尺寸对齐跳过: %s", e)
    return res


def _save_gen_video(app_dir, url):
    """将视频下载到产物目录「视频」子目录，返回 (rel, 'video', name)。

    v4.129：目录由 product_layout 计算（dated 时 产物/YYYY-MM-DD/<项目>/视频）。
    """
    v_dir = _products_dir(None, "video")
    os.makedirs(v_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    fpath = os.path.join(v_dir, f"video_{stamp}.mp4")
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=120) as r:
        with open(fpath, "wb") as f:
            f.write(r.read())
    rel = _safe_relpath(fpath, app_dir)
    return (rel, "video", os.path.basename(rel))


def _image_to_payload_value(value, app_dir):
    """把 image 参数归一化为 Agnes 接受的 URL 或 base64 data URI。

    支持：http(s) URL、base64 data URI、本地文件路径（自动读图转 base64）。
    这样无论 Agent 传的是用户附带的路径（incoming/xxx.png）、绝对路径还是
    已编码的 data URI，都能正确进入 Agnes 图生视频。
    """
    import base64
    if not value:
        return None
    if value.startswith("data:image"):
        return value
    if value.startswith("http://") or value.startswith("https://"):
        return value
    p = value
    if not os.path.isabs(p):
        # v4.164.0：先按 WORKSPACE_DIR 找（运行数据归口），未命中再回退 app_dir
        for _base in (WORKSPACE_DIR, app_dir):
            _cand = os.path.join(_base, p)
            if os.path.isfile(_cand):
                p = _cand
                break
    if os.path.isfile(p):
        try:
            ext = os.path.splitext(p)[1].lower().lstrip(".")
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp"}.get(ext, "image/png")
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return f"data:{mime};base64,{b64}"
        except Exception:
            return value
    return value


# 视频素材「天生无声」登记表：rel 路径 -> True（2026-09-06）
# 智谱兜底（CogVideoX-Flash）产出的片段无音轨，属设计而非故障。合成自检读这张表，
# 全片都是静音素材时跳过「疑似静音」告警，避免把正常空镜误报成「哑弹 BUG」。
VIDEO_SILENT_MARK = {}


def is_silent_clip(rel):
    """查该视频素材是否被标记为天生无声（智谱兜底等）。未知按有声处理（保守）。"""
    try:
        return bool(VIDEO_SILENT_MARK.get(str(rel)))
    except Exception:
        return False


def tool_video_gen(cfg, app_dir, prompt, duration=None, aspect=None, resolution=None,
                   image=None, first_frame=None, last_frame=None, dialogue=None,
                   progress=None, images=None, ref_images=None,
                   dest_path=None, on_submit=None, cancel_token=None):
    """生视频（统一内核：委托 video-agent/core 的 AgnesClient）。

    与网页版 / director_panel 共用同一套 core/，根除两份 Agnes 视频客户端。
    模型固定 agnes-video-2.5-flash（720P，seconds 4-12，三模式 text/keyframe/reference）。

    ⚠️ 画幅必选意识（2026-09-06 踩坑）：aspect 默认 portrait(9:16) 竖版。
    漫剧分镜、横版影视内容**必须显式传 aspect="landscape"**，否则横版构图
    会被塞进竖容器（横版内容竖着播）。竖版 text 模式由内核自动走 3:4
    中转兜底（Agnes 服务端 9:16 旋转 bug 规避），无需调用方关心。

    返回 (rel, 'video', name) 交付物元组，或错误字符串。
    duration: 秒，自动钳制到 [4,12]；aspect: 'landscape'/'portrait'；
    resolution: 仅作宽高提示（2.5-flash 实际分辨率由 size 决定，默认 720P）；
    image / images: 图生视频参考图（reference 模式，≤5 张）；
    ref_images: v4.127 多参考图入参（list，等价 images，最多 5 张，超出截断）；
    first_frame/last_frame: 首尾帧（keyframe 模式）；
    dialogue: 口播台词（中文），模型合成中文语音 + 对口型。

    v4.168.0（导演台审查 #1/#2/#5）：
      · dest_path  —— 由调用方指定落盘位置（导演台用它把片段归口到
        项目目录 clips/shot_001_<uuid>.mp4，不再散落到公共产物目录 + 秒级时间戳）。
      · on_submit  —— 提交成功立刻回调远端 task_id，供调用方落盘进 manifest，
        关软件/断网后能用 resume 接回来，不重复提交、不重复扣费。
      · cancel_token —— 轮询与下载都查取消；被取消时**原样抛错**，
        绝不伪装成"生成失败"（更不许触发免费兜底再生成一次）。
    """
    if not prompt:
        return "未提供视频描述"
    try:
        from core_agnes import AgnesClient, AgnesError, is_cancel_error
    except Exception as e:
        return f"视频内核导入失败：{e}"
    base, key = _agnes_creds(cfg)
    # 时长：2.5-flash 限 [4,12] 秒，取整数秒
    if duration and isinstance(duration, (int, float)):
        secs = int(round(float(duration)))
    else:
        secs = 8
    secs = max(4, min(12, secs))
    # 比例：landscape -> 16:9，否则默认竖版 9:16（抖音/视频号/小红书）
    aspect_ratio = "16:9" if aspect == "landscape" else "9:16"
    size = "720P"
    # 参考图归并：images(多) 优先，其次 ref_images(v4.127)，最后 image(单)
    # Agnes 2.5 硬上限 5 张（实测传 6 张报 400）—— 超出一律截断，不报错不中断
    ref_list = []
    if images:
        ref_list = list(images) if isinstance(images, (list, tuple)) else [images]
    elif ref_images:
        ref_list = list(ref_images) if isinstance(ref_images, (list, tuple)) else [ref_images]
    elif image:
        ref_list = [image]
    if len(ref_list) > MAX_REF_IMAGES:
        ref_list = ref_list[:MAX_REF_IMAGES]
    # 图片归一化（本地/相对路径 -> data URI，URL 透传），交给 core 前先转好
    first_frame = _image_to_payload_value(first_frame, app_dir) if first_frame else None
    last_frame = _image_to_payload_value(last_frame, app_dir) if last_frame else None
    ref_list = [_image_to_payload_value(v, app_dir) for v in ref_list] if ref_list else None
    # 进度回调适配：core 用 on_event(type, payload)，包装成 progress(str)
    def _on_event(ev, payload):
        if not progress:
            return
        if ev == "submitted":
            progress("🎬 视频已提交，生成中…")
        elif ev == "progress":
            progress(f"🎬 视频生成中…已等待约 {int(payload.get('elapsed', 0))}s")
        elif ev == "done":
            progress("✅ 视频已生成")
    # 保存到产物目录「视频」，路径与旧 _save_gen_video 保持一致（video_pipeline 靠 rel 拼回）
    # v4.129：目录改由 product_layout 计算（dated 时 产物/YYYY-MM-DD/<项目>/视频）
    # v4.168.0：调用方给了 dest_path 就听调用方的（导演台工程目录归口）
    if dest_path:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        except Exception:
            pass
    else:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        v_dir = _products_dir(cfg, "video")
        os.makedirs(v_dir, exist_ok=True)
        dest_path = os.path.join(v_dir, f"video_{stamp}.mp4")
    try:
        client = AgnesClient(api_key=key, base_url=base, video_model="agnes-video-2.5-flash",
                             zhipu_key=_zhipu_key(cfg))
        path = client.generate_video(
            prompt=prompt,
            seconds=secs,
            aspect_ratio=aspect_ratio,
            size=size,
            mode=None,            # 三模式由 core 自动推导（imgs->reference / 首尾帧->keyframe）
            first_frame=first_frame,
            last_frame=last_frame,
            images=ref_list,
            dialogue=dialogue,
            model="agnes-video-2.5-flash",
            dest_path=dest_path,
            on_event=_on_event,
            timeout=VIDEO_POLL_TIMEOUT,
            on_submit=on_submit,
            cancel_token=cancel_token,
        )
    except Exception as e:
        # v4.168.0：取消不是失败 —— 原样抛出，让 TaskGraph/管线标 cancelled，
        # 而不是把"用户点了停止"写成"视频生成失败"。
        if is_cancel_error(e):
            raise
        if isinstance(e, AgnesError):
            return f"视频生成失败（统一内核）：{e.msg}"
        return f"视频生成失败（统一内核）：{e}"
    if not path or not os.path.isfile(path):
        return "视频生成未返回本地文件"
    # P1-9（v4.186.0 审查）：只验存在不验大小 → CDN 静默截断/磁盘写满/
    # ffmpeg 残留产生的 0 字节或半截 mp4 会通过 isfile 校验，被打上 done
    # 并进 merge 当成品交付。正常 720P ≥4s 的 mp4 至少数百 KB，10KB 以下
    # 视为残片拒绝（阈值取保守下限，不影响任何合法产物）。
    try:
        _vsz = os.path.getsize(path)
    except OSError:
        _vsz = -1
    if 0 <= _vsz < 10 * 1024:
        return (f"视频产物异常：文件仅 {_vsz} 字节（疑似下载残片或空文件），"
                f"已拒绝交付")
    rel = _safe_relpath(path, app_dir)
    # 记录素材来源：智谱兜底 = 天生无声，供合成自检区分「设计无声」与「哑弹 BUG」
    try:
        if getattr(client, "last_source", "agnes") == "zhipu":
            VIDEO_SILENT_MARK[rel] = True
    except Exception:
        pass
    return (rel, "video", os.path.basename(rel))



def tool_schedule(args):
    """定时提醒：计算延时，返回 (结果文本, (message, delay_seconds))。

    QTimer 设置由调用方（AgentWorker -> ChatWindow）处理。
    """
    message = args.get("message") or args.get("note") or ""
    delay = args.get("delay_seconds")
    at_time = args.get("at_time") or args.get("datetime")
    if not message:
        return "未提供提醒内容", None
    now = datetime.now()
    if delay is not None:
        try:
            secs = float(delay)
        except Exception:
            return "delay_seconds 必须是数字（秒）", None
    elif at_time:
        try:
            t = datetime.strptime(at_time, "%Y-%m-%d %H:%M")
        except Exception:
            try:
                t = datetime.strptime(at_time, "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day)
            except Exception:
                return "at_time 格式应为 'YYYY-MM-DD HH:MM' 或 'HH:MM'", None
        if t <= now:
            from datetime import timedelta
            t = t + timedelta(days=1)
        secs = (t - now).total_seconds()
    else:
        return "请提供 delay_seconds（秒）或 at_time（时间）", None
    secs = max(1, int(secs))
    result_str = f"已设置定时提醒，约 {secs} 秒后弹窗提醒：{message}"
    repeat = args.get("repeat_seconds")
    try:
        repeat = int(repeat) if repeat else 0
    except Exception:
        repeat = 0
    return result_str, (message, secs, repeat)


def _log_skill_hit(name, ok=True):
    """v4.110 技能使用旁路埋点（source=auto，即模型自己调 use_skill 命中）。

    route_log 缺失或写盘失败一律静默跳过——旁路不许拖垮技能加载这条主链路。
    """
    if route_log is None:
        return
    try:
        route_log.log_skill(name, "auto", ok=ok)
    except Exception:
        pass


def tool_use_skill(cfg, app_dir, skill_name):
    """加载指定技能的 prompt，返回可注入对话上下文的技能指令文本。

    跨多目录查找：内置/打包 skills、用户数据目录（Documents 下用户目录）/skills、config.skills_dir。
    同时支持 .py 与 技能名/SKILL.md 两种形态。
    """
    if not skill_name or not skill_name.strip():
        return "未提供 skill_name"
    # 必须先加载技能模块（在归一化调用之前），否则局部导入会把
    # normalize_skill_name 标记为函数内局部变量，导致 line 963 处 UnboundLocalError
    try:
        from skill_loader import load_skill_prompt, get_available_skills, normalize_skill_name
    except Exception as e:
        return f"加载技能模块失败：{e}"
    # 归一化：剥 emoji/符号、空白转连字符、大小写不敏感
    # （模型常照抄「{emoji} {name}」格式，如「📊 ppt-generator」）
    skill_name = normalize_skill_name(skill_name)
    if not skill_name:
        return "未提供有效的 skill_name"

    # v4.169.0（审查 P1-2）：加载前校验**启用状态**。
    # 原实现只把启用的技能显示在系统提示清单里，但加载时会重新扫描全部目录，
    # 不查 enabled_skills —— 模型只要记住（或猜出）某个被禁用技能的名字，
    # 照样能把它加载进来（清单里看不到 ≠ 加载不了）。
    try:
        from config import is_skill_enabled
        if not is_skill_enabled(skill_name):
            _log_skill_hit(skill_name, ok=False)
            return (f"技能「{skill_name}」当前处于**禁用**状态，无法加载。"
                    f"如需使用，请先在技能管理里启用它。")
    except Exception:
        pass   # 配置读取异常时不拦（不因配置问题把功能整个关掉）

    # 解析要扫描的技能目录（与 config.get_skill_scan_dirs 保持一致）
    try:
        from config import get_skill_scan_dirs
        dirs = get_skill_scan_dirs()
    except Exception:
        # 兜底：单目录
        if getattr(sys, 'frozen', False):
            dirs = [os.path.join(app_dir, "_internal", "skills")]
        else:
            dirs = [os.path.join(app_dir, "skills")]

    # v4.226：技能元数据强制化 —— strict 口径来自 config（默认 False=降级放行，
    # 否则仓里 v4.226 之前写的 ~30 个老技能会因缺 source/description 当场全失效）。
    _strict_meta = False
    try:
        import skill_meta
        _strict_meta = skill_meta.strict_from_config(cfg)
    except Exception:
        pass

    # 跨多目录查找技能（.py 与 SKILL.md 两种形态）
    prompt = None
    skill_dir = None
    reject_reason = ""
    degraded_skill = None
    for d in dirs:
        try:
            import skill_meta as _sm
            _sk = None
            try:
                from skill_loader import find_skill as _fs
                _sk = _fs(skill_name, d)
            except Exception:
                _sk = None
            if _sk is not None:
                _v = _sm.check_skill(_sk, strict=_strict_meta)
                if _v.verdict == _sm.VERDICT_REJECT:
                    reject_reason = _sm.rejection_text(_v)
                    _log_skill_hit(skill_name, ok=False)
                    break
                p = load_skill_prompt(skill_name, d, strict_meta=False)
                if p:
                    degraded_skill = _sk if _v.degraded else None
            else:
                p = load_skill_prompt(skill_name, d, strict_meta=False)
            if p:
                prompt = p
                _sub = os.path.join(d, skill_name)
                skill_dir = _sub if os.path.isdir(_sub) else d
                break
        except Exception:
            # 单目录失败不能中断跨目录查找
            continue

    # v4.226：元数据不合格 → 明确拒用（理由要可执行，不能让模型当技能不存在）
    if reject_reason:
        return reject_reason

    if prompt is None:
        # 汇总所有目录的可用技能名
        names = []
        for d in dirs:
            try:
                names.extend(s.get("name", "") for s in get_available_skills(d))
            except Exception:
                pass
        names = "、".join(dict.fromkeys([n for n in names if n])) or "（无）"
        _log_skill_hit(skill_name, ok=False)   # 模型想用但没找到 → 幻觉名或清单对不上
        return f"未找到技能「{skill_name}」。可用技能：{names}"

    if not prompt.strip():
        _log_skill_hit(skill_name, ok=False)
        return f"技能「{skill_name}」已加载，但未定义内容。"

    _log_skill_hit(skill_name, ok=True)
    # v4.226：降级放行时追加「未审材料」提示，让模型知道它拿的不是权威指令。
    # 这一句只挂在**未审**技能上；元数据齐全的技能返回文本与v4.225 完全一致
    #（零行为变化）。
    _note = ""
    try:
        import skill_meta as _sm2
        if degraded_skill is not None:
            _note = _sm2.unverified_note(_sm2.check_skill(degraded_skill,
                                                          strict=False))
    except Exception:
        _note = ""
    return (
        f"【已加载技能：{skill_name}】\n"
        f"技能目录：{skill_dir}\n（如技能引用 references/ 下的文件，可用 read_file 读取该目录下的文件）\n"
        f"请严格按以下专家指令完成本次任务：\n\n{prompt}\n\n"
        f"【执行要求】技能只服务**用户当前明确的目标**：\n"
        f"· 用户要产出时 → 动手做（调工具），不要只列计划/大纲；\n"
        f"· 用户只是在问、在讨论、在评估时 → **直接回答**，不要为了「用上技能」而写文件或执行动作。\n"
        f"（要不要调工具由用户的请求决定，不是由「加载了技能」决定。）{_note}"
    )


def tool_analyze_image(cfg, args):
    """调用本地免费网关的 vision 接口识图。

    网关会路由到 Agnes 2.0 Flash 的 image_url 能力。
    """
    import base64
    import urllib.request as ureq

    image_path = args.get("path", "")
    prompt = args.get("prompt", "请描述这张图片")
    if not image_path:
        return "错误：未提供图片路径（path）"

    # 相对路径 → 绝对路径解析
    if not os.path.isabs(image_path):
        resolved = os.path.abspath(image_path)
        if os.path.isfile(resolved):
            image_path = resolved
        else:
            # 兜底：在常用目录中搜索同名文件
            basename = os.path.basename(image_path)
            search_dirs = [
                os.path.expanduser("~/Downloads"),
                os.path.expanduser("~/Desktop"),
                os.path.expanduser("~/Pictures"),
                os.path.expanduser("~/Documents"),
                os.getcwd(),
            ]
            candidates = []
            for sd in search_dirs:
                if os.path.isdir(sd):
                    candidates.extend(glob.glob(os.path.join(sd, basename)))
                    # 也搜一层子目录
                    candidates.extend(glob.glob(os.path.join(sd, "*", basename)))
            if candidates:
                image_path = candidates[0]
            else:
                return f"错误：图片不存在 — {image_path}（已尝试 cwd、Downloads、Desktop、Pictures、Documents）"

    if not os.path.isfile(image_path):
        return f"错误：图片不存在 — {image_path}"

    try:
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return f"错误：读取图片失败 — {e}"

    gw = cfg.get("gateway_url", "http://127.0.0.1:8000")
    payload = json.dumps({"prompt": prompt, "image_data": image_data}).encode("utf-8")
    req = ureq.Request(
        f"{gw.rstrip('/')}/v1/vision",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with ureq.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        content = result.get("content", "")
        if not content:
            return f"识图完成，但模型未返回内容。（model={result.get('model', '?')}, ok={result.get('ok')})"
        return content
    except Exception as e:
        msg = str(e)
        if "10061" in msg or "连接被拒绝" in msg or "refused" in msg.lower():
            return (f"识图请求失败：{e}。提示：识图后端(free-api-gateway, 8000端口)未启动——"
                    f"请确认其已随本程序自动拉起（设置 gateway_autostart），或手动运行网关目录下的 run_gateway.bat。")
        return f"识图请求失败：{e}"


def card_html(name, args_str, result=None):
    """生成工具调用卡片 HTML。"""
    a = html_mod.escape(str(args_str))[:600]
    out = (f'<div style="font-size:12px;color:#065f46;margin:3px 0;">'
           f'<b>[工具] {html_mod.escape(name)}</b> '
           f'<span style="color:#475569;font-family:monospace;">{a}</span></div>')
    if result is not None:
        r = html_mod.escape(str(result))[:TOOL_RESULT_LIMIT].replace("\n", "<br>")
        out += f'<div style="font-size:12px;color:#334155;margin:2px 0 4px 8px;">→ {r}</div>'
    return out


def tool_remember(cfg, app_dir, args, progress=None):
    """将一条用户长期信息写入跨对话记忆库（memory_store）。"""
    fact = (args or {}).get("fact", "") if isinstance(args, dict) else str(args or "")
    return append_memory(fact)


def tool_sys_info(cfg, app_dir, _progress=None):
    """v4.60p：自省——返回【权威能力清单】。

    直接枚举真实注册的工具（config.TOOL_DEFS），100% 可信；并显式标注未配置/未开启项，
    防止模型把零散提示脑补成不存在的能力（如把已清空的 Obsidian 说成『语义检索』）。
    做自检/能力盘点时，模型只准逐条复述本清单，禁止从自身知识添加新功能。
    """
    import os, sqlite3, datetime
    from config import TOOL_DEFS
    # v4.108.1：自称从脱敏残留 AgentDesktop 改回本机品牌（开源脱敏时自动再变 AgentDesktop）
    _APP_LABEL = "小臭玩AI"
    lines = [f"## {_APP_LABEL} 系统实时状态（权威清单 · 只准复述此清单）",
             f"_查询时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_", ""]

    # —— 一、真实工具清单（动态枚举 TOOL_DEFS，实时可信，禁止编造）——
    lines.append(f"### 一、可直接调用的工具（共 {len(TOOL_DEFS)} 个，实时枚举自注册表）")
    for d in TOOL_DEFS:
        fn = d.get("function", {}) if isinstance(d, dict) else {}
        nm = fn.get("name", "")
        if not nm:
            continue
        ds = (fn.get("description", "") or "").strip().split("。")[0].split("\n")[0].strip()
        lines.append(f"- {nm}：{ds[:55]}")
    lines.append("")

    # —— 二、未配置 / 未开启项（显式声明，绝不可声称可用）——
    lines.append("### 二、未配置 / 未开启项（严禁声称可用）")
    ov = cfg.get("obsidian_vault_path", "")
    if ov:
        lines.append(f"- Obsidian Vault：已配置（{ov}，.md 已索引进 RAG；检索走 rag_index/rag_search 本地向量库）")
    else:
        lines.append(f"- Obsidian Vault：未配置（RAG 只用本地 rag_data 目录，未接 Obsidian）")
    lines.append(f"- Webhook：{'未开启' if not cfg.get('webhook_enabled') else '已开启'}"
                 f"（webhook_start/stop/events 工具存在，但需先开启 webhook_enabled）")
    sf = cfg.get("siliconflow", {}) or {}
    lines.append(f"- 语音识别 ASR：SenseVoiceSmall（硅基流动）→ "
                 f"{'已配置 key，可用' if sf.get('api_key') else '未配置 key，暂不可用'}")
    lines.append(f"- 语音合成 TTS：edge-tts（免 key，可用，属应用内功能非 agent 工具）")
    lines.append("")

    # —— 三、技能（动态统计真实技能数，杜绝硬编码魔数）——
    try:
        from config import get_skill_scan_dirs
        from skill_loader import get_available_skills
        seen = set()
        for d in get_skill_scan_dirs():
            try:
                for sk in get_available_skills(d):
                    n = sk.get("name", "")
                    if n and n not in seen:
                        seen.add(n)
            except Exception:
                pass
        lines.append(f"### 三、技能（{len(seen)} 个，见系统提示【可用技能】清单）")
    except Exception:
        lines.append("### 三、技能（见系统提示【可用技能】清单）")
    lines.append("")

    # —— 四、本地数据库（SQLite）——
    # v4.108.1：数据目录曾写死 ~/Documents/AgentDesktop（v4.100 脱敏残留），
    # 导致 sys_info 误报「三个库未创建、数据目录 AgentDesktop」——真实数据全在
    # USER_DATA_DIR（Documents/小臭玩AI）。改从 config 常量取，开源脱敏自动跟随。
    data_dir = USER_DATA_DIR
    lines.append("### 四、本地数据库（SQLite）")
    for db, label in [("xiaochou.db", "应用库"), ("agent_log.db", "日志库"), ("memory.db", "记忆搜索库")]:
        dp = os.path.join(data_dir, db)
        if os.path.exists(dp):
            conn = sqlite3.connect(dp)
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                  "AND name NOT LIKE 'sqlite_%'").fetchall()
            lines.append(f"- {label}（{db}）：{', '.join(t[0] for t in tables)}")
            conn.close()
        else:
            lines.append(f"- {label}：未创建")
    lines.append("")

    # —— 五、运行环境 ——
    lines.append("### 五、运行环境")
    lines.append(f"- 数据目录：{data_dir}")
    rd = cfg.get("rag_data_dir", "") or os.path.join(WORKSPACE_DIR, "rag_data")
    lines.append(f"- RAG 目录：{rd}")
    lines.append(f"- 记忆库：{data_dir}/memory.md + memory.db")
    lines.append(f"- 模型：{cfg.get('model', '')}")
    lines.append(f"- Agent 模式：{'开' if cfg.get('agent_mode') else '关'}")
    lines.append(f"- 互联网搜索：{'开' if cfg.get('search_enabled', True) else '关'}")
    lines.append(f"- MCP 服务器：filesystem（1 个，已连接）")
    lines.append("")
    lines.append("**重要**：以上为系统真实能力。做能力盘点/自检时只准逐条复述本清单，"
                 "禁止从你自己的知识添加任何新功能；本清单标注『未配置/未开启』的项绝不可声称可用。")
    return "\n".join(lines)


def tool_search_memory(cfg, app_dir, args, progress=None):
    """v4.59：全文搜索长期记忆库，返回匹配条目。"""
    from memory_store import search_memory
    query = (args or {}).get("query", "") if isinstance(args, dict) else str(args or "")
    if not query:
        return "搜索关键词为空，请提供查询词。"
    results = search_memory(query, limit=5)
    if not results:
        return f"记忆库中未找到与「{query}」相关的内容。"
    lines = [f"搜索「{query}」找到 {len(results)} 条记忆："]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. [{r['time']}] {r['snippet']}")
    return "\n".join(lines)


def tool_send_email(cfg, to, subject, body, progress=None):
    """SMTP 发送邮件。需在 config.json 配置 smtp_host/smtp_user/smtp_pass。
    示例：QQ邮箱 smtp_host=smtp.qq.com smtp_port=587 需用授权码。
    """
    smtp_host = cfg.get("smtp_host") or ""
    smtp_user = cfg.get("smtp_user") or ""
    smtp_pass = cfg.get("smtp_pass") or ""
    if not smtp_host or not smtp_user or not smtp_pass:
        return "邮件未配置：请在 config.json 设置 smtp_host/smtp_user/smtp_pass（QQ邮箱需用授权码）。"
    import smtplib
    from email.mime.text import MIMEText
    is_html = "<" in body and ">" in body if isinstance(body, str) else False
    msg = MIMEText(body, "html" if is_html else "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to
    try:
        port = cfg.get("smtp_port", 587)
        # 审计修复 F9：原实现 quit() 只在成功路径执行，login/starttls/send_message
        # 任一失败即抛，SMTP 连接（socket）泄漏。改用 with 上下文，连接必关。
        if port == 465:
            with smtplib.SMTP_SSL(smtp_host, port, timeout=15) as s:
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, port, timeout=15) as s:
                s.starttls()
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)
        return f"邮件已发送到 {to}"
    except Exception as e:
        return f"邮件发送失败：{e}"


# ============ v4.60 自动技能创建 ============

@register_tool("create_skill")
def _h_create_skill(cfg, app_dir, args, progress=None):
    """v4.60：将成功的多步工作流保存为可复用技能 SKILL.md。"""
    return (tool_create_skill(cfg, app_dir, args), [], None)


def tool_create_skill(cfg, app_dir, args, _progress=None):
    """v4.84(热修15·B)：将 agent 刚完成的复杂任务提炼为技能，**提交到审核队列**而非直接生效。

    模型可自主创建/改装自身技能（软自进化），但必须经用户「技能审核」通过才正式加载，
    避免不可控技能静默生效。落盘目录为 skills_pending/（不被 skill_loader 自动加载）。
    """
    name = (args or {}).get("name", "").strip() if isinstance(args, dict) else ""
    description = (args or {}).get("description", "").strip() if isinstance(args, dict) else ""
    prompt = (args or {}).get("prompt", "").strip() if isinstance(args, dict) else ""
    emoji = (args or {}).get("emoji", "⚡").strip() if isinstance(args, dict) else "⚡"
    category = (args or {}).get("category", "自动生成").strip() if isinstance(args, dict) else "自动生成"

    try:
        import skill_review
        return skill_review.submit_skill(cfg, name, description, prompt, emoji, category)
    except Exception as e:
        return f"技能提交失败：{e}"


# v4.106：对话框导演工具（director_status / revise_clip / revise_keyframe /
# revise_character / merge）。放文件末尾 import，避免循环导入；
# Qt-free，必须在启动时注册进 TOOL_REGISTRY（否则 get_all_tools 一致性校验告警）。
try:
    import director_agent_tools  # noqa: F401  (注册副作用)
except Exception as _e:
    log.warning("导演对话框工具注册失败: %s", _e)
