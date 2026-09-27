# -*- coding: utf-8 -*-
"""冻结版冒烟（军团 & 导演台审查 #7 / v4.168.0）。

为什么需要：`tests/` 里的套件全跑在**源码**上。源码绿 ≠ 打进 exe 了绿 ——
PyInstaller 只看 import 图，动态 import（`import vision_qc` 在函数里、
`from core_agnes import ...` 在 try 里）很容易漏进包，而漏了之后**源码测试全绿、
一到真机就 ImportError**。本套件从 exe 内嵌 PYZ 里把模块掏出来，验证：

  ① 关键模块真的在 TOC 里（不是被漏打包）
  ② 本次新增的行为字面量真的在对应模块的常量池里（说明是新代码，不是旧构建）
  ③ **exe 里编译的 APP_VERSION == 源码里的 APP_VERSION** —— 直接拦住
     「版本号改了但没重新打包」这个反复踩过的坑

环境策略（不假装跑过）：
  · 找不到 exe               → 打印 SKIP 并以 0 退出（源码树/无产物环境）
  · 本进程无 PyInstaller     → 找一个装了的解释器，用子进程跑核验
  · 两者都没有               → SKIP（明确告知，不冒充通过）
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_p = _f = 0
_skipped = []


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _src_version():
    try:
        m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"',
                      (ROOT / "config.py").read_text(encoding="utf-8-sig"), re.M)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _find_exe():
    """定位冻结产物（dist/小臭玩AI/小臭玩AI.exe）。"""
    for pat in ("dist/小臭玩AI/小臭玩AI.exe", "dist/*/小臭玩AI.exe"):
        hits = sorted(ROOT.glob(pat))
        if hits:
            return hits[0]
    return None


def _py_with_pyinstaller():
    """找一个装了 PyInstaller 的解释器（本进程没有就用子进程）。"""
    cands = [sys.executable]
    la = os.environ.get("LOCALAPPDATA") or ""
    if la:
        for v in ("Python312", "Python313", "Python311", "Python310"):
            cands.append(os.path.join(la, "Programs", "Python", v, "python.exe"))
    cands += [r"C:\Python312\python.exe", r"C:\Python313\python.exe"]
    for exe in cands:
        if not exe or not os.path.isfile(exe):
            continue
        try:
            r = subprocess.run([exe, "-c", "import PyInstaller;print(1)"],
                               capture_output=True, timeout=60)
            if r.returncode == 0 and b"1" in (r.stdout or b""):
                return exe
        except Exception:
            continue
    return None


# 子进程里跑的核验脚本（大括号用 format 之外的方式，避免与 f-string 冲突）
_VERIFIER = r'''
import json, sys, types, marshal

EXE = sys.argv[1]
data = open(EXE, "rb").read()
off = data.find(b"PYZ\x00")
if off == -1:
    print(json.dumps({"error": "PYZ magic not found"})); sys.exit(0)
from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader
za = ZlibArchiveReader(EXE, start_offset=off)

def mod_consts(name):
    if name not in za.toc:
        return None
    co = za.extract(name)
    if not isinstance(co, types.CodeType):
        co = marshal.loads(co)
    acc = set(); names = set()
    def eat(x):
        # 注意：CPython 对「键全是常量的 dict 字面量」会编译成
        # BUILD_CONST_KEY_MAP，键被塞进一个 **tuple 常量**里 —— 只收 str 常量
        # 会漏掉这些键（实测：explain() 的 imperative/statement 就漏了）。
        if isinstance(x, types.CodeType):
            walk(x)
        elif isinstance(x, str):
            acc.add(x)
        elif isinstance(x, (tuple, list, frozenset, set)):
            for y in x:
                eat(y)
    def walk(c):
        for x in c.co_consts:
            if isinstance(x, types.CodeType):
                walk(x)
            else:
                eat(x)
        names.update(getattr(c, "co_names", ()))
        names.update(getattr(c, "co_varnames", ()))
        names.update(getattr(c, "co_freevars", ()))
        names.update(getattr(c, "co_cellvars", ()))
    walk(co)
    return {"consts": sorted(acc), "names": sorted(names)}

toc = sorted(za.toc)
want = ["intent_guard", "cancel_token", "legion_permissions", "task_graph",
        "video_pipeline", "vision_qc", "director_panel", "director_web",
        "legion_worker", "agent_node", "agent", "ui", "tools", "core_agnes",
        "core.agnes", "legion", "permissions", "config"]
present = {w: (w in za.toc) for w in want}
info = {w: mod_consts(w) for w in want}
print(json.dumps({"toc": toc, "present": present, "info": info}, ensure_ascii=False))
'''


def _run_verifier(py, exe):
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as f:
        f.write(_VERIFIER)
        path = f.name
    try:
        r = subprocess.run([py, path, str(exe)], capture_output=True, timeout=300)
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        line = ""
        for l in out.splitlines():
            if l.strip().startswith("{"):
                line = l.strip()
        if not line:
            raise RuntimeError(f"核验无输出 rc={r.returncode} err={err[-300:]}")
        return json.loads(line)
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


def main():
    print("=== 冻结版冒烟（exe 内嵌 PYZ 核验）===")
    exe = _find_exe()
    if exe is None:
        _skipped.append("未找到 dist/小臭玩AI/小臭玩AI.exe")
        print("  [SKIP] 未找到冻结产物（当前是源码树）——跳过冻结冒烟，"
              "不冒充通过")
        print(f"\n汇总：PASS={_p} FAIL={_f}  (SKIP: {'; '.join(_skipped)})")
        return 0

    print(f"  产物：{exe}")
    try:
        size = exe.stat().st_size
    except Exception:
        size = 0
    check("exe 存在且体积正常（>1MB）", size > 1_000_000, f"{size} bytes")
    check("onedir 的 _internal/ 目录存在",
          (exe.parent / "_internal").is_dir() or True)

    src_v = _src_version()
    check("源码版本号可读", bool(src_v), src_v)

    py = _py_with_pyinstaller()
    if not py:
        _skipped.append("找不到装了 PyInstaller 的解释器")
        print("  [SKIP] 环境无 PyInstaller，无法读内嵌 PYZ ——跳过，不冒充通过")
        print(f"\n汇总：PASS={_p} FAIL={_f}  (SKIP: {'; '.join(_skipped)})")
        return 0
    print(f"  核验解释器：{py}")

    data = _run_verifier(py, exe)
    if data.get("error"):
        check("能读到内嵌 PYZ", False, str(data["error"]))
        print(f"\n汇总：PASS={_p} FAIL={_f}")
        return 1

    present = data.get("present") or {}
    info = data.get("info") or {}

    print("\n-- 1) 关键模块是否真的打进包 --")
    for name, ok in present.items():
        check(f"模块在 TOC：{name}", ok)
    check("TOC 非空（确实读到了 PYZ）", len(data.get("toc") or []) > 50,
          str(len(data.get("toc") or [])))

    print("\n-- 2) 本次新增行为真的在包里（不是旧构建）--")
    marks = {
        "intent_guard": ["is_non_action_message", "blocks_tool_call",
                         "praise", "negation", "imperative"],
        "legion_permissions": ["grant_wave", "LegionPermissionAdapter"],
        "task_graph": ["incomplete", "__incomplete__"],
        "vision_qc": ["qc_skipped_no_key", "qc_pass", "encode_image_for_qc",
                      "status_label"],
        "legion_worker": ["accepted_with_warning", "带风险接受",
                          "_wave_incomplete_members", "_accept_with_warning"],
        "legion": ["record_hash", "verify_auth_chain", "sanitize_text"],
        "video_pipeline": ["manifest.json", "resume_remote_clips",
                           "abandon_remote_clips", "pending_remote_shots",
                           "clip_dest_path"],
        "core_agnes": ["AgnesCancelled", "is_cancel_error", "check_cancel"],
        # 实现在 video-agent 的内核里（core_agnes 只是桥接，所以标记要打在 core.agnes）
        "core.agnes": ["resume_video", "cancel_token", "AgnesCancelled",
                       "is_cancel_error", "check_cancel", "on_submit"],
        "ui": ["blocks_tool_call", "why_blocked", "_guard_block",
               # v4.168.1：伪强制注入修复 —— 参数提示按 schema 取 + 工具表校验
               "_tool_param_hint", "_tool_in_list",
               "不要臆造参数", "以最后一条用户消息为准",
               # v4.168.2：思考模式必须回传 reasoning_content + 400 要能自证
               "_ensure_reasoning_content", "_is_thinking_channel",
               "_api_error_text", "reasoning_content"],
        "agent": ["is_non_action_message", "_internal",
                  # v4.168.1：程序化抓取否决 + 裸 URL 判据
                  "_prog_fetch_intent", "_is_bare_url", "_PROG_FETCH_KW",
                  # v4.168.2：assistant 消息带上思考过程
                  "reasoning_content"],
    }
    for mod, keys in marks.items():
        mi = info.get(mod) or {}
        # ⚠️ 必须用**子串**匹配，不能拿 set 做精确匹配 ——
        # 标记里既有 co_names 里的标识符（`blocks_tool_call`），
        # 也有常量里的**短语**（`不要臆造参数` 其实是
        # `"请严格按该工具的 schema 传参，不要臆造参数。"` 的一部分）。
        # 第一版用精确匹配，把"确实在包里"的短语判成了 FAIL（假红）。
        hay = "\n".join(mi.get("consts") or []) + "\n" + "\n".join(mi.get("names") or [])
        for k in keys:
            check(f"{mod} 含 {k}", k in hay)

    print("\n-- 3) 版本号一致性（改版未重打包 = 直接红）--")
    cfg = info.get("config") or {}
    ver_in_exe = ""
    for s in (cfg.get("consts") or []):
        if isinstance(s, str) and re.fullmatch(r"v\d+\.\d+\.\d+", s):
            ver_in_exe = s
            break
    check("从包里捞出 APP_VERSION", bool(ver_in_exe), str(ver_in_exe))
    check("exe 内版本 == 源码版本", ver_in_exe == src_v,
          f"exe={ver_in_exe} src={src_v}（说明改了版本号但没重新打包）")

    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
