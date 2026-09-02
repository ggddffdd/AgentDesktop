"""浏览器扩展本地桥接服务 v1.0

仅监听 127.0.0.1（回环），带 token 校验，接收浏览器扩展（Edge/Chrome MV3）
推送的页面内容，通过 set_event_callback 推送到主程序（注入对话上下文）。

安全设计（必须保留）：
- 只绑 127.0.0.1，外部/局域网无法访问；任意网页发往 127.0.0.1 的请求若无 token 一律 401。
- 所有写操作（POST /page）必须带 token（Header X-Bridge-Token 或 ?token=），
  防止恶意网页往Agent里灌内容或借机触发工具。
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
from urllib.parse import urlparse, parse_qs
from pathlib import Path


_event_callback = None
_server = None
_token = None
DEFAULT_PORT = 9100


def set_event_callback(fn):
    """注册事件回调，签名 fn(kind, payload)。用于 UI 注入对话/托盘通知。"""
    global _event_callback
    _event_callback = fn


def gen_token():
    """生成一次性随机配对 token（32 hex 字符）。"""
    return secrets.token_hex(16)


def _user_data_dir():
    p = Path.home() / "Documents" / "AgentDesktop"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _notify(kind, payload):
    try:
        if _event_callback:
            _event_callback(kind, payload)
    except Exception:
        pass


def _auth_ok(self):
    """token 校验：Header X-Bridge-Token 或 query ?token= 任一匹配即可。"""
    global _token
    if not _token:
        return False
    h = self.headers.get("X-Bridge-Token", "")
    q = parse_qs(urlparse(self.path).query).get("token", [""])[0]
    return h == _token or q == _token


class BridgeHandler(http.server.BaseHTTPRequestHandler):
    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
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
            # 基本字段清洗：只收需要的字段，避免超大 payload 撑爆内存
            payload = {
                "title": str(data.get("title", ""))[:500],
                "url": str(data.get("url", ""))[:2000],
                "text": str(data.get("text", ""))[:60000],
                "selection": str(data.get("selection", ""))[:20000],
                "note": str(data.get("note", ""))[:500],
                "ts": data.get("ts", ""),
            }
            if not payload["text"] and not payload["selection"] and not payload["title"]:
                self._send(400, {"error": "empty payload"})
                return
            _notify("browser_page", payload)
            self._send(200, {"status": "ok"})
        elif path == "/pair":
            # 配对：仅当 token 为空（首次）时返回新 token 供扩展写入；
            # 已配对则要求带旧 token 才能重置，避免被任意网页重置。
            global _token
            data = self._read_json()
            if not _token:
                _token = gen_token()
                self._send(200, {"token": _token, "paired": True})
            elif _auth_ok(self):
                _token = gen_token()
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
        if self._server:
            return False
        try:
            # 仅绑 127.0.0.1，拒绝外部访问
            self._server = socketserver.TCPServer((self.host, self.port), BridgeHandler)
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
