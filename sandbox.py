# -*- coding: utf-8 -*-
"""沙箱化 Python 代码执行：基于 RestrictedPython + 临时目录隔离。

审计修复 F6（两项静默缺陷）：
1. `timeout` 参数原本从未生效——死循环代码会永久卡死调用线程。
   现改为子进程隔离执行：父进程 communicate(timeout) 等待，超时直接 kill
   （Python 线程无法强杀，子进程可以）。EXE(frozen) 无独立解释器时回退
   守护线程 + join(timeout)：至少不卡调用方，并如实报告局限。
2. `os.chdir(sandbox_dir)` 是进程级副作用——沙箱运行期间其他线程的相对
   路径写文件会落进随后被 rmtree 的临时目录。现父进程不再 chdir，
   子进程通过 cwd= 参数隔离工作目录。
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import shutil
from pathlib import Path
from RestrictedPython import compile_restricted, safe_builtins
from RestrictedPython.Eval import default_guarded_getiter
from RestrictedPython.Guards import safer_getattr, guarded_iter_unpack_sequence

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


ALLOWED_MODULES = {
    "json", "math", "datetime", "re", "collections", "itertools",
    "functools", "statistics", "csv", "string", "random",
    "typing", "dataclasses", "hashlib", "base64",
}


class PrintCollector:
    """RestrictedPython print 协议适配器。

    变换后的代码是 `_print = _print_()`（_print_ 必须是**无参工厂**），
    每个 print(x) 变成 `_print._call_print(x)`。旧实现把收集器*实例*塞进
    `_print_`，调用即返回 None → 'NoneType' object has no attribute
    '_call_print'（审计修复 F6 连带修正：输出统一写进当前 sys.stdout，
    而 _execute_inline 已把 stdout 重定向到捕获缓冲，print 与直接 write
    合并为同一通道，不再有半截丢失问题）。
    """
    def _call_print(self, *objects, **kwargs):
        print(*objects, **kwargs)


def _print_factory(*args, **kwargs):
    # RestrictedPython 各版本对 `_print_(...)` 的实参不同（0 个或 _getattr_），宽进
    return _PRINT_COLLECTOR


_PRINT_COLLECTOR = PrintCollector()


def _execute_inline(code):
    """在当前进程执行受限代码（调用方负责 cwd 已在沙箱目录）。
    返回 (success, output, error)。"""
    try:
        restricted_builtins = safe_builtins.copy()
        restricted_builtins.update({
            "_getattr_": safer_getattr,
            "_getiter_": default_guarded_getiter,
            "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
        })
        byte_code = compile_restricted(code, filename="<sandbox>", mode="exec")
        sandbox_globals = {
            "__builtins__": restricted_builtins,
            "_print_": _print_factory,
        }
        for mod_name in ALLOWED_MODULES:
            try:
                sandbox_globals[mod_name] = __import__(mod_name)
            except ImportError:
                pass
        stdout_capture = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = stdout_capture
        try:
            exec(byte_code, sandbox_globals)
        finally:
            sys.stdout = old_stdout
        return True, stdout_capture.getvalue(), ""
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}"


def _run_in_subprocess(code, sandbox_dir, timeout):
    """F6 主路径：独立子进程执行，超时可硬杀。返回 (success, output, error)。"""
    result_path = os.path.join(sandbox_dir, "_sandbox_result.json")
    cmd = [sys.executable, os.path.abspath(__file__), "--runner", code,
           "--result-file", result_path]
    try:
        proc = subprocess.run(
            cmd, cwd=sandbox_dir, capture_output=True, timeout=timeout,
            creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return False, "", (f"执行超时（>{timeout}s），子进程已终止"
                           "——疑似死循环/阻塞 IO，请拆小或加限制")
    except Exception as e:
        return False, "", f"沙箱子进程启动失败：{e}"
    try:
        with open(result_path, "r", encoding="utf-8") as f:
            res = json.load(f)
        return bool(res.get("ok")), str(res.get("output", "")), str(res.get("error", ""))
    except Exception:
        # 子进程没写出结果文件（被 OOM 杀/解释器异常）：以 stderr 兜底
        err = (proc.stderr or b"").decode("utf-8", "ignore").strip()
        return False, "", err or f"沙箱子进程异常退出（code={proc.returncode}）"


def _run_in_thread(code, sandbox_dir, timeout):
    """F6 回退路径（frozen EXE 无独立解释器）：守护线程 + join(timeout)。
    超时线程无法硬杀（如实报告局限），但调用方不再被永久卡死。"""
    holder = {}
    old_cwd = os.getcwd()

    def _target():
        try:
            os.chdir(sandbox_dir)
            holder["r"] = _execute_inline(code)
        except Exception as e:
            holder["r"] = (False, "", f"{type(e).__name__}: {e}")
        finally:
            try:
                os.chdir(old_cwd)
            except Exception:
                pass

    th = threading.Thread(target=_target, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return False, "", (f"执行超时（>{timeout}s）——EXE 模式无法强杀 Python 线程，"
                           "该后台线程将持续占用资源到进程退出")
    return holder.get("r", (False, "", "沙箱执行无结果"))


def run_sandbox(code, timeout=30):
    """在受限环境中执行 Python 代码，返回 (success, output, error)"""
    sandbox_dir = Path(tempfile.mkdtemp(prefix="ds_sandbox_"))
    try:
        if getattr(sys, "frozen", False):
            return _run_in_thread(code, str(sandbox_dir), timeout)
        return _run_in_subprocess(code, str(sandbox_dir), timeout)
    finally:
        try:
            shutil.rmtree(sandbox_dir, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    # F6：子进程 runner 模式——python sandbox.py --runner <代码> --result-file <路径>
    # 用户代码的 print 被 stdout 捕获进结果 JSON，不会污染通道。
    _argv = sys.argv[1:]
    try:
        _code = _argv[_argv.index("--runner") + 1]
        _rf = _argv[_argv.index("--result-file") + 1]
    except (ValueError, IndexError):
        print("用法: python sandbox.py --runner <code> --result-file <path>")
        sys.exit(2)
    _ok, _out, _err = _execute_inline(_code)
    try:
        with open(_rf, "w", encoding="utf-8") as f:
            json.dump({"ok": _ok, "output": _out, "error": _err}, f, ensure_ascii=False)
    except Exception:
        sys.exit(1)
