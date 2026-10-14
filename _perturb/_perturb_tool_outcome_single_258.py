# -*- coding: utf-8 -*-
"""v4.258.0 扰动脚本：工具成败判定归一到单一真源（P1-6）。

反向照妖镜：逐个把改动打回「未修」状态，跑 tests/test_tool_outcome_single_258.py，
确认判据必红（改坏必红、零哑弹）。SKIP/MISS = 哑弹，判据白写。

V1  result_mixin 自起炉灶 → 回到「两份各判各的」（T1-3 专盯：打补丁只命中 1 次）
V2  task_mixin 自起炉灶   → 运行时那份恢复内联实现，msg 抛异常时重新崩（T2/T4）
V3  err_head 去掉 try 保护 → 单一真源自己也不健壮了（msg 抛异常重新冒泡）
V4  .ok 崩不再 fail-open  → 健壮性回退（T3-4）
V5  fail-open 反转        → 注入判据崩时改成记失败（误报未完成，比漏报更伤）
V6  err_head 不截断       → 超长 msg 撑爆证据登记（T3-5）
V7  error_code 不进 err_head → 机器可读原因丢失，退回"看文案"（v4.226 病根复发）
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
TC = os.path.join(ROOT, "tool_contract.py")
RM = os.path.join(ROOT, "agent_result_mixin.py")
TM = os.path.join(ROOT, "agent_task_mixin.py")
JUDGE = os.path.join(ROOT, "tests", "test_tool_outcome_single_258.py")

CASES = [
    ("V1 result_mixin 自起炉灶", RM,
     "        return tool_contract.tool_outcome(\n"
     "            tool_result, name, result_str, failed_checker)",
     "        if tool_result is not None and hasattr(tool_result, 'ok'):  # 扰动：自起炉灶\n"
     "            return (bool(tool_result.ok), None)\n"
     "        return (True, None)"),
    ("V2 task_mixin 自起炉灶", TM,
     "        return tool_contract.tool_outcome(\n"
     "            tool_result, name, result_str, failed_checker)",
     "        if tool_result is not None and hasattr(tool_result, 'ok'):  # 扰动：自起炉灶\n"
     "            return (bool(tool_result.ok),\n"
     "                    str(getattr(tool_result, 'msg', '') or ''))\n"
     "        return (True, None)"),
    ("V3 err_head 去掉 try 保护", TC,
     "        try:\n"
     "            parts.append(str(getattr(tool_result, \"msg\", \"\") or \"\"))\n"
     "        except Exception:\n"
     "            parts.append(\"\")",
     "        parts.append(str(getattr(tool_result, \"msg\", \"\") or \"\"))  # 扰动：无保护"),
    ("V4 .ok 崩不再 fail-open", TC,
     "        except Exception:\n"
     "            _ok = True            # .ok 读不出来 → fail-open（与历史两版一致）",
     "        except Exception:\n"
     "            raise  # 扰动：不再 fail-open"),
    ("V5 注入判据崩改成记失败", TC,
     "    try:\n"
     "        _failed = bool(failed_checker(name, result_str))\n"
     "    except Exception:\n"
     "        return (True, None)",
     "    try:\n"
     "        _failed = bool(failed_checker(name, result_str))\n"
     "    except Exception:\n"
     "        return (False, '注入判据崩了')  # 扰动：fail-open 反转"),
    ("V6 err_head 不截断", TC,
     "    return \" \".join(parts)[:300]",
     "    return \" \".join(parts)  # 扰动：不截断"),
    ("V7 error_code 不进 err_head", TC,
     "        _ec = getattr(tool_result, \"error_code\", None)\n"
     "        if _ec:\n"
     "            parts.append(\"[%s]\" % _ec)",
     "        _ec = None  # 扰动：丢掉机器可读原因\n"
     "        if _ec:\n"
     "            parts.append(\"[%s]\" % _ec)"),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [TC, RM, TM]
    G.arm(files)
    snaps = {}
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            snaps[fp] = f.read()
    total = 0
    hits = 0
    misses = []
    try:
        for name, fp, old, new in CASES:
            total += 1
            src = snaps[fp]
            if old not in src:
                misses.append(name)
                print("SKIP %s: old 串未命中（源码已变，需更新脚本）" % name)
                continue
            cnt = src.count(old)
            assert cnt == 1, "old 串在 %s 出现 %d 次，非唯一" % (fp, cnt)
            mutated = src.replace(old, new, 1)
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(mutated)
            try:
                rc, out = run_judge()
                failed = (rc != 0) and ("FAIL" in out)
            except Exception as e:  # noqa: BLE001
                failed, out = False, "judge 崩了: %r" % e
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                misses.append(name)
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1200])
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    finally:
        for fp, src in snaps.items():
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    if misses:
        print("哑弹清单: %s" % ", ".join(misses))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
