# -*- coding: utf-8 -*-
"""v4.242.0 扰动脚本：「多步指令 DAG 化」相C（task_state.precond_check / dag_nudge_instruction）。

反向照妖镜：变异 task_state.py 的 precond_check 逻辑，跑 tests/test_intent_dag_239.py，
确认判据必红（改坏必红）。三个 case 全部针对相C 修复本体，零哑弹。
沿用已验证的「直接变异 + _perturb_guard 还原」模式（与 _perturb_intent_dag_239.py 同构）。

设计纪律（与 MEMORY.md 同源）：
  · 变异由 _perturb_guard 直接字符串替换 + 三重还原，本脚本不自带 # 扰动 标记。
  · 三重还原：SIGTERM / SIGINT / atexit，被强杀也能还原，不留半截变异体。
  · 判据翻红的判据：退出码非 0 且 stdout/stderr 含 FAIL 字样。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
TARGET = os.path.join(ROOT, "task_state.py")
JUDGE = os.path.join(ROOT, "tests", "test_intent_dag_239.py")

# ---- 精确变异点（与 task_state.py precond_check 1:1 对齐，已用 Read 工具核对唯一性）----

# case 1：漏落盘检测失效（write_file 未调不再被标记）→ D7-2 / D8-2 漏落盘检出翻红
OLD_V1 = 'if tool == "write_file" and "write_file" not in self.succeeded_tools:'
NEW_V1 = 'if False:  # 变异：漏落盘检测失效'

# case 2：依赖前置检查失效（precond_tool 未调不再被标记）→ D7-5 依赖前置检出翻红
OLD_V2 = ('if not called:\n'
          '                    missing.append((nid, tool, pt, "precond_not_called"))')
NEW_V2 = ('if False:  # 变异：依赖前置检查失效\n'
          '                    missing.append((nid, tool, pt, "precond_not_called"))')

# case 3：fail-open 被关（异常不再被兜成空清单，直接抛出）→ D7-7 异常 fail-open 翻红
OLD_V3 = '            return missing\n        except Exception:\n            return []'
NEW_V3 = '            return missing\n        except Exception:\n            raise  # 变异：fail-open 被关'

CASES = [
    ("V1 漏落盘检测失效（write_file 未调不标记）", OLD_V1, NEW_V1),
    ("V2 依赖前置检查失效（precond_tool 未调不标记）", OLD_V2, NEW_V2),
    ("V3 fail-open 被关（异常不再兜成空清单）", OLD_V3, NEW_V3),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    G.arm([TARGET])  # 三重还原 + 残留预检
    src = open(TARGET, "r", encoding="utf-8").read()
    total = 0
    hits = 0
    try:
        for name, old, new in CASES:
            total += 1
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            # 断言只变异一处
            assert src.count(old) == 1, "old 串出现 %d 次，非唯一" % src.count(old)
            mutated = src.replace(old, new, 1)
            open(TARGET, "w", encoding="utf-8", newline="").write(mutated)
            rc, out = run_judge()
            # 判据应翻红：退出码非 0 且含 FAIL 字样
            failed = (rc != 0) and ("FAIL" in out)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1500])
            # 还原（guard 也会在退出时再还原一次）
            open(TARGET, "w", encoding="utf-8", newline="").write(src)
    finally:
        open(TARGET, "w", encoding="utf-8", newline="").write(src)  # 最终兜底还原
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
