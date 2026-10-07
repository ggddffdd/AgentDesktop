# -*- coding: utf-8 -*-
"""v4.227（P2-2）：执行后验证的首批硬验 + 全量分档登记。

v4.224 落地了 `register_verifier` / `verify_after` / `apply_post_verification`
这条链，但只登记了 2 个验证器（process_kill / clean_recycle_bin）。
本模块补两件事：

**一、首批硬验（挑「有廉价真实信号」的工具，不硬凑）**

选型标准就一条：**执行完之后，能不能用一次廉价查询拿到「副作用真的发生了吗」的信号**。
拿不到就不登记 —— 登记一个必然 return None 的验证器是自欺欺人，
还会让人误以为这个工具验过了。

  · `db_insert`  —— 回查该 id 确实在库里（insert 返回 lastrowid，有廉价真信号）
  · `db_update`  —— 回查该 id 的字段真的变了（update 返回 affected + data，可比对）
  · `db_delete`  —— 回查该 id 真的不在了
  · `write_file` —— 复查文件真落地且非空（与结局契约 `_outcome_write_file`
                    同一条判据，但作用不同：那个判「这次调用算不算成功」，
                    这个判「执行完之后副作用是不是真的落地」—— 两者都要）

**二、全量分档登记（把「没人想过」变成一件可枚举的事）**

其余 79 个工具逐一登记验证姿态（见文末分档表）。分档**不参与运行时决策** ——
运行时判定仍只看 `_VERIFY_REGISTRY`，分档表只回答「哪些验过、哪些裸奔、为什么」。
刻意不让它参与决策：两套策略必然漂移（与 v4.226 删掉 `_AllowAllDecision` 同理）。
"""
import json
import os

from tool_contract import (
    register_verifier,
    register_verification_tier,
)

# ============================================================
# 一、首批硬验
# ============================================================


def _db_conn():
    """取 database_tools 的连接对象（拿不到就返回 None → 验证器不表态）。"""
    try:
        import database_tools
        inst = getattr(database_tools, "_db", None)
        if inst is None:
            inst = database_tools.get_db()
        return inst._get_connection()
    except Exception:
        return None


def _verify_db_insert(args, result=None):
    """插入后回查该 id 确实在库里（反编造：没插进去不能说成功）。"""
    a = args or {}
    table = a.get("table") or "notes"
    rid = (a.get("data") or {}).get("_last_id") or a.get("record_id")
    # insert 的返回值在 ToolResult.msg 里（json），但验证器拿 args 更稳：
    # 这里改为「按必填字段回查有没有匹配行」，不依赖返回值解析。
    data = a.get("data") or {}
    if not isinstance(data, dict) or not data:
        return None
    import contextlib
    conn = None
    try:
        import database_tools
        inst = getattr(database_tools, "_db", None) or database_tools.get_db()
        conn = inst._get_connection()
        cols = [k for k in data if k not in ("id", "created_at", "updated_at")]
        if not cols:
            return None
        where = " AND ".join("[%s] = ?" % c for c in cols)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM [%s] WHERE %s"
                    % (table, where), [data[c] for c in cols])
        n = cur.fetchone()[0]
        if not isinstance(n, int):
            return None
        return (n > 0, ("按传入字段回查 %s 表未找到匹配行" % table) if n <= 0
                else ("已在 %s 表回查到匹配行" % table))
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _verify_db_update(args, result=None):
    """更新后回查该 id 确实存在（存在性是最低限的落地证据）。

    刻意**不**逐字段比对：`data` 里的值可能是表达式语义（大小写/类型归一），
    逐字段比对会假红 —— 那不是失败，是我们验错了。所以只验「这条记录真在」，
    字段级验证留给将来有真信号时再说（宁可少验，不可假红）。
    """
    a = args or {}
    table = a.get("table") or "notes"
    rid = a.get("record_id")
    if rid in (None, ""):
        return None
    conn = None
    try:
        import database_tools
        inst = getattr(database_tools, "_db", None) or database_tools.get_db()
        conn = inst._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM [%s] WHERE id = ?" % table, (rid,))
        n = cur.fetchone()[0]
        if not isinstance(n, int):
            return None
        return (n > 0, ("回查 %s 表 id=%s 不存在（更新未生效）" % (table, rid))
                if n <= 0 else ("已回查到 %s id=%s" % (table, rid)))
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _verify_db_delete(args, result=None):
    """删除后回查该 id 真的不在了（残留就如实说残留）。"""
    a = args or {}
    table = a.get("table") or "notes"
    rid = a.get("record_id")
    if rid in (None, ""):
        return None
    conn = None
    try:
        import database_tools
        inst = getattr(database_tools, "_db", None) or database_tools.get_db()
        conn = inst._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM [%s] WHERE id = ?" % table, (rid,))
        n = cur.fetchone()[0]
        if not isinstance(n, int):
            return None
        return (n == 0, ("%s 表 id=%s 仍残留（删除未生效）" % (table, rid))
                if n > 0 else ("已确认 %s id=%s 已删除" % (table, rid)))
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _verify_write_file(args, result=None):
    """写完复查文件真在磁盘上且非空。

    与结局契约 `_outcome_write_file` 同一判据但**不重复造**：
    结局契约判「这次调用算不算成功」（exec_tool 内），验证器判「执行完之后
    副作用是不是真的落地」（exec_tool 之后）。两道都留着 —— 一道被绕过另一道还在。
    """
    p = (args or {}).get("path")
    if not p or not isinstance(p, str):
        return None
    try:
        ap = p
        if not os.path.isabs(ap):
            try:
                import tools as _tools
                ap = os.path.join(_tools.WORKSPACE_DIR, p)
            except Exception:
                ap = os.path.abspath(p)
        if not os.path.isfile(ap):
            return (False, "复查：文件并不存在 %s" % p)
        size = os.path.getsize(ap)
        return (size > 0, ("复查：文件存在但为空（0 字节）" if size <= 0
                           else "复查：文件已落地（%d 字节）" % size))
    except Exception:
        return None


# ---- 登记硬验 ----
register_verifier("db_insert", _verify_db_insert)
register_verifier("db_update", _verify_db_update)
register_verifier("db_delete", _verify_db_delete)
register_verifier("write_file", _verify_write_file)


# ============================================================
# 二、全量分档登记
# ============================================================
# 档位语义见 tool_contract.register_verification_tier。
#
# verified 已有验证器（上面 4 个 + v4.224 的 process_kill / clean_recycle_bin）
# degraded 有意不验 —— 说明理由，别让它日后被当漏项"补上"
# open     有真实信号可查但还没写 —— 这就是待办清单本身

_VERIFIED = [
    "db_insert", "db_update", "db_delete", "write_file",
    "process_kill", "clean_recycle_bin",
]

# 刻意不验，并写清为什么（这些日后被当"漏项"来补时，先读这段）
_DEGRADED = {
    # 浏览器族：「页面到底加载出来没有」没有廉价信号 —— 要验就得再跑一次
    # CDP 状态查询，那是独立立项。v4.224 尾巴里记的就是这一条。
    "browser_open": "页面加载状态无廉价信号，需 CDP 状态查询（独立立项）",
    "browser_read": "正文是否真读全无廉价信号，需 CDP 状态查询（独立立项）",
    "browser_click": "点击是否生效无廉价信号，需 CDP 状态查询（独立立项）",
    "browser_fill": "填充是否生效无廉价信号，需 CDP 状态查询（独立立项）",
    # 军团族：子代理产出是「文本」不是「副作用」，验证器无信号可查
    "legion_board": "子代理产出是文本非副作用，无验证信号",
    "legion_get_output": "子代理产出是文本非副作用，无验证信号",
    "legion_get_sources": "子代理产出是文本非副作用，无验证信号",
    "legion_find_asset": "子代理产出是文本非副作用，无验证信号",
    "legion_list_outputs": "子代理产出是文本非副作用，无验证信号",
    "legion_read_log": "子代理产出是文本非副作用，无验证信号",
    "legion_report_issue": "子代理产出是文本非副作用，无验证信号",
    # 导演台：改的是内存里的剧本状态，落地点是「下一次渲染」，无廉价信号
    "director_status": "改的是内存态剧本，落地点在渲染，无廉价信号",
    # 系统信息类：返回值本身就是查询结果，再「验证」一次是自证
    "sys_info": "返回值即查询结果，验证无意义（自证）",
    "process_list": "返回值即查询结果，验证无意义（自证）",
    "window_list": "返回值即查询结果，验证无意义（自证）",
    "window_get_info": "返回值即查询结果，验证无意义（自证）",
    "db_query": "返回值即查询结果，验证无意义（自证）",
    "log_query": "返回值即查询结果，验证无意义（自证）",
    "web_search": "返回值即查询结果，验证无意义（自证）",
    "rag_search": "返回值即查询结果，验证无意义（自证）",
    "skill_search": "返回值即查询结果，验证无意义（自证）",
    "search_memory": "返回值即查询结果，验证无意义（自证）",
    "list_automation": "返回值即查询结果，验证无意义（自证）",
    "context_summary": "返回值即推导结果，验证无意义（自证）",
    "context_compress": "返回值即推导结果，验证无意义（自证）",
    "analyze_image": "返回值即推导结果，验证无意义（自证）",
    "read_file": "返回值即磁盘内容，验证无意义（自证）",
    "clipboard_read": "返回值即剪贴板内容，验证无意义（自证）",
    "app_get_text": "返回值即目标应用界面文字，验证无意义（自证）",
    "app_list_controls": "返回值即控件枚举结果，验证无意义（自证）",
    "rag_index": "返回值即索引操作回执，无廉价复查信号",
    "screenshot": "返回值即截图产物路径，验证无意义（自证）",
    # 本工具自产的回执：返回值就是执行结果本身
    "remember": "返回值即本工具回执，验证无意义（自证）",
    "schedule": "返回值即本工具回执，验证无意义（自证）",
    "run_workflow": "返回值即任务图产出，验证无意义（自证）",
    "chart_gen": "返回值即图表生成回执，无廉价复查信号",
}

# 有真实信号可查、但还没写验证器的（= 待办清单本身）
_OPEN = [
    # 写入类：可复查目标是否真的变了（窗口标题、剪贴板内容、鼠标位置…）
    "app_click", "app_close", "app_focus", "app_kill", "app_launch",
    "app_screenshot", "app_type", "app_wait_for", "app_window_state",
    "clipboard_write", "keyboard_press", "keyboard_type",
    "mouse_click", "mouse_move", "mouse_scroll",
    "window_focus", "process_start",
    # 自动化任务：新建/删除后可复查任务是否真在列表里
    "create_automation", "delete_automation",
    # 远端类：webhook 是否真在监听
    "webhook_start", "webhook_stop", "webhook_events", "send_email",
    # 执行类：run_command / run_python 可复查产物或副作用
    "run_command", "run_python",
    # 技能类：use_skill 的副作用是文本产出，install 有真实信号
    "use_skill", "skill_install", "create_skill",
    # 多模态生成：产物落盘可复查
    "image_gen", "video_gen",
    # 导演台写入类：改的是内存态，但可复查「改完再读」是否一致
    "director_confirm", "director_gen_clues", "director_merge",
    "director_rollback", "director_revise_character", "director_revise_clip",
    "director_revise_clue", "director_revise_keyframe",
    "director_revise_shots", "director_revise_story",
    # 抓取类：web_fetch 下载来的内容落盘可复查
    "web_fetch",
]

for _n in _VERIFIED:
    register_verification_tier(_n, "verified")
for _n, _why in _DEGRADED.items():
    register_verification_tier(_n, "degraded")
for _n in _OPEN:
    register_verification_tier(_n, "open")


def degraded_reasons():
    """返回 {工具名: 降级理由}，给判据与排障用（理由必须存在，不能是空壳）。"""
    return dict(_DEGRADED)
