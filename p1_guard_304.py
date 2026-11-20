# -*- coding: utf-8 -*-
"""v4.304.0 — 第三方代码审查 P1 三项的确定性护栏判据（纯标准库）。

对应报告（小臭玩AI_代码审查报告_v4.303.0.docx）：
  F-01 加密记忆库解密失败被空库覆盖  → db_plain_is_valid / fail_closed_decrypt
  F-02 web_fetch 可读本地文件与内网  → url_access_policy / is_blocked_host / read_response_capped
  F-03/F-09 write_file 可覆盖运行时文件、敏感判断子串黑名单
                                   → PROTECTED_RUNTIME_NAMES / is_protected_runtime_path / is_sensitive_path

设计纪律（沿用 v4.297/303）：
  · 判据是纯函数或仅依赖注入的确定性逻辑，不读真实用户数据、不依赖网络；
  · 业务模块（memory_store / search / tools）只接线，不放判定细节；
  · fail-closed：判不准就拒绝，不放行。
"""
import gzip
import ipaddress
import os
import urllib.parse

# ---- F-01 加密记忆库 ----
SQLITE_HEADER = b"SQLite format 3\x00"

# F-02 网络抓取边界
ALLOWED_FETCH_SCHEMES = ("http", "https")
MAX_FETCH_BYTES = 10 * 1024 * 1024          # 单次响应原文上限 10 MB
MAX_DECOMPRESSED_BYTES = 20 * 1024 * 1024   # gzip 解压后上限 20 MB（防 gzip bomb）
MAX_REDIRECTS = 5                            # 重定向逐跳复检的最大跳数

_BLOCKED_HOST_SUFFIXES = (
    ".localhost", ".local", ".internal", ".localdomain",
)

# ---- F-03/F-09 受保护运行时文件 ----
# 精确文件名（basename 全等或明确前缀），不是子串——避免 secretary_plan.md 被误伤。
PROTECTED_RUNTIME_NAMES = frozenset({
    "config.json", "config.json.bak", "config.json.tmp",
    "sessions.json", "automation_tasks.json", "task_state.json",
    "memory.md", "memory_core.md", "memory_pending.md",
    "memory.db", "memory.db.enc", "agent_log.db", "structured_log.db",
})
PROTECTED_RUNTIME_PREFIXES = ("memory.salt", "config.json.", "sessions.json.")
PROTECTED_RUNTIME_DIRS = ("task_resume",)
SENSITIVE_KEY_EXTS = (".pem", ".key", ".p12", ".pfx")
SENSITIVE_KEY_NAMES = frozenset({
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "credentials", "credentials.json", ".env", ".env.local", ".env.production",
})
SENSITIVE_DIR_PARTS = (".ssh", ".aws", ".gnupg", ".config/gcloud")


# ==================== F-01 ====================
def db_plain_is_valid(data):
    """解密后的字节是否像一个完整 SQLite 库（至少头正确）。空字节/乱字节一律 False。"""
    return bool(data) and bytes(data[:len(SQLITE_HEADER)]) == SQLITE_HEADER


def fail_closed_decrypt(cipher, enc_bytes):
    """解密加密库并校验 SQLite 头；任何失败都抛错，绝不返回可被当空库使用的值。

    抛错后调用方必须：不连接、不建表、不回写 .enc。这样错误口令/损坏文件
    不会让 _with_db 用空库覆盖原加密记忆。
    """
    if cipher is None:
        raise ValueError("未配置解密器")
    if not enc_bytes:
        raise ValueError("加密库为空")
    plain = cipher.decrypt(bytes(enc_bytes))
    if not db_plain_is_valid(plain):
        raise ValueError("解密结果不是有效 SQLite 库（拒绝以此覆盖记忆）")
    return plain


# ==================== F-02 ====================
def is_blocked_host(host):
    """主机是否为回环/私有/链路本地/保留/组播地址，或本机专用域名。DNS 名（非 IP）不在此拦。"""
    h = (host or "").strip().strip("[]").lower()
    if not h:
        return True
    if h == "localhost" or any(h.endswith(s) for s in _BLOCKED_HOST_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False  # 普通域名；逐跳复检在接线层做
    return bool(
        ip.is_loopback or ip.is_private or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def url_access_policy(url):
    """返回 (是否允许, 原因)。只放 http/https，且主机不得是本机/内网地址。"""
    try:
        parts = urllib.parse.urlsplit(url or "")
    except Exception:
        return False, "URL 解析失败"
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_FETCH_SCHEMES:
        return False, "仅允许 http/https，收到 %r" % scheme
    try:
        host = parts.hostname
    except Exception:
        return False, "主机名解析失败"
    if not host:
        return False, "缺少主机名"
    if is_blocked_host(host):
        return False, "目标为主机/内网保留地址：%s" % host
    return True, ""


def read_response_capped(resp, limit=MAX_FETCH_BYTES):
    """流式读取响应，超限即停并返回 (已读字节, 是否截断)。不给 resp.read() 吃满内存的机会。"""
    cap = int(limit) if limit and int(limit) > 0 else MAX_FETCH_BYTES
    chunks = []
    total = 0
    while True:
        block = resp.read(min(64 * 1024, cap + 1 - total))
        if not block:
            break
        chunks.append(block)
        total += len(block)
        if total >= cap:
            return b"".join(chunks), True
    return b"".join(chunks), False


def gunzip_capped(data, limit=MAX_DECOMPRESSED_BYTES):
    """gzip 解压并限制解压后体积，防 gzip bomb。返回 (数据, 是否截断)。"""
    cap = int(limit) if limit and int(limit) > 0 else MAX_DECOMPRESSED_BYTES
    d = gzip.GzipFile(fileobj=__import__("io").BytesIO(bytes(data)))
    out = d.read(cap + 1)
    return out[:cap], len(out) > cap


# ==================== F-03/F-09 ====================
def _basename(path):
    return os.path.basename(str(path).lower().replace("\\", "/"))


def _parts(path):
    return [p for p in str(path).lower().replace("\\", "/").split("/") if p]


def is_protected_runtime_path(path, workspace_dir):
    """路径是否指向受保护的运行时文件（配置/会话/记忆/任务/指数库）。

    write_file 用它拒绝覆盖；按 basename 精确匹配 + 明确目录，不做子串匹配。
    """
    name = _basename(path)
    if name in PROTECTED_RUNTIME_NAMES:
        return True
    if any(name.startswith(pre) for pre in PROTECTED_RUNTIME_PREFIXES):
        return True
    if name.endswith(".db") or name.endswith(".db-wal") or name.endswith(".db-shm"):
        return True
    parts = _parts(path)
    if any(seg in PROTECTED_RUNTIME_DIRS for seg in parts):
        return True
    # 显式指向工作区根的 config/sessions 也要挡（路径里带工作区名时）
    return False


def is_sensitive_path(path):
    """敏感文件判定（替换子串黑名单）：路径组件 + 扩展名 + 精确文件名。

    消除 secretary_plan.md / secret_santa.md 误伤；覆盖 id_ed25519、*.pem/*.key、
    config.json.bak/tmp、sessions.json、memory.md、agent_log.db 等原漏判项。
    """
    low = str(path or "").lower().replace("\\", "/")
    if not low:
        return False
    name = _basename(low)
    parts = _parts(low)
    if name in SENSITIVE_KEY_NAMES:
        return True
    if name.endswith(SENSITIVE_KEY_EXTS):
        return True
    if is_protected_runtime_path(low, None):
        return True
    for seg in parts:
        if seg in (".ssh", ".aws", ".gnupg"):
            return True
    if any(s in low for s in (".ssh/", ".aws/", ".gnupg/", ".config/gcloud/")):
        return True
    return False
