"""Webhook / 事件驱动模块 v1.1
内置 HTTP 服务器，支持外部事件触发：
- GitHub webhook 接收（/webhook/github）
- 自定义 webhook（/webhook/custom）
- 跨应用触发器（/api/trigger）
- 健康检查（/health）

事件记录落用户目录 webhook_events.jsonl，并可通过 set_event_callback
推送到 UI（托盘通知）。服务器在后台 daemon 线程运行，进程退出自动终止。

使用方式：
    from webhook_server import webhook_start, webhook_stop, webhook_recent_events
    webhook_start(9000)                 # 启动
    events = webhook_recent_events(20)  # 取最近 20 条
    webhook_stop()                      # 停止
"""

import http.server
import socketserver
import threading
import json
from datetime import datetime
from pathlib import Path


_event_callback = None


def set_event_callback(fn):
    """注册事件回调，签名 fn(kind, payload)。用于 UI 托盘通知等。"""
    global _event_callback
    _event_callback = fn


def _user_data_dir():
    p = Path.home() / "Documents" / "小臭玩AI"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _log_event(kind, payload):
    try:
        path = _user_data_dir() / "webhook_events.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(
                {"kind": kind, "payload": payload,
                 "timestamp": datetime.now().isoformat()},
                ensure_ascii=False) + "\n")
    except Exception:
        pass
    if _event_callback:
        try:
            _event_callback(kind, payload)
        except Exception:
            pass


class WebhookHandler(http.server.BaseHTTPRequestHandler):
    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            return json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            return {}

    def do_POST(self):
        data = self._read_json()
        if self.path == "/webhook/github":
            event = self.headers.get("X-GitHub-Event", "unknown")
            _log_event("github", {"event": event, "payload": data})
            body = {"status": "received", "event": event}
        elif self.path == "/webhook/custom":
            _log_event("custom", data)
            body = {"status": "received"}
        elif self.path == "/api/trigger":
            action = data.get("action", "")
            _log_event("trigger", data)
            body = {"status": "triggered", "action": action}
        else:
            self._send(404, {"error": "Not Found"})
            return
        self._send(200, body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        elif self.path in ("/", "/index.html"):
            self._send_html(200, self._status_page())
        else:
            self._send(404, {"error": "Not Found"})

    def _send(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def log_message(self, fmt, *args):
        pass  # 静默，避免刷屏

    def _send_html(self, code, html):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _status_page(self):
        host = self.headers.get("Host", "localhost:9000")
        events = webhook_recent_events(10)
        items = []
        for e in reversed(events):
            kind = e.get("kind", "?")
            ts = e.get("timestamp", "")
            try:
                txt = json.dumps(e.get("payload", {}), ensure_ascii=False)
            except Exception:
                txt = str(e.get("payload", ""))
            if len(txt) > 240:
                txt = txt[:240] + "…"
            items.append(
                "<li><span class='kind'>" + _esc(kind) + "</span>"
                "<span class='t'>" + _esc(ts) + "</span>"
                "<pre>" + _esc(txt) + "</pre></li>")
        ev_html = "\n".join(items) if items else "<li class='muted'>暂无事件</li>"
        return STATUS_PAGE_HTML.replace("{HOST}", _esc(host)).replace("{EVENTS}", ev_html)


class WebhookServer:
    def __init__(self, host="0.0.0.0", port=9000):
        self.host = host
        self.port = port
        self._server = None
        self._thread = None

    def start(self):
        if self._server:
            return False
        try:
            self._server = socketserver.TCPServer((self.host, self.port), WebhookHandler)
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


_server = None


def get_webhook_server(cfg=None):
    global _server
    if _server is None:
        port = (cfg or {}).get("webhook_port", 9000)
        host = (cfg or {}).get("webhook_host", "0.0.0.0")
        _server = WebhookServer(host=host, port=port)
    return _server


def webhook_start(port=None):
    global _server
    if _server is None:
        _server = WebhookServer(port=port or 9000)
    r = _server.start()
    return r


def webhook_stop():
    global _server
    if _server:
        _server.stop()
        return True
    return False


def webhook_recent_events(n=20):
    try:
        path = _user_data_dir() / "webhook_events.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        out = []
        for ln in lines[-n:]:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
        return out
    except Exception:
        return []


def _esc(s):
    if not isinstance(s, str):
        s = str(s)
    return (s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


STATUS_PAGE_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>小臭玩AI · Webhook</title>
<style>
  body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;max-width:720px;margin:24px auto;padding:0 16px;color:#222;line-height:1.5}
  h1{font-size:20px;margin:0 0 4px}
  .ok{color:#1a9e3e;font-weight:600;margin:6px 0}
  .box{background:#f6f8fc;border:1px solid #e3e8f0;border-radius:10px;padding:12px 16px;margin:14px 0}
  code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;background:#eef2f8;padding:1px 6px;border-radius:4px}
  pre{white-space:pre-wrap;word-break:break-all;background:#fff;border:1px solid #eee;border-radius:6px;padding:8px;margin:6px 0 0;font-size:12px}
  ul{list-style:none;padding:0;margin:0}
  li{padding:8px 0;border-bottom:1px solid #f0f0f0}
  .kind{display:inline-block;background:#1A73E8;color:#fff;border-radius:4px;padding:1px 8px;font-size:12px}
  .t{color:#999;font-size:12px;margin-left:8px}
  .muted{color:#999}
</style></head><body>
<h1>⚡ 小臭玩AI · Webhook 服务</h1>
<p class="ok">● 运行中</p>
<div class="box">
  <div>局域网访问：<code>http://{HOST}</code></div>
  <div>本机访问：<code>http://127.0.0.1:9000</code></div>
</div>
<div class="box">
  <b>接口说明</b>
  <ul>
    <li><code>GET /health</code> — 健康检查（JSON）</li>
    <li><code>GET /</code> — 本状态页</li>
    <li><code>POST /webhook/github</code> — GitHub webhook 接收</li>
    <li><code>POST /webhook/custom</code> — 自定义 webhook</li>
    <li><code>POST /api/trigger</code> — 跨应用触发器</li>
  </ul>
</div>
<div class="box">
  <b>最近事件</b>
  <ul>{EVENTS}</ul>
</div>
<p class="muted">事件记录于 ~/Documents/小臭玩AI/webhook_events.jsonl</p>
</body></html>"""
