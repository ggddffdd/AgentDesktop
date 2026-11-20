# -*- coding: utf-8 -*-
"""v4.304.0：第三方代码审查 P1 三项护栏的回归判据（F-01 / F-02 / F-03+F-09）。

判据分两层：
  A 纯函数层——p1_guard_304 的决策逻辑，确定性、不碰真实用户数据、不联网；
  B 接线层——memory_store / search / tools 真实调用护栏，用沙箱目录与假 cipher。

旧代码（v4.303.0）没有 p1_guard_304 模块、没有接线 → 探测式调用返回哨兵，
对应判据翻红；新代码全绿。
"""
import io
import os
import sys
import tempfile
import sqlite3
from pathlib import Path
from unittest import mock

_SBX = tempfile.mkdtemp(prefix="xc_304_guard_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_WORKSPACE_DIR"] = os.path.join(_SBX, "ws")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0


class _Sentinel:
    """旧代码缺函数时的替代值：支持下标/比较/布尔，保证旧代码能跑完整套而不是半路崩。

    任何与真实期望值（True/False/bytes/tuple）的比较都为假 → 判据干净翻红；
    `probe(...)[0]` 之类的下标访问返回自身而非抛 TypeError。
    """
    def __getitem__(self, k):
        return self

    def __eq__(self, other):
        return False

    def __ne__(self, other):
        return True

    def __bool__(self):
        return False

    def __iter__(self):
        # 兼容 `ok, reason = probe(...)` 元组解包；两个都是哨兵 → 判据翻红
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
    """旧代码缺模块/缺函数时返回唯一哨兵，避免「两个 None 相等」假绿。"""
    if mod is None:
        return _SENTINEL
    f = getattr(mod, fn, None)
    if f is None:
        return _SENTINEL
    try:
        return f(*args, **kwargs)
    except Exception as e:
        return ("__EXC__", type(e).__name__, str(e))


try:
    import p1_guard_304 as G
except Exception:
    G = None

SRC = {
    "p1_guard_304.py": (ROOT / "p1_guard_304.py").read_text(encoding="utf-8") if (ROOT / "p1_guard_304.py").exists() else "",
    "memory_store.py": (ROOT / "memory_store.py").read_text(encoding="utf-8-sig"),
    "search.py": (ROOT / "search.py").read_text(encoding="utf-8"),
    "tools.py": (ROOT / "tools.py").read_text(encoding="utf-8-sig"),
}


def fn_seg(src, name):
    key = "def %s(" % name
    i = src.find(key)
    if i < 0:
        return ""
    j = src.find("\ndef ", i + len(key))
    return src[i:(j if j > 0 else len(src))]


print("== A. F-01 加密库 fail-closed 判据 ==")
check("A1 SQLite 头常量正确", getattr(G, "SQLITE_HEADER", None) == b"SQLite format 3\x00")
check("A2 合法头判真", probe(G, "db_plain_is_valid", b"SQLite format 3\x00rest") is True)
check("A3 空数据判假", probe(G, "db_plain_is_valid", b"") is False)
check("A4 乱码判假", probe(G, "db_plain_is_valid", b"garbage-not-sqlite") is False)
check("A5 截断头判假", probe(G, "db_plain_is_valid", b"SQLite fo") is False)

class _FakeCipher:
    def __init__(self, plain=None, raise_on=None):
        self.plain = plain if plain is not None else b"SQLite format 3\x00data"
        self.raise_on = raise_on
    def decrypt(self, b):
        if self.raise_on:
            raise self.raise_on("bad key")
        return self.plain

r = probe(G, "fail_closed_decrypt", _FakeCipher(raise_on=ValueError), b"xxx")
check("A6 解密异常抛错不返回空", isinstance(r, tuple) and r[0] == "__EXC__" and r[1] == "ValueError", r)
r = probe(G, "fail_closed_decrypt", _FakeCipher(plain=b"garbage"), b"xxx")
check("A7 解密后非 SQLite 头抛错", isinstance(r, tuple) and r[0] == "__EXC__" and "SQLite" in r[2], r)
check("A8 合法解密返回明文", probe(G, "fail_closed_decrypt", _FakeCipher(), b"xxx") == b"SQLite format 3\x00data")
r = probe(G, "fail_closed_decrypt", None, b"xxx")
check("A9 无 cipher 抛错", isinstance(r, tuple) and r[0] == "__EXC__", r)
check("A10 空加密数据抛错", isinstance(probe(G, "fail_closed_decrypt", _FakeCipher(), b""), tuple))

print("== B. F-02 URL 边界判据 ==")
check("B1 scheme 常量", getattr(G, "ALLOWED_FETCH_SCHEMES", None) == ("http", "https"))
check("B2 file:// 拒绝", probe(G, "url_access_policy", "file:///C:/Windows/win.ini")[0] is False)
check("B3 回环拒绝", probe(G, "url_access_policy", "http://127.0.0.1:8000/")[0] is False)
check("B4 私网 10.x 拒绝", probe(G, "url_access_policy", "http://10.0.0.1/")[0] is False)
check("B5 私网 192.168 拒绝", probe(G, "url_access_policy", "https://192.168.1.1/x")[0] is False)
check("B6 链路本地拒绝", probe(G, "url_access_policy", "http://169.254.169.254/")[0] is False)
check("B7 localhost 拒绝", probe(G, "url_access_policy", "http://localhost/x")[0] is False)
check("B8 常规 http 放行", probe(G, "url_access_policy", "http://example.com/x")[0] is True)
check("B9 常规 https 放行", probe(G, "url_access_policy", "https://github.com/a")[0] is True)
check("B10 内网域名后缀拒绝", probe(G, "url_access_policy", "http://foo.local/x")[0] is False)

class _FakeResp:
    def __init__(self, chunks):
        self.chunks = list(chunks)
    def read(self, n):
        if not self.chunks:
            return b""
        head = self.chunks[0]
        take = min(n, len(head))
        out, self.chunks[0] = head[:take], head[take:]
        if not self.chunks[0]:
            self.chunks.pop(0)
        return out

d, t = probe(G, "read_response_capped", _FakeResp([b"a" * 100]), 50)
# 实现允许多读 1 字节用于判定超限（cap+1），返回 50 或 51 均可接受
check("B11 响应超上限被截断", isinstance(d, bytes) and 50 <= len(d) <= 51 and t is True,
      (len(d) if isinstance(d, bytes) else d, t))
d, t = probe(G, "read_response_capped", _FakeResp([b"abc"]), 50)
check("B12 小响应不截断", d == b"abc" and t is False)
import gzip as _gz
buf = io.BytesIO()
with _gz.GzipFile(fileobj=buf, mode="wb") as f:
    f.write(b"hello gzip")
d, t = probe(G, "gunzip_capped", buf.getvalue(), 100)
check("B13 gzip 正常解压", d == b"hello gzip" and t is False)
d, t = probe(G, "gunzip_capped", buf.getvalue(), 3)
check("B14 gzip bomb 截断", d == b"hel" and t is True)

print("== C. F-03/F-09 受保护路径判据 ==")
PROTECTED = ["config.json", "config.json.bak", "sessions.json", "memory.md",
             "memory.db", "agent_log.db", "automation_tasks.json"]
for i, name in enumerate(PROTECTED):
    check("C%-2d %s 受保护" % (i + 1, name), probe(G, "is_protected_runtime_path", name, "/ws") is True)
check("C9 普通文件不误判", probe(G, "is_protected_runtime_path", "notes.txt", "/ws") is False)
check("C10 子目录 config 也挡", probe(G, "is_protected_runtime_path", "/ws/sub/config.json", "/ws") is True)
# 注意：「secret」这类子串故意不再拦截——子串黑名单正是误伤根源（secretary_plan.md）
SENS = ["config.json.bak", "sessions.json", "memory.md", "agent_log.db",
        "id_ed25519", "key.pem", ".ssh/id_rsa", "credentials"]
for i, name in enumerate(SENS):
    check("C%-2d %s 判敏感" % (i + 11, name), probe(G, "is_sensitive_path", name) is True)
for i, name in enumerate(["secretary_plan.md", "secret_santa.md", "notes.txt", "report.md"]):
    check("C%-2d %s 不误伤" % (i + 16, name), probe(G, "is_sensitive_path", name) is False)

print("== D. 接线层判据 ==")
import search as SEARCH
for u in ["file:///C:/Windows/win.ini", "http://127.0.0.1:1/", "http://10.0.0.1/"]:
    try:
        SEARCH.http_get(u, timeout=1)
        rejected = False
    except Exception as e:
        rejected = ("拒绝" in str(e)) or ("仅允许" in str(e)) or ("主机" in str(e))
    check("D-web_fetch 拒绝 %s" % u[:24], rejected)

import tools as TOOLS
check("D-sensitive secretary 放行", TOOLS._is_sensitive_file("secretary_plan.md") is False)
check("D-sensitive sessions 拦截", TOOLS._is_sensitive_file("sessions.json") is True)
check("D-sensitive id_ed25519 拦截", TOOLS._is_sensitive_file("id_ed25519") is True)

sbx = tempfile.mkdtemp(prefix="xc304_write_")
old_ws = getattr(TOOLS, "WORKSPACE_DIR", None)
TOOLS.WORKSPACE_DIR = sbx
try:
    msg1 = TOOLS.tool_write_file(sbx, "config.json", "HACK")
    check("D-write_file 拒 config.json", "拒绝" in msg1, msg1)
    msg2 = TOOLS.tool_write_file(sbx, "sessions.json", "HACK")
    check("D-write_file 拒 sessions.json", "拒绝" in msg2, msg2)
    msg3 = TOOLS.tool_write_file(sbx, "note.txt", "ok")
    check("D-write_file 放行普通文件", "已写入" in msg3, msg3)
finally:
    if old_ws is not None:
        TOOLS.WORKSPACE_DIR = old_ws

# F-01 接线：损坏 .enc + 错误口令 → 抛错、.enc 不变、不建空库
import memory_store as MS
sbx2 = tempfile.mkdtemp(prefix="xc304_db_")
MS._configure(sbx2)
dbp = os.path.join(sbx2, "memory.db")
conn = sqlite3.connect(dbp)
conn.execute("CREATE TABLE memories(id INTEGER PRIMARY KEY, content TEXT)")
conn.execute("INSERT INTO memories(content) VALUES('precious long-term memory')")
conn.commit(); conn.close()
plain = open(dbp, "rb").read()
try:
    from cryptography.fernet import Fernet
    good_key = Fernet.generate_key()
    class _Cipher:
        def __init__(self, k): self.f = Fernet(k)
        def encrypt(self, b): return self.f.encrypt(b)
        def decrypt(self, b): return self.f.decrypt(b)
    ep = dbp + ".enc"
    open(ep, "wb").write(_Cipher(good_key).encrypt(plain))
    os.remove(dbp)
    MS._cipher = _Cipher(Fernet.generate_key())  # 错误口令
    enc_before = open(ep, "rb").read()
    raised = False
    try:
        MS._with_db(lambda c: c.execute("CREATE TABLE IF NOT EXISTS x(a)"))
    except Exception:
        raised = True
    check("D01 解密失败抛错", raised)
    check("D02 加密文件字节不变", open(ep, "rb").read() == enc_before)
    check("D03 未创建空明文库", not os.path.exists(dbp))
finally:
    MS._cipher = None

check("D04 fail_closed_decrypt 已接线 memory_store", "fail_closed_decrypt" in SRC["memory_store.py"])
check("D05 URL 策略已接线 search", "url_access_policy" in SRC["search.py"])
check("D06 受保护名单已接线 tools", "is_protected_runtime_path" in SRC["tools.py"])
check("D07 敏感判据已替换 tools", "is_sensitive_path" in SRC["tools.py"])

print("\n==== %s PASS=%d FAIL=%d ====" % ("test_p1_guard_304", PASS, FAIL))
sys.exit(1 if FAIL else 0)
