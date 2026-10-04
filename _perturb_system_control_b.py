# -*- coding: utf-8 -*-
"""扰动验证：证明 `tests/test_system_control_b.py` 的判据「会红在该红的地方」。

判据绿不等于判据有效。本脚本逐个**拆掉 B 批判据所守的写法**，看对应判据是否真转红。

规矩（与 _perturb_canvas_*.py / _perturb_system_control_a.py 同一套）：
  1. 期望采用**包含式**：expect ⊆ actual_red；只要求「该红的红了」，不禁止连带红；
  2. 每个 case 真的生成一份变异源码落临时文件，用 SCT_PATH 指向它跑判据，
     **不在原文件上动刀**；
  3. 收尾跑一次「原样基线」，期望零红 —— 防「判据过宽、什么都判红」。

两组改动各按本仓约定验两个方向（「整段删掉」与「只删动作留条件」）：
  缺参校验：PB2 helper 空转（留函数、删判别） / PB11 整段删 8 处调用（留 helper）
  进程解析：PB4 整段回退裸切（留函数）    / PB5 只删逗号清洗（留 csv 解析）

用法：python _perturb_system_control_b.py
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
JUDGE = os.path.join(ROOT, "tests", "test_system_control_b.py")

sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

SCT_SRC = open(os.path.join(ROOT, "system_control_tools.py"), encoding="utf-8").read()

PASS_N = 0
FAIL_N = 0
FAILED_CASES = []


def run_judge(env_src):
    paths = []
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    try:
        for var, (prefix, src) in env_src.items():
            fd, p = tempfile.mkstemp(prefix=prefix, suffix=".py", dir=ROOT)
            os.close(fd)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(src)
            paths.append(p)
            env[var] = p
        r = subprocess.run([sys.executable, JUDGE], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=420,
                           env=env, cwd=ROOT)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception as e:                                     # noqa: BLE001
        out = "%s: %s" % (type(e).__name__, e)
    finally:
        for p in paths:
            try:
                os.remove(p)
            except Exception:                                  # noqa: BLE001
                pass
    red = re.findall(r"\[FAIL\]\s+(.+?)\s*$", out, re.M)
    return red, out


def sub(src, old, new, label, count=1):
    if src.count(old) != count:
        raise AssertionError("锚点失配(%s): 命中 %d 次，期望 %d" % (label, src.count(old), count))
    return src.replace(old, new)


def case(name, env_src, expects):
    global PASS_N, FAIL_N
    red, out = run_judge(env_src)
    print("-" * 66)
    print("case %s" % name)
    if not red:
        print("  [FAIL] 变异后居然零红 —— 判据根本没抓到这个改动")
        print("  ---- 判据输出尾部 ----")
        print("\n".join(out.strip().splitlines()[-12:]))
        FAIL_N += 1
        FAILED_CASES.append(name + "（零红）")
        return
    missed = [e for e in expects if not any(e in r for r in red)]
    if missed:
        print("  [FAIL] 期望转红但没红：%s" % missed)
        print("  实际红项：")
        for r in red:
            print("    -", r)
        FAIL_N += 1
        FAILED_CASES.append(name + "（漏红）")
        return
    print("  [OK  ] 命中 %d 项：%s" % (len(red), "；".join(red[:3]) + ("…" if len(red) > 3 else "")))
    PASS_N += 1


_REQ_DEF_START = "def _require(args, *keys):"
_REQ_DEF_END = '    return "缺少必填参数：" + "、".join(missing) + "。请补齐后重试。"\n'

_MUST = "★ 每个必填键缺失时都返回『缺少必填参数：<键>』（不是 KeyError）"
_NO_RAISE = "★ 空入参 14 个工具全部不抛异常（文件头承诺『永不抛异常』）"
_SHAPE = "★ 14 个工具 × 3 种畸形入参：恒返回 (str, list, None)，一处不抛"
_PERMILLE = "★ 千分位内存不被逗号切碎：12,345 K → 12 MB"

# ---------------------------------------------------------------------------
# 缺参校验
# ---------------------------------------------------------------------------
# PB1：整段删掉 _require 定义（8 处调用留着，全部 NameError）
_s = SCT_SRC.index(_REQ_DEF_START)
_e = SCT_SRC.index(_REQ_DEF_END, _s) + len(_REQ_DEF_END)
_mut = SCT_SRC[:_s] + SCT_SRC[_e:]
assert "_miss = _require(args, " in _mut and "def _require(" not in _mut, "PB1 变异未生效"
case("PB1 整段删掉 _require 定义（调用留着 → NameError）",
     {"SCT_PATH": ("sct_mut_", _mut)},
     [_NO_RAISE, _MUST, _SHAPE])

# PB2：只让 helper 空转（删判别、留函数）
_mut = sub(SCT_SRC,
           "    missing = [k for k in keys if k not in args]\n"
           "    if not missing:\n"
           "        return None\n",
           "    missing = []\n"
           "    if not missing:\n"
           "        return None\n",
           "PB2 blank require")
case("PB2 _require 只空转（留函数、删判别）",
     {"SCT_PATH": ("sct_mut_", _mut)},
     [_MUST])

# PB3：只回退一处调用（mouse_move）
_mut = sub(SCT_SRC,
           '    _miss = _require(args, "x", "y")\n'
           "    if _miss:\n"
           "        return (_miss, [], None)\n",
           "",
           "PB3 drop one guard")
case("PB3 只删 mouse_move 一处缺参校验（其余 7 处留着）",
     {"SCT_PATH": ("sct_mut_", _mut)},
     [_MUST, _NO_RAISE])

# PB4：整段回退为按逗号裸切（留函数，删 csv 解析）
_mut = sub(SCT_SRC,
           "        for parts in csv.reader(io.StringIO(result.stdout)):\n"
           "            if len(parts) < 5:\n"
           "                continue\n"
           "            name, pid, mem_str = parts[0], parts[1], parts[4]\n",
           '        for line in result.stdout.strip().split("\\n"):\n'
           '            parts = line.replace(\'"\', "").split(",")\n'
           "            if len(parts) < 5:\n"
           "                continue\n"
           "            name, pid, mem_str = parts[0], parts[1], parts[4]\n",
           "PB4 revert to raw split")
case("PB4 整段回退 process_list 为按逗号裸切",
     {"SCT_PATH": ("sct_mut_", _mut)},
     [_PERMILLE, "★ 旧的裸切写法已清除"])

# PB5：只删逗号清洗（csv 解析留着）
_mut = sub(SCT_SRC,
           'mem_str.replace("K", "").replace(",", "").strip()',
           'mem_str.replace("K", "").strip()',
           "PB5 drop comma strip")
case("PB5 只删千分位逗号清洗（csv 解析留着）",
     {"SCT_PATH": ("sct_mut_", _mut)},
     [_PERMILLE, "★ 千分位内存不被逗号切碎：1,234,567 K → 1205 MB"])

# ---------------------------------------------------------------------------
# 行为契约
# ---------------------------------------------------------------------------
_mut = sub(SCT_SRC,
           '        direction = "向上" if clicks > 0 else "向下"\n',
           '        direction = "向下" if clicks > 0 else "向上"\n',
           "PB6 flip scroll direction")
case("PB6 把 mouse_scroll 方向判反",
     {"SCT_PATH": ("sct_mut_", _mut)},
     ["mouse_scroll 正数 → 向上"])

_mut = sub(SCT_SRC,
           "            all_wins = [w for w in all_wins if w.title.strip()][:50]\n",
           "            all_wins = list(all_wins)[:50]\n",
           "PB7 drop empty-title filter")
case("PB7 window_list 不再过滤空标题窗口",
     {"SCT_PATH": ("sct_mut_", _mut)},
     ["window_list 无 filter → 过滤掉空标题"])

_mut = sub(SCT_SRC,
           '        pag.hotkey(*keys.split("+"))\n',
           "        pag.hotkey(keys)\n",
           "PB8 no hotkey split")
case("PB8 keyboard_press 不再拆组合键（整串交给 hotkey）",
     {"SCT_PATH": ("sct_mut_", _mut)},
     ["★ 'ctrl+c' 拆成两个键交给 hotkey"])

_mut = sub(SCT_SRC,
           "        proc = subprocess.Popen(argv)\n",
           "        proc = subprocess.Popen(argv, shell=True)\n",
           "PB9 add shell=True")
case("PB9 process_start 改回 shell=True",
     {"SCT_PATH": ("sct_mut_", _mut)},
     ["★ process_start 不再用 shell=True（C1：命令注入）",
      "★ process_start 不传 shell=True"])

_mut = sub(SCT_SRC,
           '        logger.warning("停止信号读取异常，按已停止处理（fail-closed）: %r", _e)\n'
           "        return True\n",
           '        logger.warning("停止信号读取异常: %r", _e)\n'
           "        return False\n",
           "PB10 fail-open aborted")
case("PB10 _aborted 的 fail-closed 改成 fail-open",
     {"SCT_PATH": ("sct_mut_", _mut)},
     ["★ _aborted 回调抛异常 → True（fail-closed：绝不误当成继续干）"])

# ---------------------------------------------------------------------------
# 反向基线
# ---------------------------------------------------------------------------
print("-" * 66)
print("反向基线：原样源码跑判据，不许有任何红项")
_red0, _out0 = run_judge({})
if _red0:
    print("  [FAIL] 未变异的源码居然红了：%s" % _red0)
    FAIL_N += 1
    FAILED_CASES.append("baseline")
else:
    n_pass = len(re.findall(r"\[PASS\]", _out0))
    print("  [OK  ] 未变异源码全绿（%d 项判据）" % n_pass)
    PASS_N += 1

print("\nPERTURB PASS=%d FAIL=%d" % (PASS_N, FAIL_N))
if FAILED_CASES:
    print("失效 case：")
    for c in FAILED_CASES:
        print("  -", c)
    sys.exit(1)
print("=== PERTURB_SYSTEM_CONTROL_B_OK ===")
