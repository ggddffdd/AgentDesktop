"""浏览器扩展本地桥接服务 v1.0

仅监听 127.0.0.1（回环），带 token 校验，接收浏览器扩展（Edge/Chrome MV3）
推送的页面内容，通过 set_event_callback 推送到主程序（注入对话上下文）。

安全设计（必须保留）：
- 只绑 127.0.0.1，外部/局域网无法访问；任意网页发往 127.0.0.1 的请求若无 token 一律 401。
- 所有写操作（POST /page）必须带 token（仅接受 Header X-Bridge-Token，
  不再接受 ?token= 查询参数，避免 token 出现在 URL/日志/历史里泄露），
  防止恶意网页往小臭里灌内容或借机触发工具。
- 健康检查 GET /health 不需要 token（只读、无副作用）。

使用方式：
    from browser_bridge import browser_bridge_start, browser_bridge_stop, browser_bridge_token
    tok = browser_bridge_start(cfg)   # 启动，必要时生成并回写 token
    browser_bridge_stop()
"""

import http.server
import socketserver
import threading
import json
import secrets
import uuid
from urllib.parse import urlparse
from pathlib import Path


_event_callback = None
_server = None
_token = None
_persist_cb = None       # v4.125 M-20：token 变更持久化回调（由 main 注册）
DEFAULT_PORT = 9100
MAX_BODY = 256 * 1024    # v4.125 N-04：body 硬上限（对齐 webhook_server 256KB）
# #754：POST 处理并发上限，防止大量连接耗尽线程/内存
MAX_CONCURRENT = 16
_post_sem = threading.Semaphore(MAX_CONCURRENT)
# #754：连接读取超时（秒）——防止半开/慢速连接长期占用线程（slowloris 类）
READ_TIMEOUT = 15


def set_persist_callback(fn):
    """v4.125 M-20：注册 token 持久化回调，签名 fn(new_token)。

    /pair 重置 token 后调用，由 main 写回 config 持久化——
    否则扩展拿到新码而 config 里还是旧码，重启后全部 401。
    """
    global _persist_cb
    _persist_cb = fn


def _persist_token(tok):
    try:
        if _persist_cb and tok:
            _persist_cb(tok)
    except Exception:
        pass


def set_event_callback(fn):
    """注册事件回调，签名 fn(kind, payload)。用于 UI 注入对话/托盘通知。"""
    global _event_callback
    _event_callback = fn


def gen_token():
    """生成一次性随机配对 token（32 hex 字符）。"""
    return secrets.token_hex(16)


def _user_data_dir():
    p = Path.home() / "Documents" / "小臭玩AI"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _notify(kind, payload):
    try:
        if _event_callback:
            _event_callback(kind, payload)
    except Exception:
        pass


# #756(P2#2)：投递确认基础设施。桥接在 /page 返回前等待主程序真正完成 UI
# 注入并回报结果，避免「扩展显示已发送」但实际没进对话（假成功）。
_deliveries = {}
_deliveries_lock = threading.Lock()
DELIVERY_WAIT = 12  # 秒：等待主程序注入确认的最长时限


def report_delivery(delivery_id, ok, detail=""):
    """#756：由主程序（UI 注入完成后）回调，回报该次投递的实际结果。

    args:
        delivery_id: 与 /page 请求同款的 uuid hex。
        ok:          注入是否成功（含草稿保护拦截/自动发送失败均算 False）。
        detail:      失败原因（可选，仅用于回执，不落盘敏感内容）。
    """
    try:
        with _deliveries_lock:
            entry = _deliveries.get(delivery_id)
        if entry:
            entry["res"] = {"ok": bool(ok), "detail": str(detail)[:200]}
            entry["ev"].set()
    except Exception:
        pass


def _auth_ok(self):
    """token 校验：仅接受 Header X-Bridge-Token，恒定时间比较。

    #755 移除 ?token= 查询参数支持——URL 会进入浏览器历史/代理日志/服务端
    访问日志，token 泄露面过大；扩展侧本就用 X-Bridge-Token 头发送，无兼容损失。
    """
    global _token
    if not _token:
        return False
    h = self.headers.get("X-Bridge-Token", "")
    if not h:
        return False
    return secrets.compare_digest(h, _token)


class BridgeHandler(http.server.BaseHTTPRequestHandler):
    # #754：为整个连接设置 socket 超时，避免恶意/半开连接长期占用线程
    timeout = READ_TIMEOUT

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            # #754：负数 Content-Length 会让 rfile.read(-1) 读到连接关闭，
            # 占满线程 → 直接拒绝。v4.125 N-04：超限同样拒绝。
            if length < 0:
                self._send(400, {"error": "invalid content-length"})
                return None
            if length > MAX_BODY:
                self._send(413, {"error": "payload too large"})
                return None
            raw = self.rfile.read(length) if length else b""
            return json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            return {}

    def _send(self, code, obj):
        try:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def _send_401(self):
        self._send(401, {"error": "unauthorized",
                         "hint": "缺少或错误的 X-Bridge-Token"})

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/health", "/", "/index.html"):
            self._send(200, {"status": "ok", "service": "xiaochou-browser-bridge",
                             "authed": bool(_token)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/page":
            if not _auth_ok(self):
                self._send_401()
                return
            data = self._read_json()
            if data is None:   # v4.125 N-04：超限/非法 413/400 已回，不再处理
                return
            # 基本字段清洗：只收需要的字段，避免超大 payload 撑爆内存
            # L1：新增 mode / autosend / markdown / meta（扩展侧 L1 增强版才带，
            # 旧版扩展不带时全部走默认值，向后兼容）。
            _meta_in = data.get("meta") or {}
            if not isinstance(_meta_in, dict):
                _meta_in = {}
            try:
                _wc = int(_meta_in.get("wordCount", 0) or 0)
            except (TypeError, ValueError):
                _wc = 0
            # #756：投递 ID——用于等待主程序真实注入确认，杜绝「假成功」。
            delivery_id = uuid.uuid4().hex
            payload = {
                "title": str(data.get("title", ""))[:500],
                "url": str(data.get("url", ""))[:2000],
                "text": str(data.get("text", ""))[:60000],
                "selection": str(data.get("selection", ""))[:20000],
                "note": str(data.get("note", ""))[:500],
                "mode": str(data.get("mode", ""))[:16],
                "autosend": bool(data.get("autosend", False)),
                "markdown": str(data.get("markdown", ""))[:60000],
                "meta": {
                    "siteName": str(_meta_in.get("siteName", ""))[:200],
                    "capturedAt": str(_meta_in.get("capturedAt", ""))[:60],
                    "wordCount": _wc,
                },
                "ts": data.get("ts", ""),
                "delivery_id": delivery_id,
            }
            if not payload["text"] and not payload["selection"] and not payload["title"]:
                self._send(400, {"error": "empty payload"})
                return
            with _post_sem:    # #754：并发处理上限
                # 登记投递事件，入队后等待主程序 UI 注入确认（最多 DELIVERY_WAIT 秒）
                ev = threading.Event()
                with _deliveries_lock:
                    _deliveries[delivery_id] = {"ev": ev, "res": None}
                _notify("browser_page", payload)
                got = ev.wait(timeout=DELIVERY_WAIT)
                with _deliveries_lock:
                    res = _deliveries.pop(delivery_id, {}).get("res")
                if not got or res is None:
                    # 超时未确认——诚实返回「已接收但未确认」，不再假装成功
                    self._send(202, {"status": "accepted", "delivery_id": delivery_id,
                                     "delivered": False,
                                     "note": "已接收，等待主程序处理（未确认）"})
                elif res.get("ok"):
                    self._send(200, {"status": "ok", "delivery_id": delivery_id,
                                     "delivered": True})
                else:
                    self._send(200, {"status": "ok", "delivery_id": delivery_id,
                                     "delivered": False,
                                     "detail": res.get("detail", "")})
        elif path == "/pair":
            # 配对：仅当 token 为空（首次）时返回新 token 供扩展写入；
            # 已配对则要求带旧 token 才能重置，避免被任意网页重置。
            global _token
            data = self._read_json()
            if data is None:   # v4.125 N-04：413 已回，不再处理
                return
            with _post_sem:    # #754：并发处理上限
                if not _token:
                    _token = gen_token()
                    _persist_token(_token)  # v4.125 M-20：新码落 config
                    self._send(200, {"token": _token, "paired": True})
                elif _auth_ok(self):
                    _token = gen_token()
                    _persist_token(_token)  # v4.125 M-20：重置码落 config
                    self._send(200, {"token": _token, "paired": True, "reset": True})
                else:
                    self._send_401()
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass  # 静默，避免刷屏


class BrowserBridgeServer:
    def __init__(self, host="127.0.0.1", port=DEFAULT_PORT):
        self.host = host
        self.port = port
        self._server = None
        self._thread = None

    def start(self):
        # 审计修复 F10：已在运行属幂等成功。原返回 False 被调用方
        # （browser_bridge_start 的 `r is not True`）当启动失败 → 重复点"启动桥接"
        # 明明服务活着却返回 None，且新 token 丢失，扩展拿旧 token 鉴权失败。
        if self._server:
            return True
        try:
            # 仅绑 127.0.0.1，拒绝外部访问。
            # v4.125 N-04：TCPServer → ThreadingHTTPServer——单线程下慢请求
            # （大 body / 恶意连接）会阻塞健康检查与其他请求，属于自我 DoS；
            # threading 版每连接一线程，且 daemon_threads 不挡退出。
            self._server = http.server.ThreadingHTTPServer((self.host, self.port),
                                                           BridgeHandler)
            self._server.daemon_threads = True
        except Exception as e:
            return f"启动失败：{e}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None


def browser_bridge_start(cfg=None):
    """启动桥接服务。返回 token（可能为新生成的），调用方应写回 config 持久化。"""
    global _server, _token, DEFAULT_PORT
    cfg = cfg or {}
    port = int(cfg.get("browser_bridge_port", DEFAULT_PORT))
    # 复用已存的 token；没有则生成
    tok = cfg.get("browser_bridge_token", "")
    if not tok:
        tok = gen_token()
    _token = tok
    if _server is None:
        _server = BrowserBridgeServer(port=port)
    r = _server.start()
    if r is not True:
        return None  # 启动失败
    return _token


def browser_bridge_stop():
    global _server
    if _server:
        _server.stop()
        _server = None
        return True
    return False


def browser_bridge_token():
    """返回当前 token（供 UI 显示/复制）。"""
    return _token or ""
