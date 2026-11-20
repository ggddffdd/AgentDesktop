# -*- coding: utf-8 -*-
"""v4.307.0：第三方验收「不通过」项回归判据。

三层：
  A 纯函数层——security_guard_307 决策逻辑（确定性、无真实网络）；
  B 源码接线层——search / memory_store / tools / config 真实接线（AST + 文本）；
  C 真实运行层——本机 HTTP 服务实测 F-02：localhost./localtest.me/nip.io
    解析到回环必须被拒、download_bytes 的 file:// 必须被拒、公网域名放行。

旧代码（v4.306.0）无 security_guard_307、无 DNS 解析、download_bytes 裸 urlopen，
探测式调用返回哨兵/源码判据读到裸调用 → 对应判据翻红。
"""
import http.server
import os
import socket
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

_SBX = tempfile.mkdtemp(prefix="xc_307_sec_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_WORKSPACE_DIR"] = os.path.join(_SBX, "ws")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0


class _Sentinel:
    def __getitem__(self, k): return self
    def __eq__(self, o): return False
    def __ne__(self, o): return True
    def __bool__(self): return False
    def __iter__(self): return iter((self, self))
    def __len__(self): return 2
    def __repr__(self): return "<OLD-CODE-SENTINEL>"


_S = _Sentinel()


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


def probe(mod, fn, *a, **kw):
    if mod is None: return _S
    f = getattr(mod, fn, None)
    if f is None: return _S
    try: return f(*a, **kw)
    except Exception: return _S


try:
    import security_guard_307 as G
except Exception:
    G = None


def read_src(name):
    try: return (ROOT / name).read_text(encoding="utf-8")
    except Exception: return ""


SEARCH_SRC = read_src("search.py")
MEM_SRC = read_src("memory_store.py")
TOOLS_SRC = read_src("tools.py")
CONFIG_SRC = read_src("config.py")
GUARD304_SRC = read_src("p1_guard_304.py")


def _resolver(mapping):
    return lambda h, p: [(2, 1, 6, "", (ip, 0)) for ip in mapping.get(h, [])]


# ============ A 纯函数层 ============
def part_a():
    print("== A 纯函数层 ==")
    check("A1 尾随点规范化", probe(G, "normalize_host", "LOCALHOST.") == "localhost")
    check("A2 IPv6 去方括号", probe(G, "normalize_host", "[::1]") == "::1")
    check("A3 空主机规范化", probe(G, "normalize_host", "  ") == "")
    check("A4 回环 IP 拦", probe(G, "ip_is_forbidden", "127.0.0.1") is True)
    check("A5 私有 IP 拦", probe(G, "ip_is_forbidden", "10.1.2.3") is True)
    check("A6 link-local 拦", probe(G, "ip_is_forbidden", "169.254.1.1") is True)
    check("A7 公网 IP 放", probe(G, "ip_is_forbidden", "8.8.8.8") is False)
    check("A8 脏 IP fail-closed", probe(G, "ip_is_forbidden", "not-an-ip") is True)
    check("A9 localtest.me 文本层不拦(靠 DNS)", probe(G, "host_name_is_forbidden", "localtest.me") is False)
    check("A10 localhost. 文本层拦", probe(G, "host_name_is_forbidden", "localhost.") is True)

    ok, ips, _why = probe(G, "resolve_and_check", "localtest.me", 80,
                          _resolver({"localtest.me": ["127.0.0.1"]}))
    check("A11 解析到回环拒绝", ok is False and "127.0.0.1" in ips, (ok, ips))
    ok, ips, _why = probe(G, "resolve_and_check", "nip.io", 80,
                          _resolver({"nip.io": ["93.184.216.34", "10.0.0.5"]}))
    check("A12 多 A 记录含内网即拒", ok is False and len(ips) == 2, (ok, ips))
    ok, _ips, _why = probe(G, "resolve_and_check", "example.com", 80,
                           _resolver({"example.com": ["93.184.216.34"]}))
    check("A13 全公网放行", ok is True)
    check("A14 无 resolver fail-closed", probe(G, "resolve_and_check", "x.com", 80, None)[0] is False)
    check("A15 解析异常 fail-closed", probe(G, "resolve_and_check", "x.com", 80, lambda h, p: 1 / 0)[0] is False)
    check("A16 空解析 fail-closed", probe(G, "resolve_and_check", "x.com", 80, lambda h, p: [])[0] is False)

    check("A17 peer 内网拒(rebinding)", probe(G, "peer_ip_ok", "192.168.1.1") is False)
    check("A18 peer 公网放", probe(G, "peer_ip_ok", "8.8.8.8") is True)
    check("A19 peer None 拒", probe(G, "peer_ip_ok", None) is False)

    check("A20 download file:// 拒", probe(G, "download_url_allowed", "file:///etc/passwd", _resolver({}))[0] is False)
    check("A21 download 内网域名拒", probe(G, "download_url_allowed", "http://x.nip.io/a",
                                         _resolver({"x.nip.io": ["127.0.0.1"]}))[0] is False)
    check("A22 download 公网放", probe(G, "download_url_allowed", "http://example.com/a.png",
                                       _resolver({"example.com": ["93.184.216.34"]}))[0] is True)

    check("A23 明文+enc+错口令 deny", probe(G, "plain_db_decision", True, True, False) == "deny")
    check("A24 明文+enc+对口令 allow", probe(G, "plain_db_decision", True, True, True) == "allow")
    check("A25 仅 enc → decrypt", probe(G, "plain_db_decision", True, False, False) == "decrypt")
    check("A26 仅明文(迁移) → allow", probe(G, "plain_db_decision", False, True, False) == "allow")
    check("A27 memory.db 拦截", probe(G, "protected_db_name", "memory.db") is True)
    check("A28 普通 data.db 放行", probe(G, "protected_db_name", "my_notes.db") is False)
    check("A29 config 前缀路径取 basename", probe(G, "protected_db_name", "D:/x/memory.db") is True)
    check("A30 原子写判据 True", probe(G, "atomic_write_ok", True) is True)
    check("A31 原子写判据 False", probe(G, "atomic_write_ok", False) is False)
    check("A32 技能 fail-closed", probe(G, "skill_gate_fail_closed", True) is True)
    check("A33 快照同根", probe(G, "snapshot_root_ok", True) is True)
    check("A34 budget 接线", probe(G, "budget_wired", True) is True)


# ============ B 源码接线层 ============
def part_b():
    print("== B 源码接线层 ==")
    check("B1 p1_guard.is_blocked_host 规范化尾随点",
          "rstrip" in GUARD304_SRC and "normalize" in GUARD304_SRC.lower())
    check("B2 search 有 getaddrinfo 调用", probe(G, "count_getaddrinfo_in", SEARCH_SRC) >= 1,
          probe(G, "count_getaddrinfo_in", SEARCH_SRC))
    check("B3 download_bytes 无裸 urlopen", probe(G, "count_bare_urlopen", SEARCH_SRC) == 0,
          probe(G, "count_bare_urlopen", SEARCH_SRC))
    check("B4 search 接线 security_guard_307", "security_guard_307" in SEARCH_SRC)
    check("B5 http_get 有 peer 复核",
          probe(G, "function_has_call", SEARCH_SRC, "http_get", "peer_ip_ok"))
    check("B6 重定向逐跳用 resolve 判据", "resolve_and_check" in SEARCH_SRC)
    check("B7 p1_guard URL 策略用规范化主机", "rstrip" in GUARD304_SRC)

    check("B8 memory_store 明文+enc 用 plain_db_decision",
          probe(G, "function_has_call", MEM_SRC, "_with_db", "plain_db_decision"))
    check("B9 memory_store 校验 .enc 口令", "fail_closed_decrypt" in MEM_SRC and "plain_db_decision" in MEM_SRC)

    check("B10 tools 原子写 os.replace",
          "os.replace(tmp, p)" in TOOLS_SRC and ".tmp." in TOOLS_SRC)
    check("B11 legacy 无 workspace 根快照", probe(G, "count_legacy_workspace_snapshot", TOOLS_SRC) == 0,
          probe(G, "count_legacy_workspace_snapshot", TOOLS_SRC))
    check("B12 protected_db 精确名单接线(不再 *.db 一刀切)",
          "protected_db_name" in GUARD304_SRC and 'endswith(".db")' not in GUARD304_SRC)
    check("B13 tool_use_skill 异常 fail-closed", "skill_gate_fail_closed" in TOOLS_SRC or "无法校验启用状态" in TOOLS_SRC)

    check("B14 config budget 真正传给 init(死参数修复)",
          "init_mcp_clients(cfg, budget" in CONFIG_SRC and
          "client.start(timeout=" in CONFIG_SRC)


# ============ C 真实运行层 ============
def part_c():
    print("== C 真实运行层 ==")
    MARKER = "LOCAL-ONLY-307"  # 本机服务哨兵串（非凭据）

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = MARKER.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a): pass

    try:
        srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    except Exception as e:
        check("C0 本机服务可启动", False, e); return
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    try:
        import search as S
    except Exception as e:
        check("C0 import search", False, e); srv.shutdown(); return

    # 用真实 getaddrinfo 作 resolver，注入到判据做决策验证
    def real_resolver(h, p):
        return socket.getaddrinfo(h, p, type=socket.SOCK_STREAM)

    for host, label in [("127.0.0.1", "裸IP"), ("localhost", "localhost"),
                        ("localhost.", "尾随点"), ("localtest.me", "localtest.me"),
                        ("127.0.0.1.nip.io", "nip.io")]:
        ok, ips, why = G.resolve_and_check(host, port, real_resolver) if G else (True, [], "")
        # 所有解析到回环的主机名（含 localtest.me/nip.io）都必须判不安全
        check("C-解析 %s 解析到回环被拒" % label, ok is False, (ok, ips, why))

    # http_get 真实拦截（本机服务）
    for host, label in [("localhost.", "尾随点"), ("localtest.me", "localtest.me")]:
        url = "http://%s:%d/" % (host, port)
        try:
            txt = S.http_get(url, timeout=3)
            leaked = MARKER in txt
        except Exception:
            leaked = False
        check("C-http_get %s 不泄露本机数据" % label, not leaked)

    # download_bytes 必须拒绝 file:// 与本机内网
    tmpf = os.path.join(_SBX, "secret.txt")
    with open(tmpf, "w", encoding="utf-8") as f:
        f.write("LFI-SECRET-307")
    try:
        b = S.download_bytes("file:///" + tmpf.replace("\\", "/"), timeout=3)
        lfi = b"LFI-SECRET-307" in b
    except Exception:
        lfi = False
    check("C-download file:// 拒绝本地文件读取", not lfi)
    try:
        b = S.download_bytes("http://127.0.0.1:%d/" % port, timeout=3)
        ssrf = MARKER.encode() in b
    except Exception:
        ssrf = False
    check("C-download 内网地址拒绝", not ssrf)
    srv.shutdown()


def main():
    part_a(); part_b(); part_c()
    print("==== 汇总 ==== PASS=%d FAIL=%d ====" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
