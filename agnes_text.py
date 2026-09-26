# -*- coding: utf-8 -*-
"""Agnes 文本模型统一调用层（v4.128）。

背景：Agnes 9 月初上线 `agnes-3.0-flash`（512K 上下文 / 65,536 输出），官方主打
「减少未完成任务错误确认 / 长任务持续遵循目标 / 重视工具结果 / 减少空转循环」，
正好对上军团实测的 PM 放水、写手交错交付形态、悬空引用三类痛点。但发布初期第三方
实测稳定性差（开思考后异常中断、流中断），所以本版只做**可选 + 自动回退**，
不默认硬切：默认仍是现役稳定的 `agnes-2.5-flash`。

本模块只负责「文本」环节（军团 PM/成员、导演台剧本/分镜/提示词/验收），
**不碰生图（agnes-image-2.5-flash）与生视频（agnes-video-2.5-flash）**。

三件事：
1. 模型可配置（3.0 / 2.5 两档，默认 2.5）+ 回退链（仅选 3.0 时生效）。
2. 失败情形判定：超时 / 5xx / 429 / 流中断（无 finish_reason）/ 空响应 / 结构异常
   → 自动回退 2.5 重试一次，并打日志「3.0→2.5 回退」+ 失败原因。
3. 回退率统计（进程内滑动窗口），连续回退率过高给出「建议切回 2.5」提示。

零第三方依赖（urllib + json），打包环境（PyInstaller）与开发态行为一致。
"""

import json
import threading
import urllib.error
import urllib.request

# ---------------- 模型档位 ----------------
AGNES_TEXT_30 = "agnes-3.0-flash"
AGNES_TEXT_25 = "agnes-2.5-flash"
AGNES_TEXT_CANDIDATES = (AGNES_TEXT_30, AGNES_TEXT_25)
DEFAULT_TEXT_MODEL = AGNES_TEXT_25          # 默认保守：现役稳定版
FALLBACK_TEXT_MODEL = AGNES_TEXT_25         # 回退目标

# 官方：最大输出 65,536 Token。开 thinking 时思考过程也占输出额度 → 必须给足，
# 否则正文被截断（实测开思考后只给 4096 会输出半截）。
AGNES_MAX_OUTPUT_TOKENS = 65536
AGNES_THINKING_MAX_TOKENS = 65536

# ---------------- 域名 ----------------
# ⚠️ 以本机实测为准，不以官方文档为准：
# 现有 key 在 apihub 通道实测正常，而官方国内站 api.agnes-ai.cn 属另一套账号体系，
# 用现有 key 直接 401 无效令牌（2026-08-30 定论）。所以默认仍是 apihub，
# api 域名只登记为**可选候选**（配置项可切），绝不默认切换。
AGNES_BASE_DEFAULT = "https://apihub.agnes-ai.cn/v1"
AGNES_BASE_CN_DOC = "https://api.agnes-ai.cn/v1"
AGNES_BASE_CANDIDATES = (AGNES_BASE_DEFAULT, AGNES_BASE_CN_DOC)

# 价格口径：官方刊例价输入 ¥0.35/百万、输出 ¥1.00/百万，当前**限时 ¥0**。
# 代码不得写死「永久免费」假设，界面统一显示「限免」。
AGNES_PRICE_LABEL = "限免"

# ---------------- 失败分类 ----------------
# 可回退（换小版本重试试试看）与不可回退（请求本身有问题，换模型也一样）
FALLBACKABLE_KINDS = ("timeout", "network", "http5xx", "http429",
                      "stream_break", "empty", "structure")
KIND_LABEL = {
    "timeout": "请求超时",
    "network": "网络异常",
    "http5xx": "服务端 5xx",
    "http429": "限流 429",
    "stream_break": "流中断（无 finish_reason）",
    "empty": "空响应",
    "structure": "响应结构异常",
    "http4xx": "请求被拒 4xx",
    "unknown": "未知错误",
}


class AgnesTextError(Exception):
    """Agnes 文本调用失败。kind 决定要不要回退。"""

    def __init__(self, kind="unknown", msg="", code=None):
        super().__init__(msg or KIND_LABEL.get(kind, kind))
        self.kind = kind
        self.msg = msg
        self.code = code


def _label(err):
    k = getattr(err, "kind", None)
    if k:
        base = KIND_LABEL.get(k, k)
        detail = getattr(err, "msg", "")
        return f"{base}：{detail}" if detail else base
    return f"{type(err).__name__}：{err}"


# ---------------- 统计（进程内滑动窗口）----------------
_LOCK = threading.Lock()
# 口径：calls 记「逻辑调用次数」（一次 agnes_chat / _agent_call 算 1 次），
# 中途失败后换档重试只记 attempts，不重复计 calls —— 否则回退率恒被稀释成 1/N。
_STATS = {"calls": 0, "fallbacks": 0, "attempts": 0, "recent": []}
_RECENT_WINDOW = 20
_UNSTABLE_RATE = 0.3
_UNSTABLE_MIN_CALLS = 5


def note_call(model="", fallback=False, ok=True):
    """记一次**逻辑调用**结果（只统计 Agnes 文本模型链路上的调用）。"""
    try:
        with _LOCK:
            _STATS["calls"] += 1
            if fallback:
                _STATS["fallbacks"] += 1
            _STATS["recent"].append(1 if fallback else 0)
            if len(_STATS["recent"]) > _RECENT_WINDOW:
                del _STATS["recent"][:-_RECENT_WINDOW]
    except Exception:
        pass


def note_attempt(model="", ok=False):
    """记一次失败的换档尝试（不计入 calls，只累计 attempts 供排查）。"""
    try:
        with _LOCK:
            _STATS["attempts"] += 1
    except Exception:
        pass


def stats():
    with _LOCK:
        return {
            "calls": _STATS["calls"],
            "fallbacks": _STATS["fallbacks"],
            "attempts": _STATS["attempts"],
            "rate": (float(_STATS["fallbacks"]) / _STATS["calls"]
                     if _STATS["calls"] else 0.0),
            "recent_rate": (float(sum(_STATS["recent"])) / len(_STATS["recent"])
                            if _STATS["recent"] else 0.0),
            "window": len(_STATS["recent"]),
        }


def recent_fallback_rate():
    return stats()["recent_rate"]


def unstable_hint(min_calls=_UNSTABLE_MIN_CALLS, rate=_UNSTABLE_RATE):
    """近期回退率过高 → 返回给界面的提示文案；否则返回空串。"""
    s = stats()
    if s["window"] < min_calls:
        return ""
    if s["recent_rate"] < rate:
        return ""
    return ("agnes-3.0-flash 当前不稳定（近期回退率 %d%%），建议切回 agnes-2.5-flash"
            % int(round(s["recent_rate"] * 100)))


def reset_stats():
    with _LOCK:
        _STATS["calls"] = 0
        _STATS["fallbacks"] = 0
        _STATS["attempts"] = 0
        _STATS["recent"] = []


# ---------------- 模型解析 ----------------
def _normalize_model(m):
    """容错：「3.0」「agnes-3.0」「agnes-3.0-flash」都归一到标准名。"""
    s = (m or "").strip().lower()
    if not s:
        return ""
    if s in AGNES_TEXT_CANDIDATES:
        return s
    if s.startswith("agnes-"):
        for cand in AGNES_TEXT_CANDIDATES:
            if s == cand.replace("-flash", "") or s.startswith(cand.replace("-flash", "")):
                return cand
        return s
    if s in ("3.0", "3"):
        return AGNES_TEXT_30
    if s in ("2.5", "2"):
        return AGNES_TEXT_25
    return s


def is_agnes_text_model(model):
    return _normalize_model(model) in AGNES_TEXT_CANDIDATES


def is_agnes_base(base_url):
    return "agnes-ai" in (base_url or "").lower()


def resolve_text_model(cfg=None):
    """文本模型：显式配置 > Agnes 档位 chat_model > 档位 model > 默认 2.5。"""
    cfg = cfg or {}
    m = _normalize_model((cfg.get("agnes_text_model") or "").strip())
    if not m:
        prof = (cfg.get("model_profiles") or {}).get("Agnes") or {}
        m = _normalize_model((prof.get("chat_model") or prof.get("model") or "").strip())
    return m or DEFAULT_TEXT_MODEL


def chain_for(cfg=None, model="", base_url=""):
    """返回本次调用要依次尝试的模型列表（长度 1 或 2）。

    - 模型不是 Agnes 文本档（如 DeepSeek）→ 原样单发，本模块完全不介入。
    - 选 2.5（默认）→ 单发，**全程不碰 3.0**（保守）。
    - 选 3.0 且回退开关开 → ["agnes-3.0-flash", "agnes-2.5-flash"]。
    """
    cfg = cfg or {}
    m = _normalize_model(model) or resolve_text_model(cfg)
    if not is_agnes_text_model(m):
        return [m]
    if m != AGNES_TEXT_30:
        return [m]
    if not bool(cfg.get("agnes_text_fallback", True)):
        return [m]
    fb = _normalize_model((cfg.get("agnes_text_fallback_model") or FALLBACK_TEXT_MODEL).strip())
    if not fb or fb == m:
        return [m]
    return [m, fb]


def creds(cfg=None):
    """取 Agnes 通道的 base_url / api_key（与 tools._agnes_creds 同口径，独立实现避免循环导入）。"""
    cfg = cfg or {}
    prof = (cfg.get("model_profiles") or {}).get("Agnes") or {}
    base = (prof.get("base_url") or cfg.get("agnes_base_url")
            or cfg.get("base_url") or AGNES_BASE_DEFAULT)
    key = prof.get("api_key") or cfg.get("api_key") or ""
    return str(base).rstrip("/"), key


def thinking_payload(thinking=False, max_tokens=None):
    """Thinking 请求体片段。

    官方：Chat Completions 用 `chat_template_kwargs: {"enable_thinking": true}`。
    思考过程占用输出 Token 额度 → 开启时 max_tokens 必须给足（默认顶格 65,536）。
    """
    out = {}
    if thinking:
        out["chat_template_kwargs"] = {"enable_thinking": True}
        out["max_tokens"] = int(max_tokens or AGNES_THINKING_MAX_TOKENS)
    elif max_tokens:
        out["max_tokens"] = int(max_tokens)
    return out


def model_label(model="", cfg=None):
    """界面显示用：带上「限免」口径，不写死永久免费。"""
    m = _normalize_model(model) or resolve_text_model(cfg)
    if is_agnes_text_model(m):
        return f"{m}（{AGNES_PRICE_LABEL}）"
    return m or ""


# ---------------- 失败判定 ----------------
def classify_failure(err):
    """把异常归类为可回退/不可回退的 kind（供回退决策与日志）。"""
    if isinstance(err, AgnesTextError):
        return err.kind
    code = getattr(err, "code", None)
    if isinstance(code, int):
        if code >= 500:
            return "http5xx"
        if code == 429:
            return "http429"
        return "http4xx"
    name = type(err).__name__.lower()
    if "timeout" in name:
        return "timeout"
    if isinstance(err, urllib.error.URLError) or "urlerror" in name:
        msg = str(getattr(err, "reason", err)).lower()
        return "timeout" if "timed out" in msg or "timeout" in msg else "network"
    if isinstance(err, urllib.error.HTTPError):
        return "http5xx" if getattr(err, "code", 0) >= 500 else "http4xx"
    if isinstance(err, (json.JSONDecodeError, ValueError, KeyError, IndexError, TypeError)):
        return "structure"
    if isinstance(err, OSError):
        return "network"
    return "unknown"


def is_fallbackable(err):
    """这个失败值不值得换下一个模型重试一次？"""
    return classify_failure(err) in FALLBACKABLE_KINDS


def failure_label(err):
    return "%s（%s）" % (KIND_LABEL.get(classify_failure(err), "未知错误"), _label(err))


# ---------------- 底层：一次请求 ----------------
# 测试可注入 _OPENER（req, timeout）→ 返回带 read() 的响应对象，避免真打网络。
_OPENER = None


def _urlopen(req, timeout):
    if _OPENER is not None:
        return _OPENER(req, timeout)
    # 🔇 v4.128.1 热修：urlopen 第二个位置参数是 data 不是 timeout——
    # 写成 urlopen(req, timeout) 会把 240（int）当请求体发出去，
    # http.client 报「message_body should be a bytes-like object ... got <class 'int'>」。
    # mock 测试没抓到：_OPENER 签名 (req, timeout) 绕开了真 urlopen。
    return urllib.request.urlopen(req, timeout=timeout)


def _post_once(base, key, body, timeout):
    """发一次非流式请求，返回助手文本。失败抛 AgnesTextError（带 kind）。"""
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    url = str(base).rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with _urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
    except Exception as e:
        raise AgnesTextError(classify_failure(e), str(e), code=getattr(e, "code", None))
    try:
        data = json.loads(raw)
    except Exception as e:
        raise AgnesTextError("structure", f"响应不是合法 JSON：{e}")
    try:
        content = (data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        raise AgnesTextError("structure", "响应结构异常（缺 choices[0].message.content）")
    if not content:
        raise AgnesTextError("empty", "响应内容为空")
    return content


# ---------------- 对外主入口 ----------------
def agnes_chat(cfg=None, messages=None, model=None, temperature=0.7,
               thinking=False, max_tokens=None, timeout=240, log=None):
    """同步调用 Agnes 文本接口，返回助手文本（失败抛 RuntimeError）。"""
    text, _meta = agnes_chat_meta(cfg=cfg, messages=messages, model=model,
                                  temperature=temperature, thinking=thinking,
                                  max_tokens=max_tokens, timeout=timeout, log=log)
    return text


def agnes_chat_meta(cfg=None, messages=None, model=None, temperature=0.7,
                    thinking=False, max_tokens=None, timeout=240, log=None):
    """同 agnes_chat，额外返回 meta：{"model","requested","fallback","reason","attempts"}。

    回退链：仅当本次模型是 3.0 且开了回退时才可能有第二档；2.5 全程单发。
    """
    cfg = cfg or {}
    base, key = creds(cfg)
    chain = chain_for(cfg, model=model, base_url=base)
    meta = {"model": chain[0], "requested": chain[0], "fallback": False,
            "reason": "", "attempts": 0}
    last = None
    for i, m in enumerate(chain):
        meta["attempts"] += 1
        body = {"model": m, "messages": messages or [], "temperature": temperature}
        body.update(thinking_payload(thinking, max_tokens))
        try:
            content = _post_once(base, key, body, timeout)
        except Exception as e:
            last = e
            kind = classify_failure(e)
            if i < len(chain) - 1 and kind in FALLBACKABLE_KINDS:
                meta["reason"] = KIND_LABEL.get(kind, kind)
                if log:
                    log("  ↩︎ %s 失败（%s）→ 回退 %s" % (m, failure_label(e), chain[i + 1]))
                continue
            if is_agnes_text_model(chain[0]):
                note_attempt(m, ok=False)
            # 最后一档也失败 → 包成 RuntimeError 上抛（与 v4.128 之前 _agnes_chat
            # 的异常契约一致，调用方 catch RuntimeError 的行为不变）。
            raise RuntimeError(f"Agnes 对话接口失败：{failure_label(e)}") from e
        if is_agnes_text_model(chain[0]):
            note_call(m, fallback=(i > 0), ok=True)
        meta["model"] = m
        meta["fallback"] = i > 0
        if i > 0:
            if log:
                log("  ↩︎ %s→%s 回退成功（%s）" % (chain[0], m, meta["reason"] or "未知原因"))
        return content, meta
    if last is not None:
        raise RuntimeError(f"Agnes 对话接口失败：{last}")
    raise RuntimeError("Agnes 对话接口失败：未知错误")
