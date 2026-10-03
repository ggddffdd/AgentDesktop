# -*- coding: utf-8 -*-
"""扰动脚本统一护栏：防「进程被强杀 → 变异残留 → 后续判据假红/假绿」。

背景（真实事故）：_perturb_canvas_*.py 用 try/finally 还原被变异源码，
但 Python 的默认 SIGTERM 处理器是「立刻终止进程」——不抛异常、不走 finally。
于是当扰动脚本因超时/被 kill 而中断时（例如批量跑 9 个脚本超过工具超时），
磁盘上会留下半截变异体，例如 executors.py 里的
    if False:  # 扰动：不查越目录
后续所有判据就会「基线已红」，排查成本极高（看起来很像是业务代码坏了）。

本模块提供两件事：
  arm(paths)             —— 启动时：① 快照这些文件；② 装 SIGTERM/SIGINT/atexit
                            三重还原；③ 预检残留变异标记，发现即大声报错退出。
  assert_no_leftover()   —— 只做预检（供不想快照的脚本单独调用）。

约定：所有「变异标记」必须写成含 `# 扰动` 的注释，
否则预检扫不到。「变异」二字不可用作 marker——它在合法源码里已被占用
（skill_loader.py 的「变异选择符」注释），会导致永久误报。
新增扰动 case 请沿用 `# 扰动：<说明>` 这一写法。
"""

import atexit
import glob
import os
import signal
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# 预检扫描范围：顶层 .py + tests/*.py（画布链路的全部被测源码都在这里）
_SKIP_PREFIX = "_perturb_"
# 只认「扰动」：全仓合法源码里 0 次出现；而「变异」会误报
# —— skill_loader.py 有一行 `# 变异选择符`（Unicode 变体选择符），是正当注释。
# 实测结论：扰动脚本注入的调试注释 100% 用 `# 扰动：` 前缀，故以它作唯一标记。
_LEFT_MARKERS = ("# 扰动",)
_LEFT_PATTERN_EXEMPT = ("_perturb_guard.py",)

# 扰动脚本会在 ROOT 下落两类「私有中间文件」，正常路径都由 finally 清理：
#   ① 变异源码临时副本 —— tempfile.mkstemp(prefix="canvas_mut_") / "probe_gfx_mut_"，
#      供子进程以 CANVAS_PATH / PROBE_GFX_PATH 指向它跑判据（不在原文件上动刀的那一路）；
#   ② 旁路备份 —— 改原文件前先 shutil.copy(path, path + ".bak")
#      （canvas_edit / canvas_localedit），toast 用的是 "_toast.py.perturb.bak"。
# 但被强杀时 finally 不执行 → 上面两类都会残留成未跟踪垃圾文件。
#   实测残留过：canvas_graph.py.bak、risk.py.bak、_toast.py.perturb.bak。
# 这些名字都是扰动脚本的私有约定（业务源码旁不会出现 *.py.bak），
# 故在预检阶段无条件清理是安全的；arm() 再记下启动清单，兜底删掉「本次新增的」。
_SCRATCH_PATTERNS = ("canvas_mut_*.py", "*_mut_*.py", "*.py.bak", "*.perturb.bak")


def _scratch_files():
    found = set()
    for pat in _SCRATCH_PATTERNS:
        found.update(glob.glob(os.path.join(ROOT, pat)))
    return found


def _remove_retry(path, tries=5, delay=0.15):
    """删文件带重试：Windows 上刚写完的 .py 可能被 Defender 实时扫描/索引短暂占用，
    此时 os.remove 抛 PermissionError —— 这正是「残留副本偶尔出现」的真实原因
    （父脚本 finally 里 except Exception: pass 把它吞了）。"""
    import time
    for i in range(tries):
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if i == tries - 1:
                return False
            time.sleep(delay)
    return False


def sweep_scratch(verbose=True):
    """清掉 ROOT 下所有「变异源码临时副本」。返回被清掉的文件名列表。

    这些文件名（canvas_mut_*.py）是扰动脚本的私有 scratch 约定，不可能与
    业务源码重名，故在预检阶段无条件清理是安全的。
    """
    gone = []
    for fp in sorted(_scratch_files()):
        if _remove_retry(fp):
            gone.append(os.path.basename(fp))
        elif verbose:
            print("[护栏] 临时副本删除失败（请手动处理）: %s" % fp, file=sys.stderr)
    if gone and verbose:
        print("[护栏] 已清理上次残留的临时副本: %s" % ", ".join(gone))
    return gone


def _scan_files():
    files = [os.path.join(ROOT, n) for n in sorted(os.listdir(ROOT))
             if n.endswith(".py") and not n.startswith(_SKIP_PREFIX)]
    tdir = os.path.join(ROOT, "tests")
    if os.path.isdir(tdir):
        files += [os.path.join(tdir, n) for n in sorted(os.listdir(tdir))
                  if n.endswith(".py")]
    return files


def find_leftovers():
    """返回 [(相对路径, 行号, 行内容)]，即当前源码里残留的变异标记。"""
    hits = []
    for fp in _scan_files():
        if os.path.basename(fp) in _LEFT_PATTERN_EXEMPT:
            continue
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    # 只认「代码行上的调试行」：跳过纯注释行（比如本模块的文档）
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if any(m in line for m in _LEFT_MARKERS):
                        hits.append((os.path.relpath(fp, ROOT), i, stripped))
        except OSError:
            continue
    return hits


def assert_no_leftover(exit_code=3):
    """预检：先清掉 scratch 临时副本，再查残留变异标记；有标记就报错退出。"""
    sweep_scratch()
    hits = find_leftovers()
    if not hits:
        return
    print("=" * 68)
    print("[护栏] 检测到源码残留「扰动/变异」标记 —— 疑似上一次扰动被强杀未还原！")
    for rel, ln, text in hits:
        print("  %s:%d  %s" % (rel, ln, text))
    print("  请先还原这些行（git diff / git checkout -- <file>）再跑扰动。")
    print("=" * 68)
    sys.exit(exit_code)


_ARMED = False


def arm(paths=None):
    """快照 + 三重还原（SIGTERM / SIGINT / atexit）+ 残留预检。

    paths: 可迭代的绝对文件路径；None → 扫描 ROOT 下顶层 .py 与 tests/*.py。
    返回快照 dict（供脚本自己做精确还原时复用）。
    """
    global _ARMED
    assert_no_leftover()

    files = [os.path.abspath(p) for p in paths] if paths else _scan_files()
    snap = {}
    for fp in files:
        try:
            with open(fp, "rb") as f:
                snap[fp] = f.read()
        except OSError:
            continue
    scratch_before = _scratch_files()

    def _restore():
        for fp, blob in snap.items():
            try:
                with open(fp, "rb") as f:
                    if f.read() == blob:
                        continue          # 未被动过，免写
            except OSError:
                pass
            try:
                with open(fp, "wb") as f:
                    f.write(blob)
                print("[护栏] 已还原 %s" % os.path.relpath(fp, ROOT))
            except OSError as e:
                print("[护栏] 还原失败 %s: %s" % (fp, e), file=sys.stderr)
        # 清掉本次运行新产生的变异源码临时副本（正常路径 finally 已删，此处兜底）
        for fp in _scratch_files() - scratch_before:
            if _remove_retry(fp):
                print("[护栏] 已清理临时副本 %s" % os.path.basename(fp))
            else:
                print("[护栏] 临时副本删除失败: %s" % fp, file=sys.stderr)

    if _ARMED:
        return snap
    _ARMED = True

    def _on_signal(signum, frame):
        # 抛 SystemExit → 走脚本自身的 finally → 再走本模块 atexit 双保险
        print("\n[护栏] 收到信号 %d，还原被变异源码后退出" % signum)
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass
    atexit.register(_restore)
    return snap


if __name__ == "__main__":
    # 走 assert_no_leftover：先清 scratch 临时副本，再查残留变异标记
    sweep_scratch()
    assert_no_leftover()
    print("干净：未发现残留变异标记")
