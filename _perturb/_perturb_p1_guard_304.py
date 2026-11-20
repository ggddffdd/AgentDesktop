# -*- coding: utf-8 -*-
"""v4.304.0 P1 三项护栏的扰动：真实改坏源码，目标判据必须翻红。

九组扰动覆盖：F-01 解密头校验/fail-closed、F-02 scheme/内网/大小上限、
F-03/F-09 受保护名单/敏感组件规则，以及 memory_store/search/tools 三处接线。
所有目标文件均为 CRLF，跨行锚点必须写真实 \\r\\n。
"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard  # noqa: E402

SUITE = os.path.join(ROOT, "tests", "test_p1_guard_304.py")
CR = "\r\n"
CASES = [
    # ---- F-01 ----
    ("p1_guard_304.py", "SQLite 头校验失效（乱码也被当合法库）",
     "    return bool(data) and bytes(data[:len(SQLITE_HEADER)]) == SQLITE_HEADER",
     "    return True  # 扰动：头校验失效", "A4"),

    ("p1_guard_304.py", "fail-closed 失效（解密异常被吞成空值）",
     "    plain = cipher.decrypt(bytes(enc_bytes))",
     "    plain = b''  # 扰动：不解密直接返回空", "A8"),

    ("memory_store.py", "memory_store 解密接线断开（回到只 warning 继续用空库）",
     "                raise RuntimeError(\"记忆库解密失败，已保留原加密文件（未改动）：%s\" % e) from e",
     "                log.warning(\"记忆库解密失败: %s\", e)  # 扰动：fail-closed 接线断开", "D01"),

    # ---- F-02 ----
    ("p1_guard_304.py", "URL 策略整体失效（file:// 与内网全放行）",
     "def url_access_policy(url):" + CR
     + "    \"\"\"返回 (是否允许, 原因)。只放 http/https，且主机不得是本机/内网地址。\"\"\"",
     "def url_access_policy(url):" + CR
     + "    return True, \"\"  # 扰动：策略整体放行", "B2"),

    ("p1_guard_304.py", "内网地址判定失效（ip.is_private 恒假）",
     "    return bool(" + CR
     + "        ip.is_loopback or ip.is_private or ip.is_link_local" + CR
     + "        or ip.is_reserved or ip.is_multicast or ip.is_unspecified" + CR
     + "    )",
     "    return False  # 扰动：内网判定整体失效", "B3"),

    ("p1_guard_304.py", "响应大小上限失效（一次性读全量）",
     "        if total >= cap:" + CR
     + "            return b\"\".join(chunks), True",
     "        if False:  # 扰动：大小上限失效" + CR
     + "            return b\"\".join(chunks), True", "B11"),

    ("search.py", "http_get URL 策略接线断开（web_fetch 又可抓 file://）",
     "    ok, reason = _g304.url_access_policy(url)" + CR
     + "    if not ok:" + CR
     + "        raise ValueError(\"web_fetch 拒绝：%s\" % reason)",
     "    pass  # 扰动：URL 策略接线断开", "D-web_fetch"),

    # ---- F-03/F-09 ----
    ("p1_guard_304.py", "受保护运行时名单失效（config.json 可写）",
     "    if name in PROTECTED_RUNTIME_NAMES:" + CR
     + "        return True",
     "    if False:  # 扰动：受保护名单失效" + CR
     + "        return True", "C1"),

    ("tools.py", "write_file 受保护接线断开（又能覆盖 config.json）",
     "    if _g304.is_protected_runtime_path(p, WORKSPACE_DIR):" + CR
     + "        return \"拒绝：目标是运行时受保护文件（配置/会话/记忆/任务/数据库），禁止写入。\"",
     "    if False:  # 扰动：write_file 受保护接线断开" + CR
     + "        return \"拒绝：目标是运行时受保护文件（配置/会话/记忆/任务/数据库），禁止写入。\"", "D-write_file"),
]


def run_suite():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, SUITE], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def main():
    files = sorted({os.path.join(ROOT, rel) for rel, *_ in CASES})
    _perturb_guard.arm(files)
    originals = {p: io.open(p, encoding="utf-8", newline="").read() for p in files}
    baseline = run_suite()
    if "FAIL=0" not in baseline:
        print("BASELINE RED：套件本身未全绿")
        print(baseline[-1200:])
        print("PERTURB PASS=0 FAIL=%d" % len(CASES))
        return 1
    npass = nfail = 0
    for rel, label, old, new, want in CASES:
        path = os.path.join(ROOT, rel)
        src = io.open(path, encoding="utf-8", newline="").read()
        if src.count(old) != 1:
            print("MISS %s：锚点数量=%d" % (label, src.count(old)))
            nfail += 1
            continue
        try:
            io.open(path, "w", encoding="utf-8", newline="").write(
                src.replace(old, new, 1))
            out = run_suite()
        finally:
            io.open(path, "w", encoding="utf-8", newline="").write(
                originals[path])
        red = [line for line in out.splitlines() if "[FAIL]" in line]
        if "PASS=" in out and any(want in line for line in red):
            print("PASS %s → %s" % (label, want))
            npass += 1
        else:
            print("MISS %s：期望 %s，实际 %s"
                  % (label, want, red[:3] or out[-200:]))
            nfail += 1
    for path, original in originals.items():
        if io.open(path, encoding="utf-8", newline="").read() != original:
            print("FAIL 源码未恢复：%s" % os.path.basename(path))
            nfail += 1
    print("PERTURB PASS=%d FAIL=%d" % (npass, nfail))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
