# -*- coding: utf-8 -*-
"""v4.195 批⑨：Evidence Registry —— 证据登记处（根治架构的地基）。

## 为什么要这个模块

现有八层防线（v4.194.1）**全在猜模型的意图**：

- 批③猜「这句话是不是在引用版本号」
- 批⑤/⑦猜「它到底读完没有」
- legion 猜「这个数字附近有 URL 就算有来源」

**猜就有绕过空间。** 外部审查列出的 5 种绕过方式（先集中列来源再给数字、
「据公开资料」模糊措辞、来源与数字无关、把推断写成「估算」降级、
用表格让数字脱离检测窗口），本质上都是**让机器猜不出来**。

根治的办法是把 detective（事后检测）换成 declarative（事前声明）：

    工具结果进模型之前，先登记成**编号证据 [EV#n]**（完整原文留存）；
    模型引用时必须显式标注 ⟦EV#n⟧；
    机器只做**验证**（这条 EV 的原文是否真的支撑这句断言），
    不再做**推断**（满世界扫 URL 猜它有没有来源）。

## 本模块的三件事

1. `register()` —— 工具返回时登记**完整原文**
   （当前三处保存点全是压缩过的：上下文 compress→6000、
   tool_log→500，等于没有任何一处留得下原文 —— 这是审查问题 7 的真身）
2. `get()` —— 按编号取回原文，供 claim-evidence 回验
3. `model_header()/model_footer()` —— 给模型看的编号抬头与钉子，
   沿用批⑥「首尾双钉」已被真机验证有效的手法

## 设计约束

- **绝不阻断主流程**：任何异常都降级为「不登记」，工具调用照常。
- **允许行级引用**：保留行数组，使模型可以引用 ⟦EV#n:L120-180⟧ 精确片段，
  回验时能缩小到具体几行而不是整个证据块。
- **体积可控**：`prune()` 按条数/天数裁剪，避免无限膨胀。
"""

import os
import re
import time
import json
import hashlib
import sqlite3
import threading
import logging
from datetime import datetime

log = logging.getLogger(__name__)

# ── 存储位置：跟随 USER_DATA_DIR（~\\Documents\\小臭玩AI），不放 dist 顶层 ──
_DEFAULT_DIR = None
_LOCK = threading.RLock()
_MAX_ROWS = 2000          # 保留最近条数
_MAX_AGE_DAYS = 30        # 保留天数
_SEQ = 0                  # 本进程内单调递增编号（跨轮唯一，便于模型引用）


def set_dir(path):
    """测试/改道用：设置证据库目录（必须在 register 前调用才生效）。"""
    global _DEFAULT_DIR
    _DEFAULT_DIR = path
    global _DB_READY
    _DB_READY = False


def _dir():
    global _DEFAULT_DIR
    if _DEFAULT_DIR:
        return _DEFAULT_DIR
    try:
        import config
        return os.path.join(config.USER_DATA_DIR, "evidence")
    except Exception:
        return os.path.join(
            os.path.expanduser("~"), "Documents", "小臭玩AI", "evidence")


_DB_PATH_CACHE = {}
_DB_READY = False


def _db_path():
    d = _dir()
    if d not in _DB_PATH_CACHE:
        _DB_PATH_CACHE[d] = os.path.join(d, "evidence.db")
    return _DB_PATH_CACHE[d]


def _ensure_dir():
    try:
        os.makedirs(_dir(), exist_ok=True)
        return True
    except Exception as e:
        log.warning("证据目录创建失败: %s", e)
        return False


_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence (
    id       INTEGER PRIMARY KEY,
    ts       REAL    NOT NULL,
    ts_str   TEXT,
    tool     TEXT,
    args     TEXT,
    raw      TEXT,
    n_chars  INTEGER,
    n_lines  INTEGER,
    ok       INTEGER DEFAULT 1,
    err      TEXT,
    sha      TEXT
);
CREATE INDEX IF NOT EXISTS idx_ev_ts ON evidence(ts);
"""


def _connect():
    p = _db_path()
    conn = sqlite3.connect(p, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    global _DB_READY
    if _DB_READY:
        return True
    if not _ensure_dir():
        return False
    try:
        conn = _connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _DB_READY = True
        return True
    except Exception as e:
        log.warning("证据库初始化失败（登记降级为不启用）: %s", e)
        return False


# ────────────────────────── 登记 ──────────────────────────

# ── v4.214.0：入库脱敏（外部审核 P1-3 可采纳部分）──────────────────────
# 设计取舍：证据链的存在意义是「完整原文可回验」（v4.195 批⑨），所以只对
# **凭据类**内容打码，其余一字不动 —— 不做「默认只存摘要」（那会摧毁回验能力）。
# 规则全部带上下文（键名 / Bearer|Basic 前缀 / sk- 前缀 / JWT 三段结构 / Cookie 头行），
# 禁止裸「N 位字母数字」泛匹配（L233：泛规则必造噪音）。
_MASK = "***"
# ① 键值上下文：key: value / key=value / "key": "value"，值 ≥8 位才算凭据
#    （短值如 token:5 是普通内容，不打码）。
_KV_RE = re.compile(
    r"(?i)([\"']?)(api[-_]?key|access_token|refresh_token|token|client_secret|"
    r"secret|password|passwd|authorization|sessionid|session_id)"
    r"([\"']?)(\s*[:=]\s*)([\"']?)([^\s\"',;&]{8,})")
_KV_REPL = r"\1\2\3\4\5" + _MASK
# ② HTTP 认证方案：Bearer / Basic + 12 位以上凭据值。
_AUTH_SCHEME_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}")
_AUTH_SCHEME_REPL = r"\1 " + _MASK
# ③ sk- 前缀 API key（OpenAI/DeepSeek 风格，自带上下文无需键名）。
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")
# ④ JWT 三段结构（eyJ 开头自带指纹，无需上下文）。
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
# ⑤ Cookie / Set-Cookie 整行头（HTTP 响应转储场景）。
_COOKIE_RE = re.compile(r"(?im)^([ \t]*(?:set-)?cookie[ \t]*:[ \t]*).+$")


def _mask_secrets(text):
    """凭据打码：五类规则依次过一遍，其余内容一字不动。

    失败兜底：打码异常时原文放行（登记不中断 —— 证据链连续性优先，
    且 str 上的 re.sub 实际不可能抛）。
    """
    if not text:
        return text
    try:
        text = _KV_RE.sub(_KV_REPL, text)
        text = _AUTH_SCHEME_RE.sub(_AUTH_SCHEME_REPL, text)
        text = _SK_RE.sub(_MASK, text)
        text = _JWT_RE.sub(_MASK, text)
        text = _COOKIE_RE.sub(r"\1" + _MASK, text)
    except Exception:
        pass
    return text


def register(tool, args, raw, ok=True, err=None):
    """登记一条工具证据，返回编号 (int)；任何失败返回 None（绝不阻断主流程）。

    args  : dict 或 str，工具入参（照原文留存，便于回验「到底读了哪一段」）
    raw   : 工具返回的**完整原文**（未经 compress / clip）
    ok    : 工具调用是否成功 —— False 的证据不可作为支撑（工具失败禁止补全）
    err   : 失败原因文本
    """
    global _SEQ
    try:
        if raw is None:
            raw = ""
        if not isinstance(raw, str):
            raw = str(raw)
        try:
            args_s = args if isinstance(args, str) else json.dumps(
                args or {}, ensure_ascii=False)
        except Exception:
            args_s = str(args)
        # v4.214.0：入库前凭据打码（raw/args/err 三处；sha 基于打码后文本，
        # 库内自洽 —— 回验只对库内原文，不受影响）
        raw = _mask_secrets(raw)
        args_s = _mask_secrets(args_s)
        if err:
            err = _mask_secrets(str(err))
        n_lines = raw.count("\n") + (0 if raw.endswith("\n") or not raw else 1)
        sha = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12]
        ts = time.time()
        with _LOCK:
            _SEQ += 1
            eid = _SEQ
        if not _init_db():
            return None
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO evidence (id, ts, ts_str, tool, args, raw, "
                "n_chars, n_lines, ok, err, sha) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (eid, ts, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 str(tool or ""), args_s, raw, len(raw), n_lines,
                 1 if ok else 0, (err or None), sha))
            conn.commit()
        finally:
            conn.close()
        # 顺手裁剪（不在关键路径做太多事：每 20 条裁一次）
        with _LOCK:
            if eid % 20 == 0:
                try:
                    prune()
                except Exception:
                    pass
        return eid
    except Exception as e:
        log.warning("证据登记失败（忽略）: %s", e)
        return None


# ────────────────────────── 取回 ──────────────────────────

def get(eid):
    """按编号取回证据 dict；不存在或异常返回 None。"""
    try:
        if not eid or not _init_db():
            return None
        conn = _connect()
        try:
            r = conn.execute(
                "SELECT * FROM evidence WHERE id = ?", (int(eid),)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()
    except Exception as e:
        log.warning("证据取回失败: %s", e)
        return None


def raw_text(eid):
    """取回某证据的完整原文；无则空串。"""
    r = get(eid)
    return (r or {}).get("raw") or ""


def lines_slice(eid, start=None, end=None):
    """按行区间取证据片段（1-based，闭区间）。越界自动裁剪。"""
    txt = raw_text(eid)
    if not txt:
        return ""
    ls = txt.split("\n")
    a = max(1, int(start or 1))
    b = min(len(ls), int(end or len(ls)))
    if b < a:
        return ""
    return "\n".join(ls[a - 1:b])


def recent(n=20, ok_only=False):
    """最近 n 条证据的轻量列表（不含原文，避免撑爆）。"""
    try:
        if not _init_db():
            return []
        conn = _connect()
        try:
            q = "SELECT id, ts_str, tool, args, n_chars, n_lines, ok, sha FROM evidence"
            if ok_only:
                q += " WHERE ok=1"
            q += " ORDER BY id DESC LIMIT ?"
            return [dict(r) for r in conn.execute(q, (int(n),)).fetchall()]
        finally:
            conn.close()
    except Exception as e:
        log.warning("证据列表失败: %s", e)
        return []


# ───────────────────── 给模型看的抬头 / 钉子 ─────────────────────

def model_header(eid, tool=None, n_chars=None, n_lines=None, sha=None,
                 ts_str=None, ok=True, err=None, args_head=None):
    """生成注入模型上下文的 [EV#n] 抬头。

    沿用批⑥「首尾双钉」—— 已由 v4.191.0 真机对照实验证明有效
    （扑空三处全部诚实报 RESULT NOT FOUND，零编造）。
    """
    bits = ["[EV#%d] 证据登记%s" % (int(eid), "" if ok else "（本次调用失败）")]
    meta = []
    if tool:
        meta.append("工具 %s" % tool)
    if args_head:
        meta.append("参数 %s" % args_head)
    if n_lines:
        meta.append("共 %d 行" % n_lines)
    if n_chars:
        meta.append("%d 字符" % n_chars)
    if sha:
        meta.append("校验 %s" % sha)
    if ts_str:
        meta.append(ts_str)
    if meta:
        bits.append("  " + " · ".join(meta))
    if ok:
        bits.append("  ↓ 以下为该证据内容（可能被压缩，不代表完整原文）")
    else:
        bits.append("  ⚠ 本次调用失败：" + str(err or "未知原因")
                    + " —— **禁止据此补全任何事实**，只能如实报告失败")
    return "\n".join(bits) + "\n"


def model_footer(eid):
    """页脚钉子：强调引用纪律。"""
    return ("\n[/EV#%d] 以上内容需引用时，必须在结论处标注 ⟦EV#%d⟧"
            "（可精确到行：⟦EV#%d:L12-30⟧）。"
            "未标注即视为无证据的主张。" % (int(eid), int(eid), int(eid)))


# ───────────────────── ⟦EV#n⟧ 标记解析 ─────────────────────

_EV_TAG_RE = re.compile(r"⟦EV#(\d+)(?::L(\d+)-(\d+))?⟧")
# 兼容半角写法（模型可能输出 [EV#12] 这种）—— 但必须与既有 markdown 链接区分，
# 故只认紧跟 ! 或 ⟦ 的形式；这里退一步认 [EV#n] 且前面不是 "]" 的情况。
_EV_TAG_ASCII_RE = re.compile(r"(?<![\]\w])!?\[EV#(\d+)(?::L(\d+)-(\d+))?\]")


def parse_ev_tags(text):
    """从文本里解析所有 ⟦EV#n⟧ 标记。

    返回 [(eid, start_line_or_None, end_line_or_None)]，按出现顺序去重（保留首次）。
    """
    out, seen = [], {}
    if not text:
        return out
    for rx in (_EV_TAG_RE, _EV_TAG_ASCII_RE):
        for m in rx.finditer(text):
            eid = int(m.group(1))
            s, e = m.group(2), m.group(3)
            has_line = bool(s and e)
            prev = seen.get(eid)
            if prev is None:
                item = (eid, int(s) if has_line else None,
                        int(e) if has_line else None)
                seen[eid] = item
                out.append(item)
            elif has_line and prev[1] is None:
                # 同一证据被引用两次，第二次更精确（带行号）→ 升级。
                # 去重若按「首次命中优先」，⟦EV#1⟧ 在前会把 ⟦EV#1:L10-12⟧ 的行号
                # 信息丢掉，回验范围从 12 行退化成整篇（精度即漏检率）。
                item = (eid, int(s), int(e))
                seen[eid] = item
                out[out.index(prev)] = item
    return out


def strip_ev_tags(text):
    """去掉 ⟦EV#n⟧ 标记（用于给用户的干净展示，内部仍保留 ID 信息）。"""
    if not text:
        return text
    t = _EV_TAG_RE.sub("", text)
    t = _EV_TAG_ASCII_RE.sub("", t)
    return t


# ───────────────────── 回验辅助 ─────────────────────

def evidence_text_for(eid, start_line=None, end_line=None):
    """取某证据的可回验文本（按行区间切片；无区间则全文）。"""
    if start_line or end_line:
        return lines_slice(eid, start_line, end_line)
    return raw_text(eid)


def supports(eid, needle, start_line=None, end_line=None):
    """**确定性回验**：证据原文里是否真的含 needle（字符串级）。

    这是根治的关键 —— 不再问「附近有没有 URL」，而是问
    「这条被引用的证据原文里，有没有这个具体事实」。
    """
    if needle is None or str(needle).strip() == "":
        return False
    return str(needle) in evidence_text_for(eid, start_line, end_line)


def prune(max_rows=_MAX_ROWS, max_age_days=_MAX_AGE_DAYS):
    """按条数 + 天数裁剪，防止证据库无限膨胀。"""
    try:
        if not _init_db():
            return 0
        conn = _connect()
        try:
            cur = conn.execute(
                "DELETE FROM evidence WHERE id NOT IN "
                "(SELECT id FROM evidence ORDER BY id DESC LIMIT ?)",
                (int(max_rows),))
            n1 = cur.rowcount or 0
            cut = time.time() - max_age_days * 86400
            cur = conn.execute("DELETE FROM evidence WHERE ts < ?", (cut,))
            n2 = cur.rowcount or 0
            conn.commit()
            return max(n1, n2)
        finally:
            conn.close()
    except Exception as e:
        log.warning("证据库裁剪失败: %s", e)
        return 0


def clear():
    """清空证据库（测试用 / 手动重置）。"""
    try:
        if not _init_db():
            return False
        conn = _connect()
        try:
            conn.execute("DELETE FROM evidence")
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as e:
        log.warning("证据库清空失败: %s", e)
        return False


def count():
    try:
        if not _init_db():
            return 0
        conn = _connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return 0
