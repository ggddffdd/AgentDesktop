# -*- coding: utf-8 -*-
"""沙箱化 Python 代码执行：基于 RestrictedPython + 临时目录隔离。"""
import io
import os
import sys
import tempfile
import shutil
from pathlib import Path
from RestrictedPython import compile_restricted, safe_builtins
from RestrictedPython.Eval import default_guarded_getiter
from RestrictedPython.Guards import safer_getattr, guarded_iter_unpack_sequence


ALLOWED_MODULES = {
    "json", "math", "datetime", "re", "collections", "itertools",
    "functools", "statistics", "csv", "string", "random",
    "typing", "dataclasses", "hashlib", "base64",
}


class PrintCollector:
    """自定义 print 收集器"""
    def __init__(self):
        self._buffer = io.StringIO()

    def __call__(self, *args, **kwargs):
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        self._buffer.write(sep.join(str(a) for a in args) + end)

    def getvalue(self):
        return self._buffer.getvalue()


def run_sandbox(code, timeout=30):
    """在受限环境中执行 Python 代码，返回 (success, output, error)"""
    sandbox_dir = Path(tempfile.mkdtemp(prefix="ds_sandbox_"))
    old_cwd = os.getcwd()
    try:
        os.chdir(sandbox_dir)
        restricted_builtins = safe_builtins.copy()
        restricted_builtins.update({
            "_print_": None,
            "_getattr_": safer_getattr,
            "_getiter_": default_guarded_getiter,
            "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
        })
        byte_code = compile_restricted(code, filename="<sandbox>", mode="exec")
        printer = PrintCollector()
        sandbox_globals = {
            "__builtins__": restricted_builtins,
            "_print_": printer,
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
        output = stdout_capture.getvalue() or printer.getvalue()
        return True, output, ""
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}"
    finally:
        os.chdir(old_cwd)
        try:
            shutil.rmtree(sandbox_dir)
        except Exception:
            pass
