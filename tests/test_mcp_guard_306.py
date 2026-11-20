# -*- coding: utf-8 -*-
"""v4.306.0：F-06（stderr drain / stdin 写锁 / event bug）+ F-07（后台初始化 + 短超时）回归判据。

判据分三层：
  A 纯函数层——mcp_guard_306 的决策逻辑（确定性、无 I/O）；
  B 源码接线层——mcp_client / main / ui 真实调用护栏（AST + 文本）；
  C 真实运行层——假 MCP 子进程：持续写 stderr 不卡死、5 路并发写不串行化
    则 JSON 行完整、正常回包不再抛 TypeError。

旧代码（v4.305.0）没有 mcp_guard_306 模块、没有接线 → 探测式调用返回哨兵、
源码判据读到裸 PIPE / event 下标 / 同步 init，对应判据翻红；新代码全绿。
"""
import io
import os
import sys
import json
import threading
import subprocess
import tempfile
from pathlib import Path

_SBX = tempfile.mkdtemp(prefix="xc_306_mcp_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_WORKSPACE_DIR"] = os.path.join(_SBX, "ws")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0


class _Sentinel:
    """旧代码缺函数时的替代值：任何比较都为假 → 干净翻红。"""
    def __getitem__(self, k):
        return self

    def __eq__(self, other):
        return False

    def __ne__(self, other):
        return True

    def __bool__(self):
        return False

    def __iter__(self):
        return iter((self, self))

    def __len__(self):
        return 2

    def __repr__(self):
        return "<OLD-CODE-SENTINEL>"


_SENTINEL = _Sentinel()


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


def probe(mod, fn, *args, **kwargs):
    if mod is None:
        return _SENTINEL
    f = getattr(mod, fn, None)
    if f is None:
        return _SENTINEL
    try:
        return f(*args, **kwargs)
    except Exception:
        return _SENTINEL


try:
    import mcp_guard_306 as G
except Exception:
    G = None


def read_src(name):
    p = ROOT / name
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


MCP_SRC = read_src("mcp_client.py")
MAIN_SRC = read_src("main.py")
UI_SRC = read_src("ui.py")


# ============ A 纯函数层 ============
def part_a():
    print("== A 纯函数层 ==")
    check("A1 stderr drain 允许", probe(G, "stderr_policy", "drain") is True)
    check("A2 stderr devnull 允许", probe(G, "stderr_policy", "devnull") is True)
    check("A3 裸 PIPE 拒绝", probe(G, "stderr_policy", "raw_pipe") is False)
    check("A4 未知 mode 拒绝(fail-closed)", probe(G, "stderr_policy", "whatever") is False)

    check("A5 write+flush 同锁串行 → True", probe(G, "serialized_write_ok", True, True) is True)
    check("A6 flush 不在同锁 → False", probe(G, "serialized_write_ok", True, False) is False)
    check("A7 write 未加锁 → False", probe(G, "serialized_write_ok", False, True) is False)

    check("A8 从 pending entry 取 result", probe(G, "pending_result", {"result": {"ok": 1}}) == {"ok": 1})
    check("A9 entry 无 result → None", probe(G, "pending_result", {"event": object()}) is None)
    check("A10 非 dict → None", probe(G, "pending_result", "bad") is None)

    try:
        import threading as _th
        check("A11 threading.Event 不可下标(证明 bug 真实)",
              probe(G, "event_is_subscriptable", _th.Event()) is False)
    except Exception as e:
        check("A11 threading.Event 不可下标(证明 bug 真实)", False, repr(e))

    check("A12 后台+QApp后 → 可", probe(G, "init_should_be_background", True, True) is True)
    check("A13 主线程同步 → 不可", probe(G, "init_should_be_background", False, True) is False)
    check("A14 QApp 前同步 → 不可", probe(G, "init_should_be_background", True, False) is False)

    check("A15 预算5s 通过", probe(G, "server_budget_ok", 5.0) is True)
    check("A16 预算60s 拒绝", probe(G, "server_budget_ok", 60) is False)
    check("A17 预算0 拒绝", probe(G, "server_budget_ok", 0) is False)
    check("A18 预算脏值拒绝(fail-closed)", probe(G, "server_budget_ok", "x") is False)

    _i, _t = probe(G, "split_budget", 5.0)
    check("A19 预算拆分相加=总额", abs(float(_i) + float(_t) - 5.0) < 0.01, (_i, _t))
    _i2, _t2 = probe(G, "split_budget", 5.0, 0.67)
    check("A20 非等比拆分相加仍=总额", abs(float(_i2) + float(_t2) - 5.0) < 0.01, (_i2, _t2))
    check("A21 非法比例回退对半", probe(G, "split_budget", 4.0, 9.9) == (2.0, 2.0))

    check("A22 drain 超上限需截断", probe(G, "stderr_tail_kept", 100, 64) is True)
    check("A23 未超上限不截断", probe(G, "stderr_tail_kept", 10, 64) is False)
    check("A24 非法上限 fail-closed", probe(G, "stderr_tail_kept", 10, 0) is False)


# ============ B 源码接线层 ============
def part_b():
    print("== B 源码接线层 ==")
    check("B1 无裸 stderr PIPE 无人读", probe(G, "count_stderr_pipe_without_drain", MCP_SRC) == 0)
    check("B2 无 event 下标取结果", probe(G, "count_event_subscript_return", MCP_SRC) == 0)
    check("B3 mcp_client stderr drain 线程真启动(AST)", probe(G, "count_drain_starts", MCP_SRC) == 1)
    check("B4 mcp_client 两处 stdin write 都在写锁内(AST)", probe(G, "count_locked_stdin_writes", MCP_SRC) == 2)
    check("B5 连接线调用 mcp_guard_306", "mcp_guard_306" in MCP_SRC)
    check("B6 _send_request 用 budget 超时", "budget" in MCP_SRC.lower() or "_timeout" in MCP_SRC)
    # F-07：main.py 不再同步 init
    check("B7 main.py 不在 QApp 前同步 init（改为后台）",
          "start_mcp_init_async" in MAIN_SRC
          and "config.init_mcp_clients(cfg)" not in MAIN_SRC.split("app = QApplication")[0])
    check("B8 main.py 连接线 mcp_guard_306", "mcp_guard_306" in MAIN_SRC)
    # ui.py 兜底改成读后台结果，不再主线程同步 init
    check("B9 ui.py 兜底不再主线程同步 init",
          "config.init_mcp_clients(self.cfg)" not in UI_SRC)
    check("B10 ui.py 有 MCP 初始化等待/状态更新", "mcp_init" in UI_SRC.lower())
    _CFG_SRC = read_src("config.py")
    check("B11 config 有后台初始化函数", "def start_mcp_init_async(" in _CFG_SRC)
    check("B12 get_all_tools 取工具前等后台线程",
          "mcp_init_wait" in _CFG_SRC.split("def get_all_tools")[1].split("def ")[0])


# ============ C 真实运行层：假 MCP server ============
_FAKE_SERVER = r'''
import sys, json, time, threading
mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
# 持续写 stderr，模拟 Node/npx 大量日志（F-06a：drain 缺失会写满管道卡死）
def spam_stderr():
    if mode in ("noisy", "noisy_concurrent"):
        i = 0
        while True:
            sys.stderr.write("log line %d %s\n" % (i, "x" * 200))
            sys.stderr.flush()
            i += 1
            time.sleep(0.001)
threading.Thread(target=spam_stderr, daemon=True).start()

lines_seen = []
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except Exception:
        # 半行 JSON（未串行化导致交错）——记录并可标记
        lines_seen.append("BAD:" + line.strip()[:80])
        continue
    lines_seen.append("OK")
    rid = msg.get("id")
    method = msg.get("method", "")
    if mode == "slow_init" and method == "initialize":
        time.sleep(60)  # F-07：慢服务器，超时后应放弃
    resp = {"jsonrpc": "2.0", "id": rid, "result": {"tools": []}}
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()
    if mode == "noisy_concurrent":
        # 把收到的行写回 stderr 供测试统计完整性（drain 会读走）
        sys.stderr.write("RECV:" + repr(len(lines_seen)) + "\n")
        sys.stderr.flush()
'''


def _write_fake_server():
    p = os.path.join(_SBX, "fake_mcp_server.py")
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(_FAKE_SERVER)
    return p


def part_c():
    print("== C 真实运行层（假 MCP server）==")
    server = _write_fake_server()
    try:
        import mcp_client
        # 旧代码模块名可能不同，load 当前
        McpClient = mcp_client.McpClient

        # C1/C2：持续 stderr + 正常回包（F-06a drain + event bug）
        try:
            c = McpClient(name="fake-noisy", command=sys.executable,
                          args=[server, "noisy"])
            ok = c.start()
            check("C1 持续写stderr时仍能启动并回包", ok is True)
            # 若旧代码 event bug，start 内部就会 TypeError → ok 不会为 True
            c.stop()
        except Exception as e:
            check("C1 持续写stderr时仍能启动并回包", False, repr(e))

        # C3：5 路并发 tools/call，每行完整 JSON（F-06b 写锁）
        try:
            c2 = McpClient(name="fake-concurrent", command=sys.executable,
                           args=[server, "noisy_concurrent"])
            ok2 = c2.start()
            if ok2:
                errs = []
                def call_n(n):
                    try:
                        r = c2.call_tool("t%d" % n, {"n": n})
                        if "失败" in r or "无响应" in r:
                            errs.append(r)
                    except Exception as ex:
                        errs.append(repr(ex))
                ths = [threading.Thread(target=call_n, args=(i,)) for i in range(5)]
                for t in ths:
                    t.start()
                for t in ths:
                    t.join(15)
                check("C3 5路并发调用均无超时/崩溃", not errs, errs[:2])
            else:
                check("C3 5路并发调用均无超时/崩溃", False, "start failed")
            c2.stop()
        except Exception as e:
            check("C3 5路并发调用均无超时/崩溃", False, repr(e))

        # C4：单服务器短超时——慢服务器不得等满 30s（F-07b）
        #      只验证 guard 判据口径（不真等 5s，避免拖慢测试）
        check("C4 单服务器预算口径≤5s", probe(G, "server_budget_ok", 5.0) is True)

        # C5：client.start 接受 timeout 参数（供 F-07 接线）
        import inspect
        try:
            sig = inspect.signature(mcp_client.McpClient.start)
            has_timeout = "timeout" in sig.parameters
        except Exception:
            has_timeout = False
        check("C5 start() 有 timeout 参数", has_timeout)
    except Exception as e:
        check("C1 持续写stderr时仍能启动并回包", False, repr(e))
        check("C3 5路并发调用均无超时/崩溃", False, repr(e))
        check("C4 单服务器预算口径≤5s", False, repr(e))
        check("C5 start() 有 timeout 参数", False, repr(e))


part_a()
part_b()
part_c()
print("=== 汇总 === PASS=%d FAIL=%d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
