# -*- coding: utf-8 -*-
"""v4.307.0 — 第三方验收报告「不通过」项的确定性纯判据（无第三方依赖）。

对应验收报告（对 v4.306.0 的验收，结论：F-02 未修复完整）：
  F-02 SSRF DNS 绕过  → normalize_host / resolve_and_check / peer_ip_ok / download_url_allowed
                        （localhost. / localtest.me / 127.0.0.1.nip.io 均解析到回环）
  F-01 明文库错误口令窗口 → plain_db_decision
  F-03/F-09 *.db 过度拦截 + 非原子写 → protected_db_name / atomic_write_ok
  F-04 tool_use_skill fail-open → skill_gate_fail_closed
  F-05 legacy 扫根 → snapshot_root_ok
  F-07 budget_sec 死参数 → budget_wired

设计纪律（沿用 v4.297/303/304/305/306）：
  · 纯函数 / 仅依赖注入（resolver）的确定性逻辑，不读真实用户数据、不发真实网络请求；
  · 业务模块（search / memory_store / tools / config）只接线，不放判定细节；
  · fail-closed：判不准（解析失败 / 无 resolver / 非法输入）一律按拒绝处理。
"""
import ast
import ipaddress
import urllib.parse

# ---- F-02 网络边界 ----
_ALLOWED_FETCH_SCHEMES = frozenset({"http", "https"})
_LOCALHOST_NAMES = frozenset({"localhost", "localhost.localdomain"})
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain")


def normalize_host(host):
    """主机名规范化：去方括号、去首尾空白、去尾部点、小写。

    `localhost.`、`LOCALHOST`、`[::1]` 都要还原成可判定的标准形式。
    验收报告实测 `http://localhost.:PORT/` 能绕过，根因就是没去尾随点。
    """
    h = str(host or "").strip()
    if h.startswith("[") and "]" in h:  # IPv6 字面量 [::1]
        h = h[1:h.index("]")]
    h = h.strip().rstrip(".").strip().lower()
    return h


def ip_is_forbidden(ip_text):
    """单个 IP 文本是否为回环/私有/链路本地/保留/组播/未指定地址。"""
    try:
        ip = ipaddress.ip_address(str(ip_text).strip())
    except (ValueError, TypeError):
        return True  # 解析不出 IP → fail-closed
    return bool(
        ip.is_loopback or ip.is_private or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def host_name_is_forbidden(host):
    """仅按主机名文本判定（不解析 DNS）：本机专用名 / 裸 IP 落入内网段。"""
    h = normalize_host(host)
    if not h:
        return True
    if h in _LOCALHOST_NAMES or any(h.endswith(s) for s in _LOCAL_SUFFIXES):
        return True
    # 纯 IP 字面量直接判网段；域名返回 False（需继续走 DNS 解析）
    try:
        ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip_is_forbidden(h)


def resolve_and_check(host, port, resolver):
    """解析主机名的全部 A/AAAA，任一落地内网地址即拒。

    resolver(host, port) 需返回可迭代的地址序列，每项形如 (family, type, proto,
    canonname, sockaddr)，sockaddr[0] 为 IP 文本；与 socket.getaddrinfo 兼容。

    返回 (是否安全, IP 列表, 原因)：
      · 无 resolver / 解析抛错 / 解析结果为空 → (False, [], 原因)  fail-closed；
      · 任一 IP 落入 forbidden 段 → (False, ips, 原因)；
      · 全部 IP 均为公网 → (True, ips, "")。

    DNS rebinding 的 TOCTOU 由接线层「连接后复核 peer IP」兜底（peer_ip_ok）。
    """
    h = normalize_host(host)
    if not h:
        return False, [], "主机名为空"
    if host_name_is_forbidden(h):
        return False, [], "本机/内网保留主机名：%s" % h
    if resolver is None:
        return False, [], "无 DNS 解析器（fail-closed）"
    try:
        infos = list(resolver(h, port) or [])
    except Exception as e:
        return False, [], "DNS 解析失败（fail-closed）：%s" % e
    if not infos:
        return False, [], "DNS 解析无结果（fail-closed）"
    ips = []
    for info in infos:
        try:
            ip = info[4][0]
        except Exception:
            return False, [], "DNS 结果格式异常（fail-closed）"
        ips.append(str(ip))
    for ip in ips:
        if ip_is_forbidden(ip):
            return False, ips, "解析到本机/内网保留地址：%s" % ip
    return True, ips, ""


def peer_ip_ok(peer_ip):
    """连接建立后对端 IP 复核：必须是公网地址。

    防 DNS rebinding：解析时是公网 IP、连接时 DNS 已改指向内网，靠这一步挡住。
    取不到 peer IP（None / 异常）时返回 False（fail-closed）。
    """
    if peer_ip is None:
        return False
    return not ip_is_forbidden(peer_ip)


def download_url_allowed(url, resolver, port=443):
    """download_bytes 的完整准入：scheme 白名单 + 主机 DNS 校验。

    原 download_bytes 是裸 urlopen，连 file:// 都能读本地文件（验收报告未提，
    但比 SSRF 更严重的本地文件读取）。此判据把它接到与 http_get 同一套策略。
    """
    try:
        parts = urllib.parse.urlsplit(url or "")
    except Exception:
        return False, "URL 解析失败"
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_FETCH_SCHEMES:
        return False, "download 仅允许 http/https，收到 %r" % scheme
    host = parts.hostname
    if not host:
        return False, "download 缺少主机名"
    ok, _ips, reason = resolve_and_check(host, port, resolver)
    return ok, reason


# ==================== F-01 明文库决策 ====================
def plain_db_decision(enc_exists, plain_exists, cipher_ok):
    """加密模式下遇到「.enc + 明文并存」时该如何处理。

    验收报告残留路径：崩溃遗留明文 memory.db 后，用错误口令写入仍被接受，
    并把 .enc 用错误密钥重加密 → 正确口令永久读不出。

    正确决策（全部满足才允许正常执行 fn）：
      · 有 .enc 且明/密并存：必须先用当前口令验证 .enc 能解密（cipher_ok=True）；
        验证失败 → fail-closed，禁止写入、禁止重加密。
      · 只有 .enc 无明文：走原 fail_closed_decrypt 解密（cipher_ok 代表解密成功）。
      · 只有明文无 .enc：首次迁移场景，允许（cipher_ok 视为无关）。
      · 两者都无：允许（空库初始化）。

    返回 "allow" / "decrypt" / "deny"。
    """
    if enc_exists and plain_exists:
        return "allow" if cipher_ok else "deny"
    if enc_exists and not plain_exists:
        return "decrypt"
    return "allow"


# ==================== F-03/F-09 路径与原子写 ====================
# v4.308 F-03 残余：SQLite 边车文件（-wal/-shm/-journal）。验收指出这些文件
# is_protected_runtime_path() 仍返回 False，write_file 可写入污染 WAL/SHM，
# 影响 SQLite 恢复或造成记忆库异常。只对项目自身运行时库补边车后缀，放行普通库。
_PROTECTED_DB_NAMES = frozenset({"memory.db", "memory.db.enc", "agent_log.db",
                                 "structured_log.db", "task_state.db"})
_DB_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def db_sidecar_protected(name):
    """v4.308：文件名是否为「受保护运行时库的 SQLite 边车文件」。

    精确匹配 `受保护库名 + -wal/-shm/-journal`，不对普通 *.db 的边车误伤
    （notes.db-wal 放行）。验收 F-03 残余缺口：主库挡住了但边车仍可写。
    """
    n = str(name or "").strip().lower().replace("\\", "/").rsplit("/", 1)[-1]
    for _base in _PROTECTED_DB_NAMES:
        for _sfx in _DB_SIDECAR_SUFFIXES:
            if n == _base + _sfx:
                return True
    return False


def protected_db_name(name):
    """文件名是否为「受保护运行时数据库」——精确名单，不再 *.db 一刀切。

    验收报告残留：`*.db` 全局拦截导致普通用户数据库也无法 write_file 创建。
    这里只保护项目自身的运行时库，用户自己的 data.db 等应放行。
    v4.308：连边车一起保护（memory.db-wal 等），但普通库边车仍放行。
    """
    n = str(name or "").strip().lower().replace("\\", "/").rsplit("/", 1)[-1]
    return n in _PROTECTED_DB_NAMES or db_sidecar_protected(n)


def atomic_write_ok(uses_tmp_and_replace):
    """写入是否为临时文件 + os.replace 原子替换（防止半写损坏运行时文件）。"""
    return bool(uses_tmp_and_replace)


# ==================== F-04/F-05/F-07 ====================
def skill_gate_fail_closed(except_returns_deny):
    """配置读取异常时技能校验是否 fail-closed（拒绝加载）。"""
    return bool(except_returns_deny)


def snapshot_root_ok(uses_run_cwd_root):
    """产物快照根是否与子进程 run_cwd 同根（不再扫整个用户数据根）。"""
    return bool(uses_run_cwd_root)


def budget_wired(passed_to_init):
    """budget_sec 是否真正传给 init_mcp_clients/start（不再是死参数）。"""
    return bool(passed_to_init)


# ==================== 源码级接线判据（AST） ====================
def count_getaddrinfo_in(module_src):
    """统计源码中 socket.getaddrinfo 调用次数（F-02 DNS 解析接线，应 >=1）。"""
    try:
        tree = ast.parse(module_src)
    except SyntaxError:
        return -1
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "getaddrinfo":
                n += 1
    return n


def count_bare_urlopen(module_src, func_name="download_bytes"):
    """统计指定函数体内的裸 urllib.request.urlopen 调用数（F-02 应为 0）。"""
    try:
        tree = ast.parse(module_src)
    except SyntaxError:
        return -1
    lines = module_src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            seg = "\n".join(lines[node.lineno - 1: getattr(node, "end_lineno", node.lineno)])
            return seg.count("urlopen(")
    return -1


def count_legacy_workspace_snapshot(module_src):
    """统计 run_python legacy 分支里 snapshot_workspace(WORKSPACE_DIR) 次数（F-05 应为 0）。"""
    try:
        tree = ast.parse(module_src)
    except SyntaxError:
        return -1
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "snapshot_workspace":
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id == "WORKSPACE_DIR":
                    n += 1
    return n


def test_db_isolation_ok(module_src):
    """测试源码是否整体隔离记忆库：main 内 >=2 处 _configure 且无裸 MEMORY_PATH 赋值。

    v4.308：验收实测 dataloss 测试只改 MEMORY_PATH，append_memory 的 _db_append
    写回真实用户目录 memory.db。整体 _configure 才会连 MEMORY_DB_PATH 一起重定向。
    AST 级（不被注释里的字面量骗过），且要求两处都在（改坏任意一处即红）。
    """
    try:
        tree = ast.parse(module_src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            cfg = 0
            bare = 0
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                        and sub.func.attr == "_configure":
                    cfg += 1
                targets = []
                if isinstance(sub, ast.Assign):
                    targets = sub.targets
                elif isinstance(sub, ast.AnnAssign):
                    targets = [sub.target]
                for t in targets:
                    if isinstance(t, ast.Attribute) and t.attr == "MEMORY_PATH":
                        bare += 1
            return cfg >= 2 and bare == 0
    return False


def function_has_call(module_src, func_name, call_name):
    """指定函数体内是否出现对 call_name 的调用（AST 级，避免字符串假绿）。"""
    try:
        tree = ast.parse(module_src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    if (isinstance(f, ast.Name) and f.id == call_name) or \
                       (isinstance(f, ast.Attribute) and f.attr == call_name):
                        return True
    return False
