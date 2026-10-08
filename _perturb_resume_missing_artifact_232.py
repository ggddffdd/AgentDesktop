# -*- coding: utf-8 -*-
"""断点 D 扰动脚本（v4.232）：续跑跳过缺失产物（write_file 产物存在性校验）。

反向照妖镜：变异 agent.py 的修复点，跑 test_resume_missing_artifact_232.py，
确认判据必红（改坏必红）。两个 case 全部针对修复本体，零哑弹。
沿用 B/A 点已验证的「直接变异 + _perturb_guard 还原」模式。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"
TARGET = os.path.join(ROOT, "agent.py")
JUDGE = os.path.join(ROOT, "tests", "test_resume_missing_artifact_232.py")

# ---- 精确变异点（与源码 1:1 对齐，已用 Read 工具核对唯一性）----

# case 1：删掉 write_file 产物存在性二次校验块 → 退回「哈希命中即 dup」，
# 即断点 D 原 bug（账本记 done 但文件被删仍判已完成、跳过）。
OLD_V1 = """        if _tool_args_hash(name, args_sig) not in _done:
            return False
        # v4.232 断点 D：write_file 必须二次校验产物仍存在，缺失则视为未 dup、必须重做。
        if name == "write_file":
            _ap = _resolve_write_file_path(args_sig)
            if _ap is not None and not os.path.isfile(_ap):
                return False
        return True"""
NEW_V1 = """        if _tool_args_hash(name, args_sig) not in _done:
            return False
        return True"""

# case 2：让 _resolve_write_file_path 永远返回 None（路径解析失效），
# 使存在性校验被整段跳过，等效于 case 1 的 bug，但变异落点不同。
OLD_V2 = """        _p = _a.get("path")
        if not _p:
            return None
        return os.path.abspath(os.path.join(WORKSPACE_DIR, _p))
    except Exception:
        return None"""
NEW_V2 = """        _p = _a.get("path")
        if not _p:
            return None
        return None
    except Exception:
        return None"""

CASES = [
    ("V1 删 write_file 产物存在性二次校验块（核心回归）", OLD_V1, NEW_V1,
     ["D2"]),
    ("V2 让 _resolve_write_file_path 恒返 None（路径解析失效）", OLD_V2, NEW_V2,
     ["D2"]),
]


def run_judge():
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


def main():
    G.arm([TARGET])  # 三重还原 + 残留预检
    src = open(TARGET, "r", encoding="utf-8").read()
    total = 0
    hits = 0
    try:
        for name, old, new, expect in CASES:
            total += 1
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            # 断言只变异一处
            assert src.count(old) == 1, "old 串出现 %d 次，非唯一" % src.count(old)
            mutated = src.replace(old, new, 1)
            open(TARGET, "w", encoding="utf-8").write(mutated)
            rc, out = run_judge()
            # 判据应翻红：退出码非 0 且含 FAIL 字样（覆盖 D2 的 FAIL=）
            failed = (rc != 0) and ("FAIL" in out or any(t in out for t in expect))
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1500])
            # 还原（guard 也会在退出时再还原一次）
            open(TARGET, "w", encoding="utf-8").write(src)
    finally:
        open(TARGET, "w", encoding="utf-8").write(src)  # 最终兜底还原
    # 注：_perturb_guard 无 disarm——arm 时注册的 atexit/signal 会在进程退出时
    # 自动还原，finally 此处也已手动还原，故无需也不能调用不存在的 disarm。
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
