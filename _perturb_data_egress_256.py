# -*- coding: utf-8 -*-
"""v4.256.0 扰动脚本：数据出口四件套（P0-1 / P0-2 / P1-1 / P1-2）。

反向照妖镜：逐个把改动打回「未修」状态，跑 tests/test_data_egress_256.py，
确认判据必红（改坏必红、零哑弹）。SKIP/MISS = 哑弹，判据白写。

V1  桥接不包裹          → 网页正文重新裸拼，越狱闭合标签重新可用
V2  注入点不调用        → main.py 继续自己拼块，browser_bridge 写得再对也白搭
V3  兜底不中和          → 边界模块不可用/异常时退回裸正文（fail-closed 被改成 open）
V4  诊断包退回顶层判键  → 嵌套 model_profiles 的真 key 重新进包
V5  _fmt_value 不递归   → 同上（递归函数本身失效）
V6  出口不接凭据规则    → sk-/Bearer 重新原样发 API（回到「两套规则」）
V7  主出口退回旧口径    → tools.exec_tool 主分支重新只脱路径
V8  MCP 分支退回旧口径  → 兜底分支漏网（同一个 bug 换条路径复发）
V9  E4 判据恒 False     → `type config.json` / `cat id_rsa` 重新看不见
V10 E4 判据放宽         → 普通命令被误伤（`dir D:\tmp` 也报敏感，误报即噪音）
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
BB = os.path.join(ROOT, "browser_bridge.py")
MN = os.path.join(ROOT, "main.py")
DE = os.path.join(ROOT, "diagnostic_export.py")
TC = os.path.join(ROOT, "tool_contract.py")
TL = os.path.join(ROOT, "tools.py")
JUDGE = os.path.join(ROOT, "tests", "test_data_egress_256.py")

CASES = [
    # ---- E1 桥接 ----
    ("V1 桥接不包裹", BB,
     '        return _ub.wrap_untrusted(body, "browser_bridge")',
     '        return body  # 扰动：不包裹'),
    ("V2 注入点不调用", MN,
     "                        block = build_inject_block(p)",
     "                        block = None  # 扰动：注入点没接"),
    ("V3 兜底不中和", BB,
     '    safe = re.sub(r"<\\s*/?\\s*untrusted", "&lt;untrusted",\n'
     '                  str(body or ""), flags=re.IGNORECASE)',
     '    safe = str(body or "")  # 扰动：兜底不中和'),
    # ---- E2 诊断包 ----
    ("V4 诊断包退回顶层判键", DE,
     "        # v4.256.0（P0-2）：一律走 _fmt_value（递归判子键），不再只判顶层键名。\n"
     '        lines.append(f"{key:16}: {_fmt_value(key, val)}")',
     '        lines.append(f"{key:16}: {str(val)[:200]}")  # 扰动：退回顶层判键'),
    ("V5 _fmt_value 不递归", DE,
     '    if isinstance(val, dict):\n'
     '        if not val:\n'
     '            return "{}"\n'
     '        parts = ["%s: %s" % (k, _fmt_value(k, v, depth + 1))\n'
     '                 for k, v in list(val.items())[:25]]\n'
     '        return "{" + "; ".join(parts) + "}"',
     '    if isinstance(val, dict):  # 扰动：不递归\n'
     '        if not val:\n'
     '            return "{}"\n'
     '        return "{" + "; ".join("%s: %s" % (k, str(v)[:60])\n'
     '                               for k, v in list(val.items())[:25]) + "}"'),
    # ---- E3 发 API 脱敏 ----
    ("V6 出口不接凭据规则", TC,
     "        _after = _ms(out)",
     "        _after = out  # 扰动：不接凭据规则"),
    ("V7 主出口退回旧口径", TL,
     "            _tr.msg = sanitize_for_model(_tr.msg)\n"
     "            _tr.data = sanitize_for_model_recursive(_tr.data)",
     "            _tr.msg = _mask_sensitive(_tr.msg)  # 扰动：退回旧口径\n"
     "            _tr.data = sanitize_for_model_recursive(_tr.data)"),
    ("V8 MCP 分支退回旧口径", TL,
     "    _tr.msg = sanitize_for_model(_tr.msg)\n"
     "    # v4.224：执行后验证（查副作用是否真生效；只降级不升级）",
     "    _tr.msg = _mask_sensitive(_tr.msg)  # 扰动：MCP 分支退回旧口径\n"
     "    # v4.224：执行后验证（查副作用是否真生效；只降级不升级）"),
    # ---- E4 敏感文件 ----
    ("V9 E4 判据恒 False", TL,
     "    if not command or not isinstance(command, str):\n"
     "        return False",
     "    if True:  # 扰动：判据恒 False\n"
     "        return False"),
    ("V10 E4 判据放宽（误伤）", TL,
     "    low = cmd.lower()\n"
     "    if not _CMD_READ_VERB_RE.search(low) and not any(\n"
     "            z in cmd for z in _CMD_READ_VERB_ZH):\n"
     "        return False\n"
     "    for _tok in _CMD_PATH_TOKEN_RE.findall(cmd):\n"
     "        _t = _tok.strip().strip('\"\\'').strip()\n"
     "        if not _t:\n"
     "            continue\n"
     "        if _is_sensitive_file(_t):\n"
     "            return True\n"
     "    return False",
     "    low = cmd.lower()\n"
     "    if not re.search(r'(type|cat|dir|echo|git|python|ping)', low):  # 扰动：动词闸放宽\n"
     "        return False\n"
     "    for _tok in _CMD_PATH_TOKEN_RE.findall(cmd):\n"
     "        _t = _tok.strip().strip('\"\\'').strip()\n"
     "        if not _t:\n"
     "            continue\n"
     "        if True:  # 扰动：任意 token 都算命中\n"
     "            return True\n"
     "    return False"),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [BB, MN, DE, TC, TL]
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
                print("---- judge output ----\n" + out[:1500])
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
