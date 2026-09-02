# -*- coding: utf-8 -*-
"""小臭玩AI · 性能基线模块（v4.78）

双轨设计：
  1) 应用内启动埋点：main.py 各阶段 mark()，启动完成 finalize_startup() 落盘
     perf/startup.jsonl（保留最近 50 条），记录真实冷启动各阶段耗时；全 try 包裹，
     任何失败都不影响主程序启动。
  2) 离线基准套件：run_benchmarks() 复跑核心路径（记忆层 append/recall/search、
     会话加载 500 条、HTML 渲染、核心模块导入、进程常驻内存），与 perf/baseline.json
     比对，超阈值标 REGRESSION / IMPROVED，落到 perf/runs/<ts>.json。

约定：
  - 顶部只 import 标准库，PySide6 / 业务模块只在函数内 import，确保 main.py 早期
    就能安全 `import perf_baseline`。
  - 记忆层 / 会话层基准全部重定向到临时目录（memory_store._configure / 打补丁
    SessionStore.PATH），跑完即还原，零污染真实数据。
  - 兼容冻结 exe：核心模块导入基准在 frozen 模式下跳过（标 None + skipped 原因）。

CLI:
  python perf_baseline.py                # 跑基准 + 与基线比对 + 落盘本次记录
  python perf_baseline.py --save-baseline # 跑基准并保存为新基线
  python perf_baseline.py --json          # 输出 JSON
"""
import os
import sys
import json
import time
import shutil
import tempfile
import subprocess
from datetime import datetime

PERF_DIR = os.path.join(os.path.expanduser("~/Documents/小臭玩AI"), "perf")
STARTUP_LOG = os.path.join(PERF_DIR, "startup.jsonl")
BASELINE_PATH = os.path.join(PERF_DIR, "baseline.json")
RUNS_DIR = os.path.join(PERF_DIR, "runs")
MAX_STARTUP_LINES = 50

# 回归 / 改善判定阈值（相对基线）：超过 +X% 视为回归，-X% 视为改善
REG_THRESHOLD = 0.15    # 15%
IMPROVE_THRESHOLD = 0.10  # 10%

# 各基准项采样次数（p50/p95 需要多次）
SAMPLES = {
    "memory_append": 20,
    "memory_recall": 30,
    "memory_search": 30,
    "html_render": 10,
}


# ============================================================
#  1) 启动埋点
# ============================================================
_start_marks = []  # [(stage, monotonic), ...]


def mark(stage):
    """在启动流程中调用，记录一个阶段的时间点。失败静默，绝不影响启动。"""
    try:
        _start_marks.append((stage, time.perf_counter()))
    except Exception:
        pass


def finalize_startup(extra=None):
    """启动完成调用：计算各阶段耗时并落盘 startup.jsonl。返回本次记录（UI 可直接展示）。

    extra: 附加记录字段（如版本、是否冻结等）。
    全 try 包裹：任何异常返回 None，不影响主程序。
    """
    try:
        if len(_start_marks) < 2:
            return None
        t0 = _start_marks[0][1]
        stages = []
        prev = _start_marks[0][1]
        for stage, t in _start_marks[1:]:
            dt = t - prev
            stages.append({"stage": stage, "t": round(t - t0, 3), "dt": round(dt, 3)})
            prev = t
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "total": round(_start_marks[-1][1] - t0, 3),
            "stages": stages,
        }
        if extra:
            rec.update(extra)
        _append_line(STARTUP_LOG, rec, MAX_STARTUP_LINES)
        return rec
    except Exception:
        return None


def last_startup():
    """读取最近一条启动记录（UI 展示用）。失败返回 None。"""
    try:
        if not os.path.exists(STARTUP_LOG):
            return None
        with open(STARTUP_LOG, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        return json.loads(lines[-1]) if lines else None
    except Exception:
        return None


def _append_line(path, obj, max_lines=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
    lines.append(json.dumps(obj, ensure_ascii=False))
    if max_lines and len(lines) > max_lines:
        lines = lines[-max_lines:]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ============================================================
#  2) 基准套件
# ============================================================
def _pctl(vals, p):
    """线性插值百分位数。vals 为空返回 None。"""
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return round(s[0], 4)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 4)


def _bench_memory():
    """记忆层基准：隔离临时目录，测 append p50/p95、recall p50/p95、search p50/p95。"""
    import memory_store as ms
    tmp = tempfile.mkdtemp(prefix="perf_mem_")
    orig_dir = ms.MEMORY_DIR
    try:
        ms._configure(tmp)
        # 播种 60 条，供 recall/search 命中
        for i in range(60):
            ms.append_memory("测试事实%d：用于召回基准的内容 %d" % (i, i),
                             type="fact", topic="t%d" % (i % 10), tags=["perf"])
        # append 采样（每次内容唯一，避免去重跳过）
        append_times = []
        for i in range(SAMPLES["memory_append"]):
            t = time.perf_counter()
            ms.append_memory("基准追加%d-%f" % (i, time.time()),
                             topic="bench_append", tags=["perf"])
            append_times.append(time.perf_counter() - t)
        # recall 采样
        recall_times = []
        for i in range(SAMPLES["memory_recall"]):
            t = time.perf_counter()
            ms.recall_memory("测试事实%d" % (i % 60), limit=8)
            recall_times.append(time.perf_counter() - t)
        # search 采样
        search_times = []
        for i in range(SAMPLES["memory_search"]):
            t = time.perf_counter()
            ms.search_memory("内容 %d" % (i % 60), limit=5)
            search_times.append(time.perf_counter() - t)
        return {
            "memory_append_p50": _pctl(append_times, 50),
            "memory_append_p95": _pctl(append_times, 95),
            "memory_recall_p50": _pctl(recall_times, 50),
            "memory_recall_p95": _pctl(recall_times, 95),
            "memory_search_p50": _pctl(search_times, 50),
            "memory_search_p95": _pctl(search_times, 95),
        }
    finally:
        try:
            ms._configure(orig_dir)  # 还原到真实目录
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def _bench_session():
    """会话加载基准：构造 500 条会话后保存，再测 SessionStore() 加载耗时。隔离临时文件。"""
    import session as sess_mod
    tmp = tempfile.mkdtemp(prefix="perf_sess_")
    tmp_path = os.path.join(tmp, "sessions.json")
    saved_path = sess_mod.SessionStore.PATH
    try:
        sess_mod.SessionStore.PATH = tmp_path
        store = sess_mod.SessionStore()  # 触发默认会话
        for i in range(500):
            sid = "s%d" % i
            s = sess_mod.Session(sid, title="会话%d" % i)
            s.messages = [
                {"role": "user", "content": "问题%d" % i},
                {"role": "assistant", "content": ("回答%d" % i) * 5},
            ]
            store.sessions[sid] = s
        store.save()  # 一次性落盘 500 条
        t = time.perf_counter()
        _ = sess_mod.SessionStore()  # 重新加载计时
        load_dt = time.perf_counter() - t
        return {"session_load_500": round(load_dt, 3)}
    finally:
        sess_mod.SessionStore.PATH = saved_path
        shutil.rmtree(tmp, ignore_errors=True)


def _bench_html(app=None):
    """HTML 渲染基准：offscreen QTextDocument 渲染样例 HTML，测 p50/p95。"""
    from PySide6.QtGui import QTextDocument
    if app is None:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(["perf_baseline"])
    sample = "<h2>标题示例</h2><p>" + "段落文本 " * 200 + "</p>" + ("<ul><li>列表项</li></ul>" * 20)
    times = []
    for _ in range(SAMPLES["html_render"]):
        doc = QTextDocument()
        t = time.perf_counter()
        doc.setHtml(sample)
        doc.adjustSize()
        _ = doc.size()
        times.append(time.perf_counter() - t)
        try:
            doc.deleteLater()
        except Exception:
            pass
    return {
        "html_render_p50": _pctl(times, 50),
        "html_render_p95": _pctl(times, 95),
    }


def _bench_import_core():
    """核心模块导入基准：子进程全新解释器 import 主要模块，测冷导入耗时。

    frozen(exe) 模式下无独立 python 可拉起，跳过并返回 skipped 原因。
    """
    if getattr(sys, "frozen", False):
        return {"import_core": None, "import_core_skipped": "frozen"}
    here = os.path.dirname(os.path.abspath(__file__))
    script = (
        "import time, importlib\n"
        "t = time.perf_counter()\n"
        "for m in ['config', 'memory_store', 'context_manager', 'session', 'ui', 'agent', 'tools']:\n"
        "    importlib.import_module(m)\n"
        "print('IMPORT_TIME', time.perf_counter() - t)\n"
    )
    try:
        env = dict(os.environ)
        env["PYTHONPATH"] = here + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        p = subprocess.run(
            [sys.executable, "-c", script], cwd=here, env=env,
            capture_output=True, text=True, timeout=120,
        )
        out = (p.stdout or "") + (p.stderr or "")
        for line in out.splitlines():
            if line.startswith("IMPORT_TIME"):
                return {"import_core": round(float(line.split()[1]), 3)}
        return {"import_core": None, "import_core_skipped": "no_output"}
    except Exception as e:
        return {"import_core": None, "import_core_skipped": str(e)[:80]}


def _process_rss_mb():
    """当前进程常驻内存(MB)。Windows 用 psapi，其他用 resource。失败返回 None。"""
    try:
        if sys.platform.startswith("win"):
            import ctypes
            from ctypes import wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            cnt = _PMC()
            cnt.cb = ctypes.sizeof(cnt)
            api = ctypes.windll.psapi.GetProcessMemoryInfo
            api.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
            api.restype = wintypes.BOOL
            api(-1, ctypes.byref(cnt), cnt.cb)  # -1 = GetCurrentProcess 伪句柄
            return cnt.WorkingSetSize / (1024.0 * 1024.0)
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return None


def run_benchmarks(app=None):
    """跑全部基准项，返回 metrics dict。单项异常标 *_error 或 None，不影响其他项。"""
    metrics = {}
    steps = [
        ("memory", _bench_memory),
        ("session", _bench_session),
        ("html", lambda: _bench_html(app)),
        ("import", _bench_import_core),
    ]
    for name, fn in steps:
        try:
            metrics.update(fn())
        except Exception as e:
            metrics[name + "_error"] = str(e)[:120]
    rss = _process_rss_mb()
    metrics["rss_mb"] = round(rss, 1) if rss else None
    metrics["ts"] = datetime.now().isoformat(timespec="seconds")
    metrics["frozen"] = bool(getattr(sys, "frozen", False))
    return metrics


# ============================================================
#  3) 基线对比 / 落盘
# ============================================================
def compare_with_baseline(metrics):
    """与 baseline.json 比对，返回 (comparison dict, verdict)。

    verdict ∈ {"NO_BASELINE", "BASELINE_ERR", "OK", "IMPROVED", "REGRESSION"}。
    任一项超 REG_THRESHOLD 即整体 REGRESSION；否则若无 IMPROVED 则 OK。
    """
    try:
        if not os.path.exists(BASELINE_PATH):
            return {}, "NO_BASELINE"
        with open(BASELINE_PATH, "r", encoding="utf-8") as f:
            base = json.load(f)
    except Exception:
        return {}, "BASELINE_ERR"

    cmp = {}
    verdict = "OK"
    has_improved = False
    for k, v in metrics.items():
        if k in ("ts", "frozen") or not isinstance(v, (int, float)):
            continue
        bv = base.get(k)
        if not isinstance(bv, (int, float)) or bv == 0:
            cmp[k] = {"value": v, "baseline": bv, "delta_pct": None, "status": "n/a"}
            continue
        dpct = (v - bv) / bv
        if dpct > REG_THRESHOLD:
            status = "REGRESSION"
            verdict = "REGRESSION"
        elif dpct < -IMPROVE_THRESHOLD:
            status = "IMPROVED"
            has_improved = True
        else:
            status = "OK"
        cmp[k] = {
            "value": round(v, 4),
            "baseline": round(bv, 4),
            "delta_pct": round(dpct * 100, 1),
            "status": status,
        }
    if verdict == "OK" and has_improved:
        verdict = "IMPROVED"
    return cmp, verdict


def save_baseline(metrics):
    """保存当前 metrics 为新基线（仅保留数值型指标）。返回基线文件路径。"""
    os.makedirs(PERF_DIR, exist_ok=True)
    clean = {k: v for k, v in metrics.items()
             if isinstance(v, (int, float)) and k not in ("ts",)}
    clean["saved_at"] = metrics.get("ts")
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    return BASELINE_PATH


def save_run(metrics, cmp, verdict):
    """保存本次运行记录（含 metrics + 比对 + 判定）到 runs/<ts>.json。"""
    os.makedirs(RUNS_DIR, exist_ok=True)
    ts = metrics.get("ts", datetime.now().isoformat(timespec="seconds"))
    fname = ts.replace(":", "").replace("-", "").replace("T", "_").replace("+", "_").split(".")[0]
    path = os.path.join(RUNS_DIR, fname + ".json")
    rec = {"metrics": metrics, "comparison": cmp, "verdict": verdict}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    return path


# ============================================================
#  4) CLI
# ============================================================
def _print_report(metrics, cmp, verdict, run_path):
    print("=" * 58)
    print("小臭玩AI 性能基线报告  (%s)" % metrics.get("ts"))
    print("=" * 58)
    for k, v in metrics.items():
        if k in ("ts", "frozen"):
            continue
        print("  %-22s %s" % (k, v))
    print("-" * 58)
    print("比对基线：%s" % verdict)
    for k, c in cmp.items():
        if c.get("status") == "n/a":
            print("  %-22s %s（无基线值，跳过）" % (k, c.get("value")))
        else:
            print("  %-22s %s  (%+g%%) %s" % (k, c["value"], c.get("delta_pct") or 0, c["status"]))
    print("-" * 58)
    print("本次记录：%s" % run_path)


def main_cli():
    import argparse
    ap = argparse.ArgumentParser(description="小臭玩AI 性能基线")
    ap.add_argument("--save-baseline", action="store_true", help="跑基准并设为新基线")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    metrics = run_benchmarks()
    if args.save_baseline:
        save_baseline(metrics)
        print("基线已保存：%s" % BASELINE_PATH)
    cmp, verdict = compare_with_baseline(metrics)
    run_path = save_run(metrics, cmp, verdict)

    if args.json:
        print(json.dumps(
            {"metrics": metrics, "comparison": cmp, "verdict": verdict, "run_path": run_path},
            ensure_ascii=False, indent=2))
    else:
        _print_report(metrics, cmp, verdict, run_path)


if __name__ == "__main__":
    main_cli()
