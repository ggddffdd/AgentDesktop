# -*- coding: utf-8 -*-
"""v4.306.0 MCP F-06/F-07 判据的真实源码扰动：目标判据必须翻红。

十组扰动覆盖：
  纯函数层：stderr_policy / pending_result / init_should_be_background / server_budget_ok
  接线层：mcp_client event bug 复活 / 去掉写锁 / main 改回同步 init /
          config 去后台函数 / get_all_tools 去等待

注意换行混合：mcp_guard_306/config 是 LF，mcp_client/main/ui 是 CRLF。
io.open(newline="") 保留原换行，锚点按各文件 NL 拼接（build 用对应 NL join）。
"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard  # noqa: E402

SUITE = os.path.join(ROOT, "tests", "test_mcp_guard_306.py")
CR = "\r\n"
LF = "\n"
G = "mcp_guard_306.py"
C = "mcp_client.py"
CFG = "config.py"
MAIN = "main.py"

CASES = [
    # ---- 纯函数：stderr 策略恒放行 → A3 裸 PIPE 应红 ----
    (G, "stderr 策略恒放行",
     ["    return mode in _ALLOWED_STDERR"],
     "    return True  # 扰动：裸 PIPE 也放行", "A3", LF),
    # ---- 纯函数：pending 结果取回恒 None → A8 红 ----
    (G, "pending 结果取回失效",
     ["    if not isinstance(pending_entry, dict):", "        return None",
      "    return pending_entry.get(\"result\")"],
     "    return None  # 扰动：取不到 server 响应", "A8", LF),
    # ---- 纯函数：后台决策恒真 → A13 主线程同步也应"可" → 红 ----
    (G, "后台初始化决策恒真",
     ["    return bool(in_background) and bool(after_qapplication)"],
     "    return True  # 扰动：同步阻塞也算通过", "A13", LF),
    # ---- 纯函数：预算校验恒真 → A16 60s 也应通过 → 红 ----
    (G, "预算校验恒真",
     ["    try:", "        return 0 < float(total_sec) <= MAX_SERVER_BUDGET_SEC",
      "    except (TypeError, ValueError):", "        return False"],
     "    return True  # 扰动：任意预算都放行", "A16", LF),
    # ---- 接线：event bug 复活 → B2 / C1 红 ----
    (C, "event 下标取结果复活（崩溃 bug）",
     ["        # F-06c：reader 把响应写在 entry[\"result\"]，event 只是唤醒信号。",
      "        return _guard.pending_result(entry)"],
     "        # 扰动：退回 event 下标取结果" + CR
     + "        return event[\"result\"]", "B2", CR),
    # ---- 接线：去掉写锁 → B4 红（锚点带唯一前缀，区分 _send_request 与 _send_notification）----
    (C, "stdin 写锁移除",
     ["            # F-06b：整条 JSON 的 write + flush 必须同锁，否则并发交错成半行。",
      "            with self._write_lock:",
      "                self.process.stdin.write(line.encode(\"utf-8\"))",
      "                self.process.stdin.flush()"],
     "            # 扰动：写不串行化" + CR
     + "            self.process.stdin.write(line.encode(\"utf-8\"))" + CR
     + "            self.process.stdin.flush()", "B4", CR),
    # ---- 接线：main 改回同步 init → B7 红 ----
    (MAIN, "main 改回 QApp 前同步 init",
     ["    config.start_mcp_init_async(cfg, budget_sec=_mcp_budget)"],
     "    config.init_mcp_clients(cfg)  # 扰动：同步阻塞启动", "B7", CR),
    # ---- 接线：config 去掉后台函数入口 → B11 红 ----
    (CFG, "config 后台初始化入口改名",
     ["def start_mcp_init_async(cfg, budget_sec=None):"],
     "def start_mcp_init_async_OLD(cfg, budget_sec=None):  # 扰动：入口丢失", "B11", LF),
    # ---- 接线：get_all_tools 去掉等待 → B12 红 ----
    # 注意：config.py 经 v4.306 编辑后为 CRLF，锚点用 CR 拼接。
    (CFG, "get_all_tools 去掉后台等待",
     ["    try:", "        mcp_init_wait(timeout=5.0)",
      "    except Exception:", "        pass", "    tools = list(TOOL_DEFS)"],
     "    tools = list(TOOL_DEFS)" + CR + "    # 扰动：不等后台，首次对话漏 MCP 工具", "B12", CR),
    # ---- 接线：stderr drain 线程移除 → B3 红 ----
    (C, "stderr drain 线程移除",
     ["        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)",
      "        self._stderr_thread.start()"],
     "        pass  # 扰动：不 drain，stderr 写满卡死", "B3", CR),
]


def build(old_lines, new, nl):
    return nl.join(old_lines), new


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
        print(baseline[-1500:])
        print("PERTURB PASS=0 FAIL=%d" % len(CASES))
        return 1
    npass = nfail = 0
    for rel, label, old_lines, new, want, nl in CASES:
        path = os.path.join(ROOT, rel)
        src = io.open(path, encoding="utf-8", newline="").read()
        old, new = build(old_lines, new, nl)
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
        if io.open(path, "r", encoding="utf-8", newline="").read() != original:
            print("FAIL 源码未恢复：%s" % os.path.basename(path))
            nfail += 1
    print("PERTURB PASS=%d FAIL=%d" % (npass, nfail))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
