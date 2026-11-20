# -*- coding: utf-8 -*-
"""v4.307 安全修复判据的真实源码扰动验证（改坏必红）。

每组扰动改一处真实源码 → 对应 want 判据必须翻红；改不红（假绿）即 FAIL。
"""
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _perturb_guard

SUITE = os.path.join(ROOT, "tests", "test_security_guard_307.py")
CR = "\r\n"
LF = "\n"

# (相对路径, 标签, 原行列表, 替换文本, want判据, 换行)
CASES = [
    # F-02: DNS 解析落地内网校验失效 → A11/A12 + C 层解析红
    ("security_guard_307.py", "DNS 内网判定恒放行",
     ["    for ip in ips:", "        if ip_is_forbidden(ip):",
      "            return False, ips, \"解析到本机/内网保留地址：%s\" % ip"],
     "    for ip in ips:  # 扰动：不查内网\n        pass", "A11", LF),
    # F-02: download_bytes 恢复裸 urlopen → B3 红
    ("search.py", "download 恢复裸取",
     ["        data, _truncated = _g304.read_response_capped(resp)",
      "        return data"],
     "        return urllib.request.urlopen(url).read()  # 扰动：裸取", "B3", CR),
    # F-02: http_get 去掉 peer 复核 → B5 红
    ("search.py", "去掉 peer 复核",
     ["        peer = _peer_ip_of(resp)",
      "        if not _g307.peer_ip_ok(peer):",
      "            raise ValueError(\"web_fetch 拒绝：连接对端地址未通过复核（%r）\" % peer)"],
     "        peer = None  # 扰动：跳过 peer 复核", "B5", CR),
    # F-01: 明文+enc 错口令不 fail-closed → B8 红（两处 plain_db_decision 调用都拿掉）
    ("memory_store.py", "明文库去掉口令校验",
     ["            decision = _g307.plain_db_decision(enc_exists, plain_exists, False)",
      "            try:",
      "                _plain = _g304.fail_closed_decrypt(_cipher, open(ep, \"rb\").read())",
      "                decision = _g307.plain_db_decision(enc_exists, plain_exists, True)"],
     "            decision = \"allow\"  # 扰动：不做决策\n"
     + CR + "            try:\n"
     + CR + "                _plain = _g304.fail_closed_decrypt(_cipher, open(ep, \"rb\").read())\n"
     + CR + "                decision = \"allow\"", "B8", CR),
    # F-03: 恢复 *.db 一刀切 → B12 红
    ("p1_guard_304.py", "恢复 *.db 全局拦",
     ["        if _g307.protected_db_name(name):", "            return True"],
     "        if name.endswith(\".db\"):  # 扰动：一刀切\n            return True", "B12", CR),
    # F-05: legacy 恢复扫根 → B11 红
    ("tools.py", "legacy 恢复扫根",
     ["    snap_root = _ws2", "    before = snapshot_workspace(snap_root)"],
     "    snap_root = _ws2  # 扰动：回退扫根\n"
     + CR + "    before = snapshot_workspace(WORKSPACE_DIR)", "B11", CR),
    # F-07: budget 死参数复活 → B14 红
    ("config.py", "budget 死参数",
     ["            init_mcp_clients(cfg, budget=budget)"],
     "            init_mcp_clients(cfg)  # 扰动：budget 不传", "B14", CR),
    # 纯函数: atomic_write_ok 恒真 → A31 红（False 应判假）
    ("security_guard_307.py", "原子写判据恒真",
     ["def atomic_write_ok(uses_tmp_and_replace):",
      "    \"\"\"写入是否为临时文件 + os.replace 原子替换（防止半写损坏运行时文件）。\"\"\"",
      "    return bool(uses_tmp_and_replace)"],
     "def atomic_write_ok(uses_tmp_and_replace):\n"
     + LF + "    \"\"\"写入是否为临时文件 + os.replace 原子替换（防止半写损坏运行时文件）。\"\"\"\n"
     + LF + "    return True  # 扰动：恒真", "A31", LF),
]


def run_suite():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, SUITE], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def main():
    files = sorted({os.path.join(ROOT, rel) for rel, *_ in CASES})
    _perturb_guard.arm(files)
    originals = {p: io.open(p, encoding="utf-8", newline="").read() for p in files}
    baseline = run_suite()
    if "FAIL=0" not in baseline:
        print("BASELINE RED：套件本身未全绿")
        print(baseline[-1500:])
        print("PERTURB PASS=0 FAIL=%d" % len(CASES))
        return 1
    npass = nfail = 0
    for rel, label, old_lines, new, want, nl in CASES:
        path = os.path.join(ROOT, rel)
        src = io.open(path, encoding="utf-8", newline="").read()
        old = nl.join(old_lines)
        if src.count(old) != 1:
            print("MISS %s：锚点数量=%d" % (label, src.count(old)))
            nfail += 1
            continue
        try:
            io.open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new, 1))
            out = run_suite()
        finally:
            io.open(path, "w", encoding="utf-8", newline="").write(originals[path])
        red = [line for line in out.splitlines() if "[FAIL]" in line]
        if "PASS=" in out and any(want in line for line in red):
            print("PASS %s → %s" % (label, want))
            npass += 1
        else:
            print("MISS %s：期望 %s，实际 %s" % (label, want, red[:3] or out[-200:]))
            nfail += 1
    for path, original in originals.items():
        if io.open(path, "r", encoding="utf-8", newline="").read() != original:
            print("FAIL 源码未恢复：%s" % os.path.basename(path))
            nfail += 1
    print("PERTURB PASS=%d FAIL=%d" % (npass, nfail))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
