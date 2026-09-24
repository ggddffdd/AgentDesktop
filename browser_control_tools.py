# -*- coding: utf-8 -*-
"""小臭玩AI — 浏览器控制工具（受控：对话触发 + 执行前确认 + 日志可见）

实际浏览器操作交给 browser_runner.py（Playwright），本模块通过 subprocess 调
系统 Python 执行，规避冻结 exe 打包 Playwright 的复杂度。所有工具返回
(result_str, deliverables, schedule)，永不抛异常。

注册方式：BROWSER_CONTROL_TOOL_DEFS 声明式 schema → config.py 聚合进 TOOL_DEFS
→ tools.py 的 exec_tool() 路由分发。
"""

import os
import sys
import json
import subprocess
import time
import urllib.request
import urllib.parse

# 系统 Python（运行 Playwright 的执行体）。顺序：配置 > 通用名兜底。
# 注：Windows 常见安装路径（如 %LOCALAPPDATA%\Programs\Python\Python3XX\python.exe）
# 由下方 _find_python() 动态探测，不在源码硬编码。
_DEFAULT_PY = [
    "python3",
    "python",
]

# v4.125 P2-10：playwright 可用性探测缓存（按解释器路径）
_PW_CACHE = {}


def _resource_path(name):
    """定位 browser_runner.py：开发态同目录，冻结态在 exe 所在目录或 _internal 下。

    重要：冻结态下本模块 __file__ 路径里的中文（小臭玩AI）可能被编码损坏（如变 СAI），
    因此**不能**依赖 __file__ 推断目录。优先用 sys.executable 所在目录——Windows 会返回
    正确的 Unicode 路径，绝不会乱码。
    """
    candidates = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exe_dir, name))
        candidates.append(os.path.join(exe_dir, "_internal", name))
        mp = getattr(sys, "_MEIPASS", None)
        if mp:
            candidates.append(os.path.join(mp, name))
            candidates.append(os.path.join(mp, "_internal", name))
    # 开发态：__file__ 同目录（源码树中文路径完好）
    base = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(base, name))
    candidates.append(os.path.join(base, "_internal", name))
    for c in candidates:
        if os.path.exists(c):
            return c
    # 全找不到：返回最可能是路径（exe 目录），让报错信息准确
    return candidates[0] if candidates else name


def _find_python(cfg):
    cand = (cfg or {}).get("browser_python")
    if cand and os.path.exists(cand):
        return cand
    # 动态探测 Windows 常见 Python 安装位置（不硬编码用户名）
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        import glob as _glob
        for pat in (
            os.path.join(local, "Programs", "Python", "Python3*", "python.exe"),
            os.path.join(local, "Programs", "Python", "Python3*", "scripts", "python.exe"),
        ):
            hits = sorted(_glob.glob(pat), reverse=True)
            if hits:
                return hits[0]
    for p in _DEFAULT_PY:
        if os.path.exists(p):
            return p
    return "python"


# ---------- CDP 自动拉起（v4.103：无需手动点快捷方式）----------
_EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _find_edge():
    for c in _EDGE_CANDIDATES:
        if os.path.isfile(c):
            return c
    return ""


# v4.147：自动拉起 Edge 时一并注入大哥的 VPN 扩展（无忧行），让被接管的浏览器带梯子。
# 扩展 ID 固定；版本目录动态解析——扩展自动更新后目录名（如 1.5.10_0）会变，不能硬编码。
_VPN_EXT_ID = "bkpoijbobhmbglhjjmnoedomdoabilol"


def _find_vpn_extension():
    """定位无忧行 VPN 扩展的 unpacked 目录（含 manifest 的版本子目录）。

    返回该版本目录完整路径；找不到返回 ""。解析 Edge 默认 profile 的 Extensions
    目录，取版本号最大的子目录（扩展更新会新增更高版本目录，旧的仍在）。
    该目录内的 manifest 带 `key` 字段，所以换到 CDP 专用 profile 后扩展 ID 不变、状态可延续。
    """
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return ""
    import glob as _glob
    base = os.path.join(local, "Microsoft", "Edge", "User Data",
                        "Default", "Extensions", _VPN_EXT_ID)
    if not os.path.isdir(base):
        return ""
    vers = [d for d in _glob.glob(os.path.join(base, "*")) if os.path.isdir(d)]
    if not vers:
        return ""
    # 版本目录形如 1.5.10_0，位数固定，按字符串排序取最大即最新版本
    vers.sort()
    return vers[-1]


def _cdp_ready(cdp_url, timeout=1.0):
    try:
        urllib.request.urlopen(cdp_url.rstrip("/") + "/json/version", timeout=timeout)
        return True
    except Exception:
        return False


def _cdp_profile_candidates():
    """调试浏览器可能用的 profile 目录（桌面 lnk 与 _ensure_cdp 用的那个 + 模块同级）。"""
    out = []
    lap = os.environ.get("LOCALAPPDATA")
    if lap:
        out.append(os.path.join(lap, "小臭玩AI", "cdp_edge_profile"))
    try:
        out.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "cdp_edge_profile"))
    except Exception:
        pass
    return out


def _vpn_ext_in_profile(profile_dir):
    """profile 里是否装了 VPN 扩展 —— 权威证据（Preferences 的扩展条目）。

    v4.148.5：不依赖 MV3 service worker 是否在跑。命令行的 --load-extension
    加载成功后，Edge 会把扩展写进 profile 的 extensions.settings，
    这条记录不随 SW 空闲回收而消失。
    """
    if not profile_dir or not _VPN_EXT_ID:
        return False
    for sub in ("Default", ""):
        for fname in ("Secure Preferences", "Preferences"):
            fp = (os.path.join(profile_dir, sub, fname) if sub
                  else os.path.join(profile_dir, fname))
            if not os.path.isfile(fp):
                continue
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    d = json.load(f)
            except Exception:
                continue
            exts = ((d.get("extensions") or {}).get("settings") or {})
            if _VPN_EXT_ID in exts:
                return True
    return False


def _cdp_has_vpn(cdp_url, timeout=2.0):
    """判断「已在运行的调试浏览器」里是否装了 VPN 扩展。**双证据判定**。

    ① `/json/list` 里出现 `chrome-extension://<VPN_ID>/worker.js`（扩展在活跃）；
    ② profile 的 Preferences/Secure Preferences 里有扩展条目（权威记录，不随 SW 休眠变化）。

    v4.148.5 修误报（大哥反馈「桌面 lnk 一直连着 VPN」却老被提示没梯子）：
    MV3 的 service worker **空闲约 1 分钟就被回收**，`/json/list` 里随之消失 ——
    只看①的话，浏览器开着超过一分钟就必然误报「未加载 VPN 扩展」。实测数据：
      t≈4s  → /json/list 有 worker.js → 判定 True
      t≈80s → /json/list 没了（SW 被回收）→ 旧判据 False（误报），新判据 True
    探测异常一律返回 True —— 宁可不打扰，也不因探测异常误报。
    """
    if not _VPN_EXT_ID:
        return True
    try:
        raw = urllib.request.urlopen(cdp_url.rstrip("/") + "/json/list",
                                     timeout=timeout).read()
        tabs = json.loads(raw.decode("utf-8"))
        if any(_VPN_EXT_ID in (t.get("url", "") or "") for t in tabs):
            return True
    except Exception:
        return True                     # 探测失败 → 不误报
    for prof in _cdp_profile_candidates():
        if _vpn_ext_in_profile(prof):
            return True
    return False


def _ensure_cdp(cdp, app_dir=None):
    """确保 CDP 调试端口可用；不可用则自动拉起带调试端口的 Edge。返回 (ok, msg)。

    关键：用**专属 profile 目录**启动 Edge（独立单例锁），永远不和用户真实 Edge 的
    默认 profile 抢锁，因此调试端口必定能起来，自动接管稳定可用。
    """
    if _cdp_ready(cdp):
        # v4.147.2：端口通 ≠ 带梯子。端口若被一个「没加载 VPN 扩展」的旧实例占着，
        # 原来会静默 return True —— 小臭接管到无梯子浏览器且零提示，境外抓取失败
        # 还会被误判成「被墙/反爬」。这里**不强行重启**用户的调试浏览器（它可能正在
        # 抓网页），只把实情作为 msg 回给调用方去提示。
        if _find_vpn_extension() and not _cdp_has_vpn(cdp):
            return True, ("调试浏览器里没找到 VPN 扩展（profile 里也没有加载记录）："
                          "本波访问境外站点会失败。建议彻底退出该 Edge，"
                          "再用桌面「VPN浏览器-9222调试端口」快捷方式重新拉起。")
        return True, ""
    edge = _find_edge()
    if not edge:
        return False, ("未找到 Edge 可执行文件，无法自动启动调试浏览器。"
                       "请确认已安装 Microsoft Edge。")
    port = urllib.parse.urlparse(cdp).port or 9222
    # 专属 profile：放在 app_dir 下，避免与默认 Edge 单例冲突导致调试端口起不来
    if app_dir:
        profile = os.path.join(app_dir, "cdp_edge_profile")
    else:
        profile = os.path.expandvars(r"%LOCALAPPDATA%/小臭玩AI/cdp_edge_profile")
    try:
        os.makedirs(profile, exist_ok=True)
    except Exception:
        profile = os.path.join(os.path.expanduser("~"), "cdp_edge_profile")
        os.makedirs(profile, exist_ok=True)
    args = [
        edge,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
    ]
    # v4.147：自动拉起时一并注入 VPN 扩展，使被接管浏览器带梯子（与桌面 lnk 行为对齐）。
    # 扩展加载 ≠ 已连上：启动后需大哥手动点扩展图标「连接」。
    #
    # ⚠️ 引号铁律（v4.147 实测坑）：args 是 list，交给 subprocess.Popen 拼接命令行，
    #    subprocess 会自动给「含空格」的参数加引号。此处**绝不能**像 .lnk 那样自带
    #    引号（.lnk 的 ARGS 是一整条原始命令行字符串，必须自己写引号）。若写成
    #    f'--load-extension="{vpn_ext}"'，会被转义成 \" 而变成字面引号，Edge 收到的
    #    值带引号→解析为相对路径→弹「无法加载该扩展…清单文件丢失或不可读取」，
    #    且调试端口都起不来。正确写法：不带引号，交给 subprocess 自动加。
    vpn_ext = _find_vpn_extension()
    if vpn_ext:
        args.append(f"--load-extension={vpn_ext}")
    try:
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, **kwargs,
        )
    except Exception as e:
        return False, f"自动启动 Edge 失败：{e}"
    for _ in range(60):  # 最多约 18 秒，Edge 冷启动较慢
        if _cdp_ready(cdp):
            return True, ""
        time.sleep(0.3)
    return False, ("自动启动 Edge 后调试端口仍未就绪。请稍后重试，或检查 Edge 是否被安全"
                   "软件拦截启动。小臭使用独立 profile 接管浏览器，不会干扰你的真实 Edge。")


def _pw_ok(py):
    """v4.125 P2-10：探测「跑 runner 的那个 Python」里有没有 playwright。

    原实现在主进程 import playwright——打包 EXE 后主进程是冻结环境，
    与子进程（系统 Python）环境不一致：主进程探测通过、子进程实际没有
    （或反过来），报错信息驴唇不对马嘴。改为直接探测子进程解释器，
    结果按解释器路径缓存（探测一次约几百毫秒，不必每次都跑）。
    """
    cache = _PW_CACHE.get(py)
    if cache is not None:
        return cache
    ok = False
    try:
        import subprocess as _sp
        _env = dict(os.environ)
        _env["PYTHONIOENCODING"] = "utf-8"
        r = _sp.run([py, "-c", "import playwright"], capture_output=True,
                    timeout=20, env=_env)
        ok = (r.returncode == 0)
    except Exception:
        ok = False
    _PW_CACHE[py] = ok
    return ok


def _run_runner(action, url, selector="", text="", cfg=None, headless="1", app_dir=None,
                links=False, links_limit=60, page_timeout=None, links_root=""):
    """调用 browser_runner.py，返回解析后的 JSON dict。

    links=True 时（v4.148.6）read 会额外带回页面外链，供浏览器搜索兜底还原真实 URL。
    links_root 限定抽链接的 CSS 容器（搜索兜底必用，避免捞进导航/登录/广告位）。
    page_timeout 可覆盖单页 goto 超时（毫秒）；搜索兜底用它压低单次耗时。
    """
    py = _find_python(cfg)
    # v4.125 P2-10：可用性检查改为探测子进程解释器（见 _pw_ok 注释）
    if not _pw_ok(py):
        return {"ok": False, "error": f"Playwright 未安装（已在 {py} 环境检查）。浏览器自动化需先运行：{py} -m pip install playwright && {py} -m playwright install chromium"}
    runner = _resource_path("browser_runner.py")
    cdp = (cfg or {}).get("browser_cdp", "") if cfg else ""
    # v4.125 P2-10：长文本走 stdin，避开 Windows 命令行 32767 字符上限
    use_stdin = bool(text) and len(text) > 800
    cmd = [
        py, runner,
        "--action", action,
        "--url", url or "",
        "--selector", selector or "",
        "--text", "" if use_stdin else (text or ""),
        "--headless", headless,
    ]
    if page_timeout:
        try:
            cmd += ["--timeout", str(int(page_timeout))]
        except Exception:
            pass
    if links:
        cmd += ["--links", "--links-limit", str(int(links_limit or 60))]
        if links_root:
            cmd += ["--links-root", links_root]
    if use_stdin:
        cmd.append("--text-stdin")
    warn = ""
    if cdp:
        ok, msg = _ensure_cdp(cdp, app_dir=app_dir)
        if not ok:
            return {"ok": False, "error": msg}
        # v4.147.2：端口可用但 VPN 未加载不算失败（浏览器确实能接管），
        # 仅把提示带出去，避免静默降级成无梯子却零提示。
        warn = msg or ""
        cmd += ["--cdp", cdp]
    try:
        # v4.108 H-09：显式注入 UTF-8 stdout，避免子进程按系统 GBK 编码输出中文
        # 导致父进程 UTF-8 解码乱码/丢字（errors="ignore" 只丢字符不报错，静默坏数据）。
        _run_env = dict(os.environ)
        _run_env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=90,
            encoding="utf-8", errors="ignore", env=_run_env,
            input=text if use_stdin else None,
        )
    except Exception as e:
        return {"ok": False, "error": f"启动浏览器执行器失败：{e}"}
    # 取 stdout 中最后一个 JSON 行
    out = None
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                out = json.loads(line)
            except Exception:
                out = None
            if out:
                break
    if out is None:
        err = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "无输出"
        res = {"ok": False, "error": f"浏览器执行器无结果：{err}"}
        # v4.147.2：无输出时更要带上 warn —— 没挂梯子时典型表现就是「连不上」，
        # 少了这句提示就会被误判成「被墙/反爬」。
        if warn:
            res["warn"] = warn
        return res
    if warn:
        # 透出给调用方：不破坏 ok 语义，旧下游不读该字段也不受影响。
        try:
            out["warn"] = warn
        except Exception:
            pass
        # 旁路落日志，保证有可追溯的审计痕（任何异常都吞，绝不拖垮主链路）
        try:
            if app_dir:
                log = os.path.join(app_dir, "log", "cdp_vpn_warn.log")
                os.makedirs(os.path.dirname(log), exist_ok=True)
                with open(log, "a", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  " + warn + "\n")
        except Exception:
            pass
    return out


def _deliver_shot(out):
    shot = out.get("screenshot", "")
    if shot and os.path.exists(shot):
        # 返回标准化 (rel, kind, name) 三元组；rel 用绝对路径，
        # _on_deliverable_open 的 os.path.join(APP_DIR, rel) 对绝对路径原样返回可正确打开。
        return [(shot, "image", os.path.basename(shot))]
    return []


def _with_warn(text, out):
    """v4.147.2：把浏览器接管的降级提示拼进工具返回文案，让对话里能直接看到。

    _run_runner 在「端口可用但 VPN 未加载」等场景会塞 warn 字段。工具层的成功返回
    文案默认不读它，提示就死在返回值里、用户永远看不到 —— 等于换个地方继续静默降级。
    这里统一挂到返回文案末尾；无 warn 时原样返回，不影响正常路径。
    """
    try:
        w = (out or {}).get("warn", "") or ""
    except Exception:
        w = ""
    return f"{text}\n\n⚠️ {w}" if w else text


# ==================== v4.148.6：浏览器搜索兜底 ====================
#
# 背景（2026-09-14 实测定论）：`search.py:http_get` 是小臭**自己的 Python 网络栈直连**
# （纯 urllib，零代理），**不吃浏览器 VPN**。所以「VPN 一直连着」与「境外搜索全空、
# 只回噪声」并不矛盾 —— 那部分流量压根没走梯子。唯一吃梯子的通道是
# browser_open / browser_read（subprocess 起 playwright，接管真实 Edge）。
#
# 于是：常规搜索链全空时，改用被接管的 Edge 打开**搜索引擎结果页**、从渲染后的
# DOM 抽链接还原真实 URL —— 让「搜索」这一环也吃上梯子。
#
# 为什么不让成员自己 browser_open 搜索页了事：实测四轮 40+ 次抓取，成员一次都没
# 主动这么做（prompt 里写了也没用）。工具层自动改道比「靠提示倒逼」可靠。

_DEFAULT_CDP = "http://127.0.0.1:9222"

# 结果页模板 + **结果容器选择器**（root 决定抽取范围，实测必须精准，见 _collect_links 注释）
#
# 引擎选择依据（2026-09-14 真机实测）：
#   · duckduckgo（html 版）→ 结果 href 是 `//duckduckgo.com/l/?uddg=<urlencoded>`，**可还原真实 URL**，
#                            实测 7.3s 拿到 inseller.my 费率详解 / seller-my.tiktok.com 官方政策页等真源；
#   · google              → 结果 href 已变成 `google.com.hk/goto?url=CAES…`（加密 protobuf，**解不出**），
#                            依赖 _is_engine_redirect 保留原链，靠 browser_read 打开时自行跳转；
#   · bing                → 结果 href 加密成 `…/ck/a?…&p=<hash>`（**同样解不出**），同 google 处理；
#                            且该查询下 Bing 命中的是 tiktok.com 官网导航页，结果质量明显更差。
_SEARCH_PAGE = {
    "duckduckgo": {"url": "https://html.duckduckgo.com/html/?q={q}", "root": "#links"},
    "google": {"url": "https://www.google.com/search?hl={hl}&num={k}&q={q}", "root": "#rso"},
    "bing": {"url": "https://www.bing.com/search?setlang={hl}&count={k}&q={q}", "root": "#b_results"},
}

# 结果页里属于「引擎自身 / 站点基建」的域 —— 导航、登录、静态资源、统计，都不是结果
_ENGINE_SELF_HOSTS = (
    "google.", "gstatic.", "googleusercontent.", "googleapis.", "ggpht.",
    "duckduckgo.com", "bing.com", "microsoft.com", "msn.com", "microsofttranslator.",
    "baidu.com", "bdstatic.", "baiducontent.", "bdimg.", "hao123",
    "w3.org", "schema.org", "mozilla.org", "creativecommons.org",
    "doubleclick", "googletagmanager", "googleadservices", "cloudflare.com",
)

# 纯图片墙：成员 browser_read 读不到任何文字，纯占位。
# ⚠️ 刻意**不**过滤 reddit / facebook / twitter / 官方社媒 —— 品牌与平台的官方页
# 常是政策公告的一手来源（如卖家群组公告），误杀比漏过更贵。让成员自己判断。
_RESULT_NOISE_HOSTS = (
    "pinterest.",
)


def _unwrap_result_url(href):
    """把结果页里的跳转链还原成真实 URL。

    DDG.h  → `//duckduckgo.com/l/?uddg=<urlencoded>`
    Google → `/url?q=<url>`
    Bing   → `/ck/a?…&u=a1<urlsafe-base64>`
    百度   → `/link?url=…`（需二次请求才能解，直接放弃，返回空让调用方跳过）
    其它   → 原样返回（结果页给的就是真实 URL）
    """
    h = (href or "").strip()
    if not h:
        return ""
    if h.startswith("//"):
        h = "https:" + h
    try:
        u = urllib.parse.urlparse(h)
        host = (u.netloc or "").lower()
        qs = urllib.parse.parse_qs(u.query or "")
        if "duckduckgo.com" in host and qs.get("uddg"):
            return urllib.parse.unquote(qs["uddg"][0])
        if ".google." in host and u.path.startswith("/url") and qs.get("q"):
            return qs["q"][0]
        if "bing.com" in host and u.path.startswith("/ck/") and qs.get("u"):
            raw = qs["u"][0]
            if raw.startswith("a1"):
                import base64
                s = raw[2:]
                s += "=" * (-len(s) % 4)
                try:
                    return base64.urlsafe_b64decode(s).decode("utf-8", "ignore")
                except Exception:
                    return ""
            return ""
        if "baidu.com" in host and u.path.startswith("/link"):
            return ""      # 跳转链需再发一次请求才拿到真身，不值当
    except Exception:
        pass
    return h


def _is_engine_redirect(url):
    """该 URL 是不是「搜索引擎的加密跳转链」（Google `/goto?url=`、Bing `/ck/a?p=`）。

    2026-09-14 实测：Google/Bing 已把结果链接加密，客户端**解不出**真实地址
    （Google 的 `url=CAES…` 是加密 protobuf，Bing 的 `p=` 是 hash）。

    ⚠️ **修正（同日第二次实测，推翻了先前假设）**：先前以为「这种链在浏览器里打开会自行 302，
    对 browser_read 可用」—— **实测为假**！用浏览器打开 `google.com.hk/goto?url=CAES…` 得到的是
    `400. That's an error / Your client has issued a malformed or illegal request`（97 字垃圾页）——
    这类加密链**与原始会话/时间绑定**，脱离结果页上下文即失效。
    故：**不能当作可用结果**。处理策略见 `_parse_search_links` / `browser_search`：
    优先返回可还原链接，全为加密链时**继续试下一个引擎**（DDG 的结果是可还原的），
    实在只能返回加密链时**必须显式标注「链接不可直接读取，仅作标题线索」**，绝不假装可用。
    """
    try:
        u = urllib.parse.urlparse(url)
        host = (u.netloc or "").lower()
        if not any(b in host for b in _ENGINE_SELF_HOSTS):
            return False
        return any((u.path or "").startswith(x)
                   for x in ("/ck/", "/goto", "/url", "/link", "/aclick"))
    except Exception:
        return False


def _parse_search_links(links, top_k=5):
    """从结果页的 a[href] 列表里筛出真正的搜索结果条目。

    同一结果在结果页里常有多个 `<a>` 指向它（标题 / 域名 / 摘要各一条，DDG 尤甚），
    故**按 URL 分组**：首条锚文本当标题，其余里最长的当摘要 —— 这样既能天然去重，
    又能白捡回摘要（实测 DDG 的摘要是完整句子，对成员判断相关性很有用）。
    """
    groups = {}          # url -> {"texts":[...], "redirect":bool}
    for it in (links or []):
        if not isinstance(it, dict):
            continue
        if not it.get("v", 1):            # 不可见元素（模板/隐藏块）跳过
            continue
        title = (it.get("t") or "").strip()
        url = _unwrap_result_url(it.get("h") or "")
        if not url.lower().startswith("http"):
            continue
        try:
            host = (urllib.parse.urlparse(url).netloc or "").lower()
        except Exception:
            continue
        if not host:
            continue
        redirect = False
        if any(b in host for b in _ENGINE_SELF_HOSTS):
            if _is_engine_redirect(url):
                redirect = True       # 加密跳转链：浏览器可跟随，保留
            else:
                continue              # 引擎自身的导航/登录/静态资源
        if not redirect and any(b in host for b in _RESULT_NOISE_HOSTS):
            continue
        g = groups.get(url)
        if g is None:
            groups[url] = {"texts": [title] if title else [], "redirect": redirect}
        elif title:
            g["texts"].append(title)

    out = []
    for url, g in groups.items():
        texts = g["texts"]
        if not texts:
            continue
        title = texts[0]
        # 结果标题通常较长；过短的锚文本基本是导航/按钮（「更多」「登录」）
        if len(title) < 8 and not any(len(t) >= 8 for t in texts):
            continue
        snippet = ""
        if len(texts) > 1:
            cand = max(texts[1:], key=len)
            if len(cand) > len(title) and len(cand) >= 30:
                snippet = cand[:240]
        out.append({"title": title[:120], "url": url,
                    "snippet": snippet, "redirect": g["redirect"]})
        if len(out) >= max(1, int(top_k)):
            break
    return out


def browser_search(cfg, query, app_dir=None, top_k=5, engines=None, cdp=None):
    """用被接管的真实浏览器（走 VPN / 带登录态）做一次搜索。

    返回 (results, note)：
      results —— [{"title","url","snippet"}]，snippet 通常为空：结果页的摘要在
                 纯文本里对不上号，成员拿到 URL 后用 browser_read 读正文即可。
      note    —— 人话说明（走了哪个引擎、是否浏览器通道、失败原因），供工具层拼进返回文案。

    永不抛异常；浏览器通道不可用时返回 ([], 原因)，调用方回落原逻辑。
    """
    if not query:
        return [], "空查询"
    try:
        cdp = cdp or ((cfg or {}).get("browser_cdp") or "") or _DEFAULT_CDP
    except Exception:
        cdp = _DEFAULT_CDP
    ok, msg = _ensure_cdp(cdp, app_dir=app_dir)
    if not ok:
        return [], f"浏览器通道不可用（{msg}）"
    warn = msg or ""
    # 强制走 CDP：即使 cfg 里没配 browser_cdp，兜底也必须用真实浏览器（才有 VPN）
    cfg2 = dict(cfg or {})
    cfg2["browser_cdp"] = cdp

    try:
        hl = "en" if all(ord(c) < 128 for c in (query or "")) else "zh-CN"
    except Exception:
        hl = "zh-CN"
    if not engines:
        # DDG html 版第一：结果 href 是可解码的 `uddg=`，能还原真实 URL；
        # Google/Bing 的结果链接已加密成 goto?url= / ck/a?p=，解不出且 browser_read 打开会 400，
        # 只作标题线索 —— 故排在后面（见 _is_engine_redirect 的实测修正）。
        engines = ["duckduckgo", "google", "bing"]

    notes = []
    _enc_fallback, _enc_engine = [], ""     # 全是加密链时的兜底候选（最后才用，且必须显式标注）
    for engine in engines:
        spec = _SEARCH_PAGE.get(engine)
        if not spec:
            continue
        url = spec["url"].format(q=urllib.parse.quote_plus(query), k=max(1, int(top_k)), hl=hl)
        out = _run_runner("read", url, cfg=cfg2, app_dir=app_dir,
                          links=True, links_limit=90, page_timeout=15000,
                          links_root=spec.get("root", ""))
        if not out.get("ok"):
            notes.append(f"{engine}：{out.get('error', '打开失败')}")
            continue
        if spec.get("root") and out.get("links_root_found") is False:
            notes.append(f"{engine}：结果容器 {spec['root']} 未出现（可能被反爬/需登录）")
            continue
        res = _parse_search_links(out.get("links"), top_k)
        if res:
            readable = [r for r in res if not r.get("redirect")]
            enc = [r for r in res if r.get("redirect")]
            if readable:
                out_res = readable[:top_k]
                note = f"经浏览器通道（{engine} 结果页 · 真实浏览器 / 走 VPN）"
                if enc and len(out_res) < top_k:
                    out_res = out_res + enc[:1]
                    note += "；另附 1 条仅作标题线索（其链接不可直接读取）"
                if warn:
                    note += f"；⚠️ {warn}"
                return out_res, note
            # 只有加密链：**不当可用结果**，先试下一个引擎（DDG 的结果是可还原的）
            if not _enc_fallback:
                _enc_fallback, _enc_engine = enc, engine
            notes.append(f"{engine}：{len(enc)} 条均为加密跳转链"
                         f"（实测 browser_read 打开返回 400），改试其它引擎")
            continue
        notes.append(f"{engine}：页面已打开但未解析出可用结果链接"
                     f"（候选 {len(out.get('links') or [])} 个）")
    if _enc_fallback:
        return _enc_fallback, (
            f"经浏览器通道（{_enc_engine} 结果页 · 真实浏览器 / 走 VPN）；"
            f"⚠️ 本组 {len(_enc_fallback)} 条**全是搜索引擎加密链、链接不可直接读取**（打开会 400），"
            f"只能当标题线索用：请挑标题里的关键词再搜一次，或直接访问标题中出现的站点")
    return [], "；".join(notes) or "浏览器搜索无结果"


# ---------- 工具实现（统一签名 cfg, app_dir, args）----------

def tool_browser_open(cfg, app_dir, args):
    url = args.get("url", "")
    if not url:
        return ("失败：browser_open 需要 url", [], None)
    out = _run_runner("open", url, cfg=cfg, app_dir=app_dir)
    if not out.get("ok"):
        # v4.147.2：失败分支同样要挂 warn —— 没梯子时抓取往往直接失败，
        # 而恰恰是这种失败最需要「不是被墙，是没挂梯子」这条提示。
        return (
            _with_warn(f"失败：{out.get('error', '未知错误')}", out),
            [],
            None,
        )
    return (
        _with_warn(
            f"已打开网页：{out.get('title', '')}（{out.get('url', '')}），截图见下方",
            out,
        ),
        _deliver_shot(out),
        None,
    )


def tool_browser_click(cfg, app_dir, args):
    url = args.get("url", "")
    sel = args.get("selector", "")
    if not url or not sel:
        return ("失败：browser_click 需要 url 和 selector", [], None)
    out = _run_runner("click", url, selector=sel, cfg=cfg, app_dir=app_dir)
    if not out.get("ok"):
        # v4.147.2：失败分支同样要挂 warn —— 没梯子时抓取往往直接失败，
        # 而恰恰是这种失败最需要「不是被墙，是没挂梯子」这条提示。
        return (
            _with_warn(f"失败：{out.get('error', '未知错误')}", out),
            [],
            None,
        )
    return (
        _with_warn(f"已点击元素并截图：{out.get('url', '')}", out),
        _deliver_shot(out),
        None,
    )


def tool_browser_fill(cfg, app_dir, args):
    url = args.get("url", "")
    sel = args.get("selector", "")
    text = args.get("text", "")
    if not url or not sel or text is None:
        return ("失败：browser_fill 需要 url、selector、text", [], None)
    out = _run_runner("fill", url, selector=sel, text=text, cfg=cfg, app_dir=app_dir)
    if not out.get("ok"):
        # v4.147.2：失败分支同样要挂 warn —— 没梯子时抓取往往直接失败，
        # 而恰恰是这种失败最需要「不是被墙，是没挂梯子」这条提示。
        return (
            _with_warn(f"失败：{out.get('error', '未知错误')}", out),
            [],
            None,
        )
    return (
        _with_warn(f"已填写表单并截图：{out.get('url', '')}", out),
        _deliver_shot(out),
        None,
    )


def tool_browser_read(cfg, app_dir, args):
    url = args.get("url", "")
    sel = args.get("selector", "")
    if not url:
        return ("失败：browser_read 需要 url", [], None)
    out = _run_runner("read", url, selector=sel, cfg=cfg, app_dir=app_dir)
    if not out.get("ok"):
        # v4.147.2：失败分支同样要挂 warn —— 没梯子时抓取往往直接失败，
        # 而恰恰是这种失败最需要「不是被墙，是没挂梯子」这条提示。
        return (
            _with_warn(f"失败：{out.get('error', '未知错误')}", out),
            [],
            None,
        )
    txt = out.get("text", "")
    preview = txt[:1500] + ("…" if len(txt) > 1500 else "")
    return (
        _with_warn(f"已读取网页文本（{len(txt)} 字）：\n{preview}", out),
        [],
        None,
    )


# ---------- 声明式 schema（OpenAI function-calling 格式）----------

BROWSER_CONTROL_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "browser_open",
            "description": "打开网页并截图。若已配置 browser_cdp（如用户以 --remote-debugging-port=9222 启动的真实 Edge 且端口已就绪），则接管该浏览器当前页面（带登录态）；否则自动拉起一个独立 profile 的 Edge，不干扰真实 Edge。用于查看网页、留档截图。**操作网页内容请用本系列工具（browser_open/click/fill/read）；不要用 app_* 工具——app_* 针对原生桌面程序（如 Excel），读不到浏览器网页内容。多步操作（填表/发布）请先 browser_open 打开页面，再 browser_fill/browser_click 直接操作当前页面（接管模式下不会重新刷新页面、不会清空已填内容）。**",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要打开的网页地址，如 https://www.baidu.com"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "点击网页指定元素并返回点击后截图。selector 支持 css=、text=、xpath= 前缀；无前缀时含 . # [ > 等按 CSS 处理，否则按可见文本。接管浏览器（CDP）模式下直接点击当前已打开页面的元素，不要重复 browser_open（重复打开会刷新清空已填内容）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页地址"},
                    "selector": {"type": "string", "description": "要点击的元素，如 text=登录 或 css=#btn"},
                },
                "required": ["url", "selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_fill",
            "description": "在指定输入框/编辑器填入文本并返回截图，用于自动填表、发布文章。selector 同 browser_click（支持 css=/text=/xpath= 前缀，无前缀智能判断）。已兼容知乎等 contenteditable 富文本编辑器（标题/正文均为 contenteditable）。接管浏览器（CDP）模式下直接填当前页面，不要重复 browser_open（重复打开会刷新清空已填内容）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页地址"},
                    "selector": {"type": "string", "description": "输入框选择器，如 css=#username"},
                    "text": {"type": "string", "description": "要填入的文本"},
                },
                "required": ["url", "selector", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_read",
            "description": "提取网页文本（或指定元素文本）返回，用于抓取网页文字内容做分析。selector 同 browser_click（支持 css=/text=/xpath= 前缀，无前缀智能判断）。接管浏览器（CDP）模式下直接读取当前页面，不要重复 browser_open。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页地址"},
                    "selector": {"type": "string", "description": "可选，只提取该元素的文本；留空则提取整页文本"},
                },
                "required": ["url"],
            },
        },
    },
]

BROWSER_CONTROL_TOOL_TABLE = {
    "browser_open": tool_browser_open,
    "browser_click": tool_browser_click,
    "browser_fill": tool_browser_fill,
    "browser_read": tool_browser_read,
}
