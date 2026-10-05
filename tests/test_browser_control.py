# -*- coding: utf-8 -*-
"""v4.213.1 判据：browser 链（browser_runner + browser_control_tools）零判据补齐（G5 收尾）。

被测对象：browser_runner.py（304 行 Playwright 子进程执行器）+
browser_control_tools.py（856 行参数组装 / CDP 管理 / JSON 解析 / 工具入口）。
此前这两块**一个判据都没有**（G5 判据倒挂的最后一角）—— 以下血泪修复改坏了无人知：

  v4.105      fill 兼容 contenteditable 富文本（知乎发布靠它）
  v4.108 H-07 整页 read 必须 inner_text("body")（改回无参直接 TypeError）
  v4.108 H-09 父进程注入 + 子进程出口双 UTF-8（改掉 = 中文静默丢字）
  v4.125 P2-10 长文本 >800 走 stdin（改阈值 = 命令行 32767 上限截断）
  v4.147.9    CDP read 的 url-not-in-page.url 导航（改掉 = read 永远读到当前页，
              军团 40 次抓取全废的根因）
  v4.147.2    warn 透传（改掉 = 「VPN 没挂」这类降级提示死在返回值里）
  risk 契约   browser_click/fill 钉死 EXEC（手动确认），open/read 钉死 READ
              —— 外部审核 P1-4 的核心担忧就是权限静默降级

**零副作用第一原则**：不启动真浏览器、不跑真子进程 —— subprocess/_pw_ok/
_resource_path/_find_python/_ensure_cdp 全部换桩；config 换假模块指向 %TEMP%。

变异注入（扰动用）：BR_PATH / BCT_PATH / RISK_PATH 指向变异副本。
用法：python tests/test_browser_control.py
"""
import ast
import importlib.util
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        print(f"  [PASS] {name}")
        _p += 1
    else:
        print(f"  [FAIL] {name}  {detail}")
        _f += 1


# ---------------------------------------------------------------------------
# 被测源：默认真源码；扰动时用 *_PATH 指向变异副本
# ---------------------------------------------------------------------------
BR_PATH = os.environ.get("BR_PATH") or os.path.join(ROOT, "browser_runner.py")
BCT_PATH = os.environ.get("BCT_PATH") or os.path.join(ROOT, "browser_control_tools.py")
RISK_PATH = os.environ.get("RISK_PATH") or os.path.join(ROOT, "risk.py")

_mod_seq = [0]


def load_mod(path):
    """按路径加载模块（每副本唯一模块名，互不污染）。"""
    _mod_seq[0] += 1
    name = "brj_mut_%d" % _mod_seq[0]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def read_src(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


# ===========================================================================
# A 组：browser_runner 纯函数
# ===========================================================================
print("== A 组：runner 纯函数 ==")
BR = load_mod(BR_PATH)

check("A1  _looks_like_css 分类（CSS 符号/HTML 标签/纯文本/空）",
      BR._looks_like_css(".titleInput") and BR._looks_like_css("#main")
      and BR._looks_like_css("div.card") and BR._looks_like_css("button")
      and BR._looks_like_css("h1")
      and not BR._looks_like_css("登录") and not BR._looks_like_css("")
      and not BR._looks_like_css("   "))


class _LocA:
    def __init__(self, tag):
        self.tag = tag

    @property
    def first(self):
        return self


class _PageA:
    def __init__(self):
        self.css, self.texts = [], []

    def locator(self, sel):
        self.css.append(sel)
        return _LocA("loc:" + sel)

    def get_by_text(self, sel):
        self.texts.append(sel)
        return _LocA("text:" + sel)


_pg = _PageA()
_c1, _k1 = BR._locate(_pg, "css=.btn")
_c2, _k2 = BR._locate(_pg, "xpath=//div")
_c3, _k3 = BR._locate(_pg, "text=登录")
_c4, _k4 = BR._locate(_pg, ".btn")
_c5, _k5 = BR._locate(_pg, "登录按钮")
check("A2  _locate 四路分发（css=/xpath=/text= 前缀 + 无前缀智能判断）",
      (_k1 == "css" and _c1.tag == "loc:.btn")
      and (_k2 == "xpath" and _c2.tag == "loc:xpath=//div")
      and (_k3 == "text" and _c3.tag == "text:登录")
      and (_k4 == "css" and _c4.tag == "loc:.btn")
      and (_k5 == "text" and _c5.tag == "text:登录按钮"),
      f"got {(_k1,_c1.tag)},{(_k2,_c2.tag)},{(_k3,_c3.tag)},{(_k4,_c4.tag)},{(_k5,_c5.tag)}")


class _PageEval:
    def __init__(self, ret=None, raise_exc=False):
        self.ret, self.raise_exc = ret, raise_exc
        self.calls = []

    def evaluate(self, js, *a):
        self.calls.append(js)
        if self.raise_exc:
            raise RuntimeError("page gone")
        return self.ret


_payload = {"links": [{"t": "x", "h": "https://a", "v": 1}], "rootFound": False}
_r1 = BR._collect_links(_PageEval(_payload))
_r2 = BR._collect_links(_PageEval("garbage-not-dict"))
_r3 = BR._collect_links(_PageEval(None, raise_exc=True))
check("A3  _collect_links 三分支（透传 / 非 dict 兜底 rootFound / 异常兜底空）",
      _r1 == _payload
      and _r2 == {"links": [], "rootFound": True}
      and _r3 == {"links": [], "rootFound": True},
      f"got {_r1!r} / {_r2!r} / {_r3!r}")

_sp1 = BR._shot_path("open")
check("A4  _shot_path 落 %TEMP% 且带动作前缀",
      _sp1.startswith(os.path.join(tempfile.gettempdir(), "xc_browser_open_"))
      and _sp1.endswith(".png"), _sp1)


# ===========================================================================
# B 组：_fill_el 富文本兼容（v4.105，知乎发布靠它）
# ===========================================================================
print("== B 组：fill 的 contenteditable 兼容 ==")


class _Kb:
    def __init__(self):
        self.calls = []

    def press(self, k):
        self.calls.append("kb:%s" % k)

    def insert_text(self, t):
        self.calls.append("ins:%s" % t)


class _PageB:
    def __init__(self):
        self.keyboard = _Kb()


class _LocB:
    def __init__(self, editable):
        self.editable = editable  # True / False / "raise"
        self.calls = []

    def evaluate(self, js):
        if self.editable == "raise":
            raise RuntimeError("detached")
        return self.editable

    def click(self):
        self.calls.append("click")

    def press(self, k):
        self.calls.append("press:%s" % k)

    def fill(self, t):
        self.calls.append("fill:%s" % t)


def _fill_case(editable):
    loc, pg = _LocB(editable), _PageB()
    BR._fill_el(pg, loc, "你好")
    return loc, pg


loc, pg = _fill_case(True)
check("B1  富文本分支：click 聚焦 + Ctrl+a/Delete 清空 + insert_text（不走 fill）",
      loc.calls == ["click", "press:Control+a"]
      and pg.keyboard.calls == ["kb:Delete", "ins:你好"]
      and not any(c.startswith("fill:") for c in loc.calls),
      f"loc={loc.calls} kb={pg.keyboard.calls}")

loc, pg = _fill_case(False)
check("B2  普通分支：直接 fill（不点不清空）",
      loc.calls == ["fill:你好"] and pg.keyboard.calls == [],
      f"loc={loc.calls} kb={pg.keyboard.calls}")

loc, pg = _fill_case("raise")
check("B3  evaluate 异常降级为普通 fill（fail-safe 到可路径）",
      loc.calls == ["fill:你好"], f"loc={loc.calls}")


# ===========================================================================
# C/D/E 组：browser_control_tools 行为（全桩，零真子进程）
# ===========================================================================
class FakeSP:
    def __init__(self, stdout='{"ok": true}', stderr="", returncode=0):
        self.calls = []
        self.result = types.SimpleNamespace(stdout=stdout, stderr=stderr,
                                            returncode=returncode)

    def run(self, cmd, **kw):
        self.calls.append({"cmd": list(cmd), "kw": kw})
        return self.result


def fresh_bct(sp=None, pw_ok=True, ensure_cdp=None, workspace=None):
    """加载 BCT 副本并打桩。返回 (module, FakeSP)。"""
    bct = load_mod(BCT_PATH)
    sp = sp or FakeSP()
    bct.subprocess = types.SimpleNamespace(run=sp.run)
    bct._pw_ok = lambda py: pw_ok
    bct._resource_path = lambda name: "FAKE_" + name
    bct._find_python = lambda cfg: "FAKE_PY"
    if ensure_cdp is not None:
        bct._ensure_cdp = lambda c, app_dir=None: ensure_cdp
    if workspace is not None:
        sys.modules["config"] = types.SimpleNamespace(WORKSPACE_DIR=workspace)
    return bct, sp


def arg_of(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


print("== C 组：_run_runner 参数组装（桩 subprocess 验证 cmd 列表）==")

bct, sp = fresh_bct()
out = bct._run_runner("read", "https://a", text="短", cfg={})
_cmd = sp.calls[0]["cmd"] if sp.calls else []
check("C1  短文本走 --text 参数（无 --text-stdin）",
      out.get("ok") is True and arg_of(_cmd, "--text") == "短"
      and "--text-stdin" not in _cmd and sp.calls[0]["kw"].get("input") is None,
      f"cmd={_cmd}")

_long = "x" * 900
bct, sp = fresh_bct()
out = bct._run_runner("fill", "https://a", selector="#t", text=_long, cfg={})
_cmd = sp.calls[0]["cmd"] if sp.calls else []
check("C2  长文本(>800)走 stdin（--text 置空 + --text-stdin + input 传文）",
      arg_of(_cmd, "--text") == "" and "--text-stdin" in _cmd
      and sp.calls[0]["kw"].get("input") == _long,
      f"text={arg_of(_cmd, '--text')!r} stdin={'--text-stdin' in _cmd}")

bct, sp = fresh_bct()
bct._run_runner("read", "https://a", cfg={}, links=True, links_limit=30,
                links_root="#b_results")
_cmd = sp.calls[0]["cmd"]
check("C3  links 参数组装（--links / --links-limit / --links-root）",
      "--links" in _cmd and arg_of(_cmd, "--links-limit") == "30"
      and arg_of(_cmd, "--links-root") == "#b_results", f"cmd={_cmd}")

bct, sp = fresh_bct()
bct._run_runner("read", "https://a", cfg={}, page_timeout=5000)
_cmd = sp.calls[0]["cmd"]
check("C4  page_timeout → --timeout 5000", arg_of(_cmd, "--timeout") == "5000",
      f"cmd={_cmd}")

bct, sp = fresh_bct()
bct._run_runner("read", "https://a", cfg={})
_env = sp.calls[0]["kw"].get("env") or {}
check("C5  子进程 env 注入 PYTHONIOENCODING=utf-8（H-09 父进程侧）",
      _env.get("PYTHONIOENCODING") == "utf-8", f"env={_env.get('PYTHONIOENCODING')}")

bct, sp = fresh_bct(ensure_cdp=(True, ""))
out = bct._run_runner("read", "https://a", cfg={"browser_cdp": "http://127.0.0.1:9222"})
_cmd = sp.calls[0]["cmd"] if sp.calls else []
check("C6  cfg 带 browser_cdp → cmd 追加 --cdp（CDP 接管路径）",
      arg_of(_cmd, "--cdp") == "http://127.0.0.1:9222" and out.get("ok") is True,
      f"cmd={_cmd}")

bct, sp = fresh_bct(pw_ok=False)
out = bct._run_runner("open", "https://a", cfg={})
check("C7  playwright 缺失 → 安装提示错误 + 零子进程",
      out.get("ok") is False and "playwright" in (out.get("error") or "")
      and not sp.calls, f"out={out} calls={len(sp.calls)}")

print("== D 组：JSON 尾行解析容错 + warn 透传 ==")

bct, sp = fresh_bct(sp=FakeSP(stdout='{"ok": false}\n[noise] whatever\n{"ok": true, "action": "read"}'))
out = bct._run_runner("read", "https://a", cfg={})
check("D1  多行 stdout 取最后一个 JSON 行（含 ok:false 的旧行不误导）",
      out.get("ok") is True and out.get("action") == "read", f"out={out}")

bct, sp = fresh_bct(sp=FakeSP(stdout="", stderr="Traceback ...\nPlaywrightError: boom"))
out = bct._run_runner("read", "https://a", cfg={})
check("D2  无 JSON 输出 → ok False + stderr 尾行进错误信息",
      out.get("ok") is False and "boom" in (out.get("error") or ""), f"out={out}")

_ws = tempfile.mkdtemp(prefix="brj_ws_")
try:
    bct, sp = fresh_bct(sp=FakeSP(stdout='{"ok": true, "url": "U", "title": "T"}'),
                        ensure_cdp=(True, "VPN 未加载提示"), workspace=_ws)
    out = bct._run_runner("open", "https://a", cfg={"browser_cdp": "http://127.0.0.1:9222"},
                          app_dir=_ws)
    _logf = os.path.join(_ws, "log", "cdp_vpn_warn.log")
    check("D3  warn 透传进 out（不破坏 ok）+ 旁路审计日志落盘",
          out.get("ok") is True and out.get("warn") == "VPN 未加载提示"
          and os.path.exists(_logf),
          f"out={out} log={os.path.exists(_logf)}")
finally:
    sys.modules.pop("config", None)
    import shutil
    shutil.rmtree(_ws, ignore_errors=True)

bct, sp = fresh_bct(sp=FakeSP(stdout='{bad json line\n{"ok": true}'))
out = bct._run_runner("read", "https://a", cfg={})
check("D4  无效 JSON 行跳过（解析失败不吞掉后面的真 JSON）",
      out.get("ok") is True, f"out={out}")

print("== E 组：tool_* 入口契约（恒三元组、缺参拒绝、失败挂 warn）==")

bct, _ = fresh_bct()


def _shape(r):
    return (isinstance(r, tuple) and len(r) == 3 and isinstance(r[0], str)
            and isinstance(r[1], list) and r[2] is None)


r = bct.tool_browser_open({}, "d", {})
check("E1  browser_open 缺 url → 失败 + 恒三元组 (str, list, None)",
      r[0].startswith("失败") and _shape(r), f"r={r}")

r = bct.tool_browser_click({}, "d", {"url": "u"})
check("E2  browser_click 缺 selector → 失败", r[0].startswith("失败") and _shape(r), f"r={r}")

r = bct.tool_browser_fill({}, "d", {"url": "u", "selector": "s", "text": None})
check("E3  browser_fill text=None → 失败（显式 None 是守卫唯一抓的形态）",
      r[0].startswith("失败") and _shape(r), f"r={r}")

r = bct.tool_browser_read({}, "d", {})
check("E4  browser_read 缺 url → 失败", r[0].startswith("失败") and _shape(r), f"r={r}")

bct, _ = fresh_bct(sp=FakeSP(stdout='{"ok": true, "url": "U", "title": "T", "warn": "VPN 未加载提示"}'))
r = bct.tool_browser_open({}, "d", {"url": "https://a"})
check("E5  成功返回文案挂 ⚠️ warn（v4.147.2 不静默降级）",
      "已打开网页" in r[0] and "⚠️ VPN 未加载提示" in r[0], f"r0={r[0][:120]}")

check("E6  _with_warn 无 warn 原样返回（None/空串/空 dict 三态）",
      bct._with_warn("abc", None) == "abc"
      and bct._with_warn("abc", {"warn": ""}) == "abc"
      and bct._with_warn("abc", {}) == "abc")

_fd, _shot = tempfile.mkstemp(prefix="brj_shot_", suffix=".png")
os.close(_fd)
try:
    d1 = bct._deliver_shot({"screenshot": _shot})
    d2 = bct._deliver_shot({"screenshot": "Z:/nope/x.png"})
    d3 = bct._deliver_shot({})
    check("E7  _deliver_shot 按文件存在性决定交付（rel, image, basename）",
          d1 == [(_shot, "image", os.path.basename(_shot))] and d2 == [] and d3 == [],
          f"d1={d1} d2={d2} d3={d3}")
finally:
    os.remove(_shot)


# ===========================================================================
# F 组：risk 契约（外部审核 P1-4 的核心担忧 = 权限静默降级）
# ===========================================================================
print("== F 组：risk 登记契约 ==")
RK = load_mod(RISK_PATH)
_RC, _RM = RK.RiskClass, RK.RISK_MAP

check("F1  browser_click/fill 钉死 EXEC（手动确认，降级必红）",
      _RM.get("browser_click") == _RC.EXEC and _RM.get("browser_fill") == _RC.EXEC,
      f"click={_RM.get('browser_click')} fill={_RM.get('browser_fill')}")

check("F2  browser_open/read 钉死 READ（免确认只限被动只读）",
      _RM.get("browser_open") == _RC.READ and _RM.get("browser_read") == _RC.READ,
      f"open={_RM.get('browser_open')} read={_RM.get('browser_read')}")

_declared = [d["function"]["name"] for d in load_mod(BCT_PATH).BROWSER_CONTROL_TOOL_DEFS]
check("F3  声明的 browser 工具全部已登记 RISK_MAP（新增忘登记必红）",
      _declared and all(n in _RM for n in _declared),
      f"declared={_declared} missing={[n for n in _declared if n not in _RM]}")


# ===========================================================================
# G 组：源码契约（血泪修复锚点，防回退）
# ===========================================================================
print("== G 组：源码锚点（血泪修复防回退）==")
_br_src = read_src(BR_PATH)
_bct_src = read_src(BCT_PATH)

check("G1  runner 整页 read 用 inner_text(\"body\")（H-07：无参直接 TypeError）",
      'inner_text("body")' in _br_src)

check("G2  runner CDP read 导航分支 url not in page.url（v4.147.9：read 永远读到当前页的根因）",
      "args.url not in page.url" in _br_src)

check("G3  runner 出口 stdout.reconfigure(utf-8)（H-09：中文 JSON 被 GBK 污染）",
      'reconfigure(encoding="utf-8")' in _br_src)

check("G4  control stdin 阈值 800（P2-10：改大 = 长文走命令行被 32767 截断）",
      "len(text) > 800" in _bct_src)

check("G5  control 子进程 env 注入 UTF-8（H-09 父进程侧）",
      '_run_env["PYTHONIOENCODING"] = "utf-8"' in _bct_src)

check("G6  control warn 旁路审计日志 cdp_vpn_warn.log（降级提示留痕）",
      "cdp_vpn_warn.log" in _bct_src)

# 语法完整性（变异副本若被改坏到不可解析，直接致命——不给「静默跳过」留门）
for _p_, _s_ in (("runner", _br_src), ("control", _bct_src)):
    try:
        ast.parse(_s_)
    except SyntaxError as e:
        check(f"G0  {_p_} 源码可解析", False, f"{e}")
        break
else:
    check("G0  两份源码均可解析（AST 完整）", True)

print("\n总判据 %d 项：PASS=%d FAIL=%d" % (_p, _p + _f, _f))
if _f:
    sys.exit(1)
print("=== TEST_BROWSER_CONTROL_OK ===")
