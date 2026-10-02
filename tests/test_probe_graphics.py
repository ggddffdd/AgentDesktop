# -*- coding: utf-8 -*-
"""最小 QGraphicsView 探针判据（probe_graphics.py，节点画布设计稿第 0 步）。

探针本身是一次性工具，但它验证的三件事——windowed 打包 / ffmpeg 子进程 / 高 DPI
——会直接决定以后正式画布怎么写。这份判据的作用是：把那三条结论**钉死**，
以后谁改坏了探针、或者环境换了导致结论失效，红灯会亮在这里而不是等到打包上线才发现。

三组判据（独立运行：python tests/test_probe_graphics.py）：
  A 静态组 —— 三坑的**防御手段**必须在源码里（按函数体切片判定，不在整文件搜关键词，
                见 L201「关键词在场 ≠ 结构在场」）；
  B 行为组 —— 真跑探针三档 QT_SCALE_FACTOR（1.0/1.5/2.0），逐项核对 checks；
  C 缩放组 —— 跨档位比对：逻辑几何必须逐字段相等，物理像素必须线性放大，
                以及「不补 DPR 的缩略图」对照组必须真的偏大（证明 C 组不是空转）。

为什么行为组要跑三档而不是一档：只跑 1.0 时 DPR 恒等于 1，所有高 DPI 断言都会
平凡成立——绿得毫无信息。三档一起比，才能证明「DPR 真的变了，而逻辑几何确实没动」。
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

FAIL = []
CHECKED = 0


def check(name, ok, extra=""):
    global CHECKED
    CHECKED += 1
    tag = "OK  " if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  — {extra}" if extra and not ok else ""))
    if not ok:
        FAIL.append(name)


def _hook(t, v, tb):
    print(f"\n[!] 未捕获异常：{t.__name__}: {v}")
    print(f"PASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
    sys.exit(1)


sys.excepthook = _hook

PROBE = os.environ.get("PROBE_GFX_PATH") or os.path.join(ROOT, "probe_graphics.py")
SRC = open(PROBE, encoding="utf-8").read()

# 扰动脚本（_perturb_probe_graphics.py）会替换上面的被测文件。它只关心 A 组静态
# 判据，用 PROBE_GFX_STATIC=1 跳过 B/C 组（那两组要真跑 Qt + ffmpeg，各 ~5 秒）。
STATIC_ONLY = os.environ.get("PROBE_GFX_STATIC") == "1"

SCALES = ("1.0", "1.5", "2.0")


def _slice_func(name):
    """取某个 def/class 的源码切片（到下一个顶层 def/class/class 为止）。

    凡是「某函数里必须有某写法」的判据都走这里 —— 在整文件里搜关键词会把
    另一处同名调用也算进去（L190/L201 的旧坑）。
    """
    m = re.search(r"^(?:class|def)\s+%s\b" % re.escape(name), SRC, re.M)
    if not m:
        return ""
    nxt = re.search(r"^(?:class|def)\s+\w+", SRC[m.end():], re.M)
    return SRC[m.end(): m.end() + nxt.start()] if nxt else SRC[m.end():]


def _slice_call_in(name, call):
    """取函数体内某一个调用语句块（从 `call` 起到括号配平 / 行尾缩进来）。"""
    body = _slice_func(name)
    i = body.find(call)
    if i < 0:
        return ""
    # 括号配平：抓到这次调用的完整实参串为止
    depth = 0
    for j in range(i + len(call) - 1, len(body)):
        if body[j] == "(":
            depth += 1
        elif body[j] == ")":
            depth -= 1
            if depth == 0:
                return body[i: j + 1]
    return body[i:]


def read(rel):
    p = os.path.join(ROOT, rel)
    return open(p, encoding="utf-8").read() if os.path.isfile(p) else ""


# ==========================================================================
# A 组：静态 —— 三坑的防御手段
# ==========================================================================
print("\n[A] 静态：三坑防御手段必须在源码里")

SE = _slice_func("self_emit")
check("A1 self_emit 存在", bool(SE))
check("A2 self_emit 对 stdout 为 None 兜底", "if s is not None" in SE,
      "windowed 下 stdout 是 None，没有这句就是 AttributeError")
check("A3 self_emit 有日志兜底（第二落点）", "open(LOG_FILE" in SE)
check("A4 self_emit 对外不抛（整体 try）", SE.count("try:") >= 2)

NC = _slice_func("NoConsole")
check("A5 NoConsole 存在", bool(NC))
check("A6 NoConsole 真的把 stdout/stderr 置 None",
      "sys.stdout, sys.stderr = None, None" in NC and "_out, self._err" in NC,
      "用 StringIO 兜着测不出 windowed 的真实形态")

RF = _slice_func("run_ffmpeg")
SI = _slice_func("_startupinfo")
check("A7 run_ffmpeg 存在", bool(RF))
check("A8 ffmpeg 子进程显式 stdin=DEVNULL", "stdin=subprocess.DEVNULL" in RF,
      "windowed 下父进程无控制台句柄，不显式给会卡住/ValueError")
check("A9 ffmpeg 子进程带 creationflags=_NO_WINDOW", "creationflags=_NO_WINDOW" in RF)
check("A10 ffmpeg 子进程传 startupinfo", "startupinfo=_startupinfo()" in RF)
check("A11 run_ffmpeg 显式 utf-8 解码", 'encoding="utf-8"' in RF,
      "Windows 下按 GBK 解 ffmpeg 的 UTF-8 输出会 UnicodeDecodeError")
check("A12 _startupinfo 设 STARTF_USESHOWWINDOW", "STARTF_USESHOWWINDOW" in SI)
check("A13 _startupinfo 设 SW_HIDE(wShowWindow=0)", "wShowWindow = 0" in SI)
check("A14 模块级 _NO_WINDOW 与项目同款",
      "_NO_WINDOW = subprocess.CREATE_NO_WINDOW" in SRC)

check("A15 探针全程无裸 print", not re.search(r"^\s*print\(", SRC, re.M),
      "windowed 下裸 print 会崩，必须全走 self_emit")
check("A16 装了全局 excepthook（崩溃留遗言）", "sys.excepthook = _hook" in SRC)
check("A17 日志路径不可写时退回临时目录", "tempfile.gettempdir()" in SRC)

CM = _slice_func("collect_metrics")
BC = _slice_func("build_canvas_view")
# 精确锚点，不用宽泛关键词：collect_metrics 里有多处 setDevicePixelRatio，
# 只有这一处是「给 ffmpeg 抽帧出来的源图补 DPR」，判它才有用（见 L201）。
check("A18 高 DPI：给抽帧出来的源图补 setDevicePixelRatio",
      'pm2.setDevicePixelRatio(dpr)' in CM)
check("A19 高 DPI：真的算了「不补 DPR」对照组",
      'm["thumb_untreated_w"] = round(' in CM,
      "没有对照组，就无法证明补 DPR 到底解决什么问题")
check("A20 坑②：真在 NoConsole 下跑了一次 ffmpeg",
      "with NoConsole():" in CM and "run_ffmpeg" in CM)
check("A21 excepthook 先于 Qt 构造装好（崩溃必有遗言）",
      "m[\"app_started\"] = True" in CM and "_install_excepthook()" in CM)
MN = _slice_func("main")
_i_platform = MN.find('setdefault("QT_QPA_PLATFORM"')
_i_call = MN.find("collect_metrics()")
check("A22 先定 QT_QPA_PLATFORM 再造 QApplication（顺序）",
      _i_platform >= 0 and _i_call > _i_platform,
      f"platform@{_i_platform} call@{_i_call}")

# ==========================================================================
# B/C 组：行为 —— 真跑三档
# ==========================================================================
HAS_QT = importlib.util.find_spec("PySide6") is not None

if STATIC_ONLY:
    print("\n[B][C] 行为/缩放组：按 PROBE_GFX_STATIC 跳过（扰动脚本只验静态判据）")

elif not HAS_QT:
    print("\n[B] 行为：跳过（本机无 PySide6，跑不了 Qt 探针）")
    print("  [SKIP] 三档自检 / 跨档位比对")
else:
    print("\n[B] 行为：真跑探针三档 QT_SCALE_FACTOR")
    workdir = tempfile.mkdtemp(prefix="test_probe_gfx_")
    runs = {}
    for s in SCALES:
        jp = os.path.join(workdir, "r%s.json" % s)
        try:
            p = subprocess.run(
                [sys.executable, PROBE, "--selftest", "--scale", s, "--json", jp],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=150,
                env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
                cwd=ROOT)
            tail = (p.stdout or "").strip().splitlines()[-3:]
        except Exception as e:
            p, tail = None, ["%s: %s" % (type(e).__name__, e)]
        ok_file = os.path.isfile(jp)
        runs[s] = json.load(open(jp, encoding="utf-8")) if ok_file else None
        check(f"B1[{s}] 探针退出码 0", p is not None and p.returncode == 0,
              " ".join(tail))
        check(f"B2[{s}] 指标 JSON 落盘且可解析", runs[s] is not None)
        if runs[s] is None:
            continue
        m = runs[s]
        check(f"B3[{s}] ffmpeg 可用（--version rc=0）", m.get("ffmpeg_version_rc") == 0,
              f"rc={m.get('ffmpeg_version_rc')} err={m.get('ffmpeg_version_err')}")
        check(f"B4[{s}] 无控制台态下 ffmpeg 仍 rc=0（坑①+②）",
              m.get("ffmpeg_noconsole_rc") == 0, str(m.get("ffmpeg_noconsole_err")))
        check(f"B5[{s}] 抽帧缩略图落地", bool(m.get("thumb_exists")) and m.get("thumb_bytes", 0) > 0)
        check(f"B6[{s}] 物理像素 == 逻辑像素 × DPR",
              m.get("grab_physical") == m.get("expect_physical"),
              f"grab={m.get('grab_physical')} expect={m.get('expect_physical')}")
        check(f"B7[{s}] grab 除回 DPR 后等于 view 逻辑尺寸",
              m.get("grab_logical") == list(m.get("view_size") or []),
              f"logical={m.get('grab_logical')} view={m.get('view_size')}")
        check(f"B8[{s}] 抽帧图补 DPR 后逻辑宽 == 160",
              m.get("thumb_treated_w") is not None
              and abs(m["thumb_treated_w"] - 160.0) <= 0.6,
              str(m.get("thumb_treated_w")))
        check(f"B9[{s}] 探针自身 checks 全绿", bool(m.get("ok")),
              str([k for k, v in (m.get("checks") or {}).items() if not v]))

    # ---------------- C 组：跨档位 ----------------
    print("\n[C] 缩放：跨档位比对（逻辑不动 / 物理放大 / 对照组）")
    got = {s: runs[s] for s in SCALES if runs[s]}
    if len(got) < 2:
        check("C1 至少两档跑通才谈得上比对", False, f"只有 {list(got)}")
    else:
        check("C1 至少两档跑通", True)
        _first = got[SCALES[0]] if SCALES[0] in got else list(got.values())[0]
        for key in ("viewport_size", "scene_rect", "node_rects", "line",
                    "proxy_widget_size", "proxy_scene_rect", "transform_m11",
                    "grab_logical", "view_size"):
            vals = {s: m.get(key) for s, m in got.items()}
            same = len({json.dumps(v, ensure_ascii=False, sort_keys=True)
                        for v in vals.values()}) == 1
            check(f"C2 逻辑几何 [{key}] 跨档位逐字段相等", same, str(vals))

        dprs = {s: m.get("dpr") for s, m in got.items()}
        check("C3 DPR 随档位变化（证明真的产生了缩放）",
              len(set(dprs.values())) == len(dprs), str(dprs))

        phys = {s: (m.get("grab_physical") or [0])[0] for s, m in got.items()}
        mono = True
        ordered = sorted(got, key=lambda x: float(x))
        for a, b in zip(ordered, ordered[1:]):
            mono = mono and phys[a] < phys[b]
        check("C4 物理像素随缩放置数增大（800/960/1280 这类）", mono, str(phys))

        check("C5 未补 DPR 的缩略图确实比补过的宽（对照组有效）",
              all(got[s].get("thumb_untreated_w", 0) > got[s].get("thumb_treated_w", 0) + 0.5
                  for s in got if float(s) > 1.0),
              str({s: (got[s].get("thumb_untreated_w"), got[s].get("thumb_treated_w"))
                   for s in got}))
        check("C6 源码模式（非打包）跑出来的 frozen=False",
              all(got[s].get("frozen") is False for s in got))

print(f"\nPASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("=== PROBE_GRAPHICS_OK ===")
