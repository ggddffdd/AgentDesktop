# -*- coding: utf-8 -*-
"""WebEngine 导航白名单与沙箱开关（v4.265.0，审查 P1-8）。

纯标准库，方便单测。Qt 页面只负责把 URL 交进来。

允许留在 WebEngine 里的导航（navigation_allowed）：
  · 空串 / about:blank / about:srcdoc
  · app://（聊天业务链接，页面层会拦截，不真正跳转）
  · localres://（导演台本地图/视频）
  · file:// 且落在工作区根之内
  · http(s) 且主机名在显式允许域里

其余（任意外站、javascript:、data:、blob:、工作区外的 file）一律拒绝。

v4.272.0：Qt 的 setHtml 不是 about:blank，而是主框架 data:text/html
导航（NavigationType 不是 LinkClicked）。这个特例不进 navigation_allowed
—— 放进去会把用户点击的 data: 链接一起放行。聊天页用
decide_chat_whitelist：先认 setHtml，其余仍走白名单。
关 Chromium 沙箱只在冻结版：开发版不写 QTWEBENGINE_DISABLE_SANDBOX。
"""
import os
from urllib.parse import unquote, urlparse

SANDBOX_ENV = "QTWEBENGINE_DISABLE_SANDBOX"


def apply_sandbox_env(environ, frozen):
    """仅冻结版写入沙箱关闭开关。用户已设置则尊重原值。

    返回本次是否写入。
    """
    if environ is None:
        return False
    if SANDBOX_ENV in environ:
        return False
    if frozen:
        environ[SANDBOX_ENV] = "1"
        return True
    return False


def navigation_allowed(url, workspace_roots=(), allow_hosts=()):
    """主框架导航能否留在 WebEngine 内。"""
    s = (url or "").strip()
    if not s:
        return True
    low = s.lower()
    if low in ("about:blank", "about:srcdoc"):
        return True
    if low.startswith("app://") or low.startswith("localres://"):
        return True
    if low.startswith(("javascript:", "data:", "blob:", "vbscript:")):
        return False
    if low.startswith("file:"):
        return _under_any(_file_url_to_path(s), workspace_roots)
    if low.startswith(("http://", "https://")):
        host = (urlparse(s).hostname or "").lower()
        allow = set()
        for h in allow_hosts or ():
            name = str(h or "").strip().lower()
            if name:
                allow.add(name)
        return bool(host) and host in allow
    return False


def allow_sethtml_navigation(url, is_main_frame, is_link_click):
    """Qt setHtml 的内部加载：主框架、非点击的 data:text/html。

    其余 data:（点击链接、子框架、svg/javascript）返回 False，
    交给 navigation_allowed 拒绝。is_link_click 必须是布尔：
    NavigationTypeLinkClicked 的值是 0，不能拿导航类型本身当布尔。
    """
    if not is_main_frame or is_link_click:
        return False
    low = (url or "").strip().lower()
    return low.startswith("data:text/html")


def decide_chat_whitelist(url, is_main_frame, is_link_click,
                          workspace_roots=(), allow_hosts=()):
    """聊天页在 app:// / 外链点击之后的主框架判定。

    setHtml 先放行；其余（含点击的 data:）仍走 navigation_allowed。
    """
    if allow_sethtml_navigation(url, is_main_frame, is_link_click):
        return True
    return navigation_allowed(
        url, workspace_roots=workspace_roots, allow_hosts=allow_hosts)


def workspace_roots():
    """工作区 / 产物 / 用户数据目录。读不到配置时返回空（file:// 全拒）。"""
    roots = []
    try:
        import config
        for p in (getattr(config, "WORKSPACE_DIR", ""),
                  getattr(config, "PRODUCTS_DIR", ""),
                  getattr(config, "USER_DATA_DIR", "")):
            if p and p not in roots:
                roots.append(p)
    except Exception:
        pass
    return roots


def allow_hosts():
    """显式允许域。只读已有 config.json，不调用 load_config（避免顺手建配置）。"""
    hosts = []
    try:
        import config
        default = (getattr(config, "DEFAULT_CONFIG", {}) or {}).get(
            "webengine_allow_hosts") or []
        if isinstance(default, str):
            default = [x.strip() for x in default.split(",") if x.strip()]
        hosts = list(default) if isinstance(default, (list, tuple)) else []
        path = getattr(config, "CONFIG_PATH", "") or ""
        if path and os.path.isfile(path):
            import json
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
            extra = {}
            if isinstance(cfg, dict):
                extra = cfg.get("webengine_allow_hosts", None)
            if isinstance(extra, str):
                hosts = [x.strip() for x in extra.split(",") if x.strip()]
            elif isinstance(extra, (list, tuple)):
                hosts = [str(x).strip() for x in extra if str(x).strip()]
    except Exception:
        pass
    return hosts


def _file_url_to_path(url):
    try:
        path = unquote(urlparse(url).path or "")
    except Exception:
        return ""
    # Windows 的 pathname2url 会给出 ///C:/...，再拼上 file:// 就变成
    # file://///C:/...。urlparse 留下的 path 带多层前导斜杠；只剥一层时
    # 盘符路径会被 normpath 当成 UNC（\\C:\...），对不上工作区根。
    drive = path.lstrip("/")
    if len(drive) >= 2 and drive[0].isalpha() and drive[1] == ":":
        path = drive
    return os.path.normpath(path)


def _under_any(path, roots):
    if not path:
        return False
    try:
        rp = os.path.realpath(path)
    except Exception:
        return False
    for root in roots or ():
        if not root:
            continue
        try:
            base = os.path.realpath(root)
        except Exception:
            continue
        if rp == base or rp.startswith(base + os.sep):
            return True
    return False
