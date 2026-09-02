# -*- coding: utf-8 -*-
"""DeepSeek 桌面助手 — 工具执行模块"""

import sys
import os
import html as html_mod
import re
import json
import time
import subprocess
import shutil
import logging
import inspect
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

import glob
from config import APP_DIR, TOOL_READ_LIMIT, TOOL_RESULT_LIMIT, PRODUCTS_DIR, USER_DATA_DIR
import search as search_mod
from system_control_tools import SYSTEM_CONTROL_TOOL_TABLE
from software_control_tools import SOFTWARE_CONTROL_TOOL_TABLE
from browser_control_tools import BROWSER_CONTROL_TOOL_TABLE
from skill_installer_tools import SKILL_INSTALLER_TOOL_TABLE
from memory_store import append_memory, search_memory as _search_memory
from structured_logger import get_logger
from chart_generator import ChartGenerator
chart_gen = ChartGenerator()
from context_manager import get_context_manager
from database_tools import DatabaseTools
db_tools = DatabaseTools()

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
    """把 4 个扩展模块的 *_TOOL_TABLE 注册进 registry（它们已是字典表，handler 签名统一）。"""
    try:
        from system_control_tools import SYSTEM_CONTROL_TOOL_TABLE
        from software_control_tools import SOFTWARE_CONTROL_TOOL_TABLE
        from browser_control_tools import BROWSER_CONTROL_TOOL_TABLE
        from skill_installer_tools import SKILL_INSTALLER_TOOL_TABLE
        for _table in (SYSTEM_CONTROL_TOOL_TABLE, SOFTWARE_CONTROL_TOOL_TABLE,
                       BROWSER_CONTROL_TOOL_TABLE, SKILL_INSTALLER_TOOL_TABLE):
            for _name, _handler in _table.items():
                _danger = _name.startswith("browser_") or _name == "skill_install"
                def _wrap(h=_handler):
                    def _w(cfg, app_dir, args, progress=None):
                        return h(cfg, app_dir, args)
                    return _w
                TOOL_REGISTRY[_name] = {"handler": _wrap(), "dangerous": _danger}
    except Exception as _e:
        log.warning("扩展工具注册失败: %s", _e)

_register_extension_tools()


# ============ v4.50 权限：风险分类（已迁移到 risk.py，借鉴 andrewyng/openworker）============
# 等级 / 风险档定义见 risk.py（RiskClass + RISK_MAP + classify + tier_of + grouped_tools）。
# 这里仅做向后兼容再导出，避免散落各处的 tools.tier_of / tools.TOOL_TIER 引用失效。
from risk import RiskClass, RISK_MAP, classify, tier_of, grouped_tools
# 兼容别名：旧代码可能直接查 TOOL_TIER（name -> 等级）
TOOL_TIER = {n: tier_of(n) for n in RISK_MAP}


# === 核心 25 工具注册到 registry（v4.31）===
# handler 签名统一 (cfg, app_dir, args) -> (result_str, deliverables, schedule)
@register_tool("web_search")
def _h_web_search(cfg, app_dir, args, progress=None):
    return (tool_web_search(cfg, args.get("query", "")), [], None)

@register_tool("web_fetch")
def _h_web_fetch(cfg, app_dir, args, progress=None):
    return (tool_web_fetch(args.get("url", "")), [], None)

@register_tool("read_file")
def _h_read_file(cfg, app_dir, args, progress=None):
    return (tool_read_file(app_dir, args.get("path", ""),
                           offset=args.get("offset", 0), limit=args.get("limit")), [], None)

@register_tool("write_file", dangerous=True)
def _h_write_file(cfg, app_dir, args, progress=None):
    p = args.get("path", "")
    r = tool_write_file(app_dir, p, args.get("content", ""))
    return (r, [(p, "file", os.path.basename(p))] if p else [], None)

@register_tool("run_command", dangerous=True)
def _h_run_command(cfg, app_dir, args, progress=None):
    return (tool_run_command(app_dir, args.get("command", "")), [], None)

@register_tool("run_python", dangerous=True)
def _h_run_python(cfg, app_dir, args, progress=None):
    r, d = tool_run_python(app_dir, args.get("code", ""))
    return (r, d, None)

@register_tool("image_gen")
def _h_image_gen(cfg, app_dir, args, progress=None):
    # v4.108 M-16：模型传的 size（"WxH"）必须透传给后端——此前被 handler 丢弃，
    # 模型以为指定了尺寸实际永远走默认。
    res = tool_image_gen(cfg, app_dir, args.get("prompt", ""),
                         size=args.get("size"), progress=progress)
    if isinstance(res, tuple):
        return (res[0], [res], None)
    return (res, [], None)

@register_tool("video_gen")
def _h_video_gen(cfg, app_dir, args, progress=None):
    res = tool_video_gen(cfg, app_dir, args.get("prompt", ""),
                         duration=args.get("duration"), aspect=args.get("aspect"),
                         image=args.get("image"),
                         first_frame=args.get("first_frame"),
                         last_frame=args.get("last_frame"),
                         dialogue=args.get("dialogue"),
                         progress=progress)
    if isinstance(res, tuple):
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
    res = chart_gen.generate(chart_type=args.get("chart_type"), data=args.get("data", {}),
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
        except Exception:
            pass
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


def exec_tool(cfg, app_dir, name, args, progress=None):
    """统一工具路由，返回 (result_str, deliverables, schedule)。

    result_str: 工具执行结果文本
    deliverables: [(rel_path, kind, name), ...] 新生成的交付物
    schedule: (message, delay_seconds) 定时提醒，无则为 None
    """
    deliverables = []
    schedule = None
    # v4.31 统一注册中心：优先查 registry（扩展模块已注册；核心工具逐步迁移中）
    _entry = TOOL_REGISTRY.get(name)
    if _entry:
        try:
            get_logger().info(f"执行工具: {name}", module="tools", extra={"tool": name})
            _r = _entry["handler"](cfg, app_dir, args, progress)
            if isinstance(_r, tuple) and len(_r) == 3:
                return _r
            return (str(_r), [], None)
        except Exception as e:
            log.warning("工具 %s 执行异常: %s", name, e)
            return (f"工具执行异常：{e}", [], None)

    try:
        get_logger().info(f"执行工具: {name}", module="tools", extra={"tool": name})
        result_str = _try_mcp_tool(name, args)
    except Exception as e:
        result_str = f"工具执行异常：{e}"

    return (result_str, deliverables, schedule)


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


def tool_web_search(cfg, query):
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
                # 境外兜底（你网络环境多半连不通，仅作最后尝试；连不通秒退不浪费）
                results = search_mod.search_ddg(subq, top_k)
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
            return "\n".join(lines)
    if serper_key:
        _r = search_mod.search_serper(query, serper_key, top_k)
        if _r:
            lines = [f"搜索「{query}」结果（来源：Serper / Google）："]
            for i, r in enumerate(_r[:top_k], 1):
                lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
            return "\n".join(lines)
    # 无付费 key：先走国内免费引擎链（Bing/百度/搜狗，你网络可通），再 DuckDuckGo 境外兜底
    chain = search_mod.provider_chain(cfg.get("search_provider", "auto"))
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
            lines = [f"搜索「{query}」结果（来源：{provider}）："]
            for i, r in enumerate(results[:top_k], 1):
                lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
            return "\n".join(lines)
    # 国内链全空 → 境外 DuckDuckGo 最后兜底（你网络多半连不通，秒退不浪费）
    _r = search_mod.search_ddg(query, top_k)
    if _r:
        lines = [f"搜索「{query}」结果（来源：DuckDuckGo 境外兜底）："]
        for i, r in enumerate(_r[:top_k], 1):
            lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
        return "\n".join(lines)
    return f"未找到搜索结果（最后错误：{last_err}）" if last_err else "未找到搜索结果"


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


def tool_web_fetch(url):
    if not url:
        return "未提供 URL"
    if _is_sensitive_file(url):
        return "已阻止：目标文件是敏感配置（可能含 API key），禁止读取。"
    try:
        raw = search_mod.http_get(url, timeout=20)
    except Exception as e:
        return f"抓取失败：{e}"
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


def tool_read_file(app_dir, path, offset=0, limit=None):
    if not path:
        return "未提供路径"
    if _is_sensitive_file(path):
        return "已阻止：目标文件是敏感配置（可能含 API key），禁止读取。"
    p = os.path.abspath(os.path.join(app_dir, path))
    root = os.path.abspath(app_dir)
    # v4.66：模型若只给文件名（不含斜杠），自动去 incoming/ 子目录找（附件都落那）
    if not os.path.isfile(p) and "/" not in path and "\\" not in path:
        cand = os.path.abspath(os.path.join(app_dir, "incoming", os.path.basename(path)))
        if os.path.isfile(cand):
            p = cand
    if p != root and not p.startswith(root + os.sep):
        return f"拒绝：只能读取工作区目录内文件（{root}）"
    if not os.path.isfile(p):
        return f"文件不存在：{p}"
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
            return f"读取失败：{e}"

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
        return f"offset={offset} 已超出文件长度（文件共 {total} 字符）。"
    seg = text[offset:offset + limit]
    if offset + limit < total:
        seg += f"\n\n[文件共 {total} 字符，已读 {offset}~{offset + limit} 段；如需后续内容，用 offset={offset + limit} 再读]"
    elif offset > 0:
        seg += f"\n\n[已读到文件末尾（共 {total} 字符）]"
    return seg


def tool_write_file(app_dir, path, content):
    if not path:
        return "未提供路径"
    p = os.path.abspath(os.path.join(app_dir, path))
    root = os.path.abspath(app_dir)
    if p != root and not p.startswith(root + os.sep):
        return f"拒绝：只能写入工作区目录内（{root}）"
    try:
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已写入 {len(content)} 字符到 {p}"
    except Exception as e:
        return f"写入失败：{e}"


def tool_run_command(app_dir, command):
    if not command or not command.strip():
        return "未提供命令"
    try:
        import platform
        # v4.60：Windows 上强制走 PowerShell，避免 cmd.exe 不认识 Get-ChildItem 等命令
        if platform.system() == "Windows":
            proc = subprocess.run(
                ["powershell", "-Command", command],
                cwd=app_dir, capture_output=True, timeout=60,
            )
        else:
            proc = subprocess.run(
                command, shell=True, cwd=app_dir,
                capture_output=True, timeout=60,
            )
    except subprocess.TimeoutExpired:
        return "命令执行超时（>60s），已终止"
    except Exception as e:
        return f"命令执行失败：{e}"
    raw = (proc.stdout or b"") + (proc.stderr or b"")
    try:
        out = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            out = raw.decode("gbk")
        except Exception:
            out = raw.decode("utf-8", "ignore")
    if not out.strip():
        out = f"（命令已执行，退出码 {proc.returncode}，无输出）"
    return out[:TOOL_RESULT_LIMIT]




def snapshot_workspace(app_dir=None):
    """Capture all files in workspace as relative paths for before/after diff."""
    if app_dir is None:
        app_dir = APP_DIR
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


def _resolve_python_exe():
    """选一个带完整第三方库（pptx/pandas/...）的 Python 解释器。
    优先 PATH 里的 python（run_command 已验证其可 import pptx），
    其次系统 Python312，最后退回当前解释器。
    """
    candidates = [
        "python",
        r"<PYTHON_EXE>",
        sys.executable,
    ]
    for c in candidates:
        if not c:
            continue
        try:
            r = subprocess.run([c, "-c", "import sys; sys.stdout.write(sys.executable)"],
                               capture_output=True, timeout=10)
            if r.returncode == 0 and r.stdout:
                return c
        except Exception:
            continue
    return None


def tool_run_python(app_dir, code):
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

    exe = _resolve_python_exe()
    if not exe:
        return "未找到可用的 Python 解释器（请确认已安装 Python 并加入 PATH）", []

    # 独立工作区：产物默认落这里，不污染 app 目录
    # v4.108.1：v4.100 脱敏残留把 USER_DATA_DIR 写成了 AgentDesktop/workspace，
    # run_python 产物落进了空壳目录——改回真实用户数据目录（小臭玩AI）。
    ws = os.path.join(USER_DATA_DIR, "workspace")
    os.makedirs(ws, exist_ok=True)
    fname = f"run_{datetime.now().strftime('%Y%m%d%H%M%S')}.py"
    fpath = os.path.join(ws, fname)
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(code)
    except Exception as e:
        return f"写临时文件失败：{e}", []

    before = snapshot_workspace(ws)
    try:
        proc = subprocess.run([exe, fpath], cwd=ws,
                               capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "代码执行超时（>120s），已终止。可把任务拆小或分步执行。", []
    except Exception as e:
        return f"执行失败：{e}", []
    finally:
        try:
            os.remove(fpath)
        except Exception:
            pass

    raw = (proc.stdout or b"") + (proc.stderr or b"")
    try:
        out = raw.decode("utf-8")
    except UnicodeDecodeError:
        out = raw.decode("gbk", "ignore")
    if not out.strip():
        out = f"（已执行，退出码 {proc.returncode}，无输出）"

    after = snapshot_workspace(ws)
    deliverables = []
    for nf in sorted(after - before):
        full = os.path.join(ws, nf)
        deliverables.append((full, classify_kind(nf), os.path.basename(nf)))

    return out[:TOOL_RESULT_LIMIT], deliverables


# 保留旧函数供内部兼容
# 保留旧函数供内部兼容
def _tool_run_python_legacy(app_dir, code):
    """旧的子进程执行方式（保留兼容）"""
    if not code or not code.strip():
        return "未提供代码", []
    exe = _resolve_python_exe()
    if not exe:
        return "未找到 Python 解释器，请先安装 Python 并加入 PATH", []
    gen_dir = os.path.join(app_dir, "gen")
    os.makedirs(gen_dir, exist_ok=True)
    fname = f"run_{datetime.now().strftime('%Y%m%d%H%M%S')}.py"
    fpath = os.path.join(gen_dir, fname)
    frel = _safe_relpath(fpath, app_dir)
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(code)
    except Exception as e:
        return f"写临时文件失败：{e}", []
    before = snapshot_workspace(app_dir)
    try:
        proc = subprocess.run([exe, fpath], cwd=app_dir,
                               capture_output=True, timeout=60)
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
    after = snapshot_workspace(app_dir)
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
        results.append(cfg_mod.rag_store.index_file(str(p)))
    else:
        for f in p.rglob("*"):
            if f.is_file() and f.suffix.lower() in ('.txt', '.md', '.py', '.pdf', '.docx'):
                results.append(cfg_mod.rag_store.index_file(str(f)))
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

    outputs = Path(PRODUCTS_DIR) / "截图"
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


def _save_gen_image(app_dir, data_or_path, is_bytes=False):
    """将生图结果存入产物目录「图片」子目录，返回 (rel, 'image', name)。"""
    img_dir = os.path.join(PRODUCTS_DIR, "图片")
    os.makedirs(img_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
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


def _build_video_prompt(prompt, dialogue=None):
    """把口播/台词包进视频 prompt（逻辑与 video-agent/core/agnes._inject_dialogue 对齐）。

    agnes-video-2.5-flash 会念出括号里的中文元指令（如“用中文说”之类），
    因此所有非台词文字改用英文，中文台词仅放在引号内，避免控制语泄漏进画面文字。
    video_pipeline.py 在逐镜生成时调用本函数注入本镜台词。
    """
    if not dialogue:
        return prompt
    d = dialogue.strip()
    if not d:
        return prompt
    return (
        f"{prompt.rstrip('. ')}\n\n"
        f'Spoken line in Mandarin: "{d}"\n'
        f"Only the quoted line above should be spoken. No English, no introduction. "
        f"Natural lip-synced mouth movement, clear spoken Mandarin voice."
    )


def _agnes_creds(cfg):
    """从配置中取 Agnes 通道的 base_url 与 api_key（独立于当前聊天模型，始终走 Agnes 直连）。"""
    prof = (cfg.get("model_profiles") or {}).get("Agnes") or {}
    base = prof.get("base_url") or cfg.get("base_url") or "https://apihub.agnes-ai.cn/v1"
    key = prof.get("api_key") or cfg.get("api_key") or ""
    return base.rstrip("/"), key


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
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as e:
        return f"生图请求失败（Agnes 直连）：{e}"
    # 兼容多种返回：data[].url / images[].url / content(裸URL)
    images = data.get("data") or data.get("images") or []
    img_url = ""
    if images:
        img_url = images[0].get("url", "") or images[0].get("b64_json", "")
    if not img_url:
        content = (data.get("content") or "").strip()
        if content.startswith("http"):
            img_url = content
    if not img_url:
        return f"生图失败：{data}"
    if progress:
        progress("⬇ 下载并保存图片…")
    try:
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
    """将视频下载到产物目录「视频」子目录，返回 (rel, 'video', name)。"""
    v_dir = os.path.join(PRODUCTS_DIR, "视频")
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
        p2 = os.path.join(app_dir, p)
        if os.path.isfile(p2):
            p = p2
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


def tool_video_gen(cfg, app_dir, prompt, duration=None, aspect=None, resolution=None,
                   image=None, first_frame=None, last_frame=None, dialogue=None,
                   progress=None, images=None):
    """生视频（统一内核：委托 video-agent/core 的 AgnesClient）。

    与网页版 / director_panel 共用同一套 core/，根除两份 Agnes 视频客户端。
    模型固定 agnes-video-2.5-flash（720P，seconds 4-12，三模式 text/keyframe/reference）。

    返回 (rel, 'video', name) 交付物元组，或错误字符串。
    duration: 秒，自动钳制到 [4,12]；aspect: 'landscape'/'portrait'；
    resolution: 仅作宽高提示（2.5-flash 实际分辨率由 size 决定，默认 720P）；
    image / images: 图生视频参考图（reference 模式，≤5 张）；
    first_frame/last_frame: 首尾帧（keyframe 模式）；
    dialogue: 口播台词（中文），模型合成中文语音 + 对口型。
    """
    if not prompt:
        return "未提供视频描述"
    try:
        from core_agnes import AgnesClient, AgnesError
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
    # 参考图归并：images(多) 优先，其次 image(单)
    ref_list = []
    if images:
        ref_list = list(images) if isinstance(images, (list, tuple)) else [images]
    elif image:
        ref_list = [image]
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
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    v_dir = os.path.join(PRODUCTS_DIR, "视频")
    os.makedirs(v_dir, exist_ok=True)
    dest_path = os.path.join(v_dir, f"video_{stamp}.mp4")
    try:
        client = AgnesClient(api_key=key, base_url=base, video_model="agnes-video-2.5-flash")
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
            timeout=120,
        )
    except AgnesError as e:
        return f"视频生成失败（统一内核）：{e.msg}"
    except Exception as e:
        return f"视频生成失败（统一内核）：{e}"
    if not path or not os.path.isfile(path):
        return "视频生成未返回本地文件"
    rel = _safe_relpath(path, app_dir)
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

    # 跨多目录查找技能（.py 与 SKILL.md 两种形态）
    prompt = None
    skill_dir = None
    for d in dirs:
        p = load_skill_prompt(skill_name, d)
        if p:
            prompt = p
            _sub = os.path.join(d, skill_name)
            skill_dir = _sub if os.path.isdir(_sub) else d
            break

    if prompt is None:
        # 汇总所有目录的可用技能名
        names = []
        for d in dirs:
            try:
                names.extend(s.get("name", "") for s in get_available_skills(d))
            except Exception:
                pass
        names = "、".join(dict.fromkeys([n for n in names if n])) or "（无）"
        return f"未找到技能「{skill_name}」。可用技能：{names}"

    if not prompt.strip():
        return f"技能「{skill_name}」已加载，但未定义内容。"

    return (
        f"【已加载技能：{skill_name}】\n"
        f"技能目录：{skill_dir}\n（如技能引用 references/ 下的文件，可用 read_file 读取该目录下的文件）\n"
        f"请严格按以下专家指令完成本次任务：\n\n{prompt}\n\n"
        f"【执行要求】加载技能后必须立即调用相应工具（如 run_python / image_gen）动手执行任务，"
        f"禁止只列出计划或大纲而不实际行动。"
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
    rd = cfg.get("rag_data_dir", "") or os.path.join(app_dir, "rag_data")
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
        if port == 465:
            s = smtplib.SMTP_SSL(smtp_host, port, timeout=15)
        else:
            s = smtplib.SMTP(smtp_host, port, timeout=15)
            s.starttls()
        s.login(smtp_user, smtp_pass)
        s.send_message(msg)
        s.quit()
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
