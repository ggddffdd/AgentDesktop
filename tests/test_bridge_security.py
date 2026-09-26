# -*- coding: utf-8 -*-
"""浏览器桥接安全回归测试

覆盖本轮修复的三项 + 既有边界：
- #755 认证收紧：仅接受 Header X-Bridge-Token，`?token=` 查询参数必须被拒
- #754 请求加固：负数 Content-Length → 400；超限 body → 413；
                 无 token / 错 token → 401；空 payload → 400
- #756 诚实回执：未确认 → 202 accepted(delivered=false)
                 注入成功 → 200(delivered=true)
                 注入失败 → 200(delivered=false) + detail（不再假成功）
- 既有边界：/health 免鉴权、GET /page → 404、已配对后 /pair 无 token → 401

纯标准库，无 GUI 依赖（browser_bridge 只 import 标准库）：
    python tests/test_bridge_security.py
退出码 0=全过，1=有失败。
"""
import json
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import browser_bridge as bb

PASS = 0
FAIL = 0

TOKEN = "b" * 32
PORT = None
BASE = None


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


def post(path, data, token=None):
    """走 urllib 发 POST。token 仅通过 X-Bridge-Token 头发送。"""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Bridge-Token"] = token
    req = urllib.request.Request(
        BASE + path, data=json.dumps(data).encode("utf-8"),
        headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body


def raw_status(path, header_lines):
    """裸 socket 只发请求头（不带 body），用于构造 urllib 发不出的畸形请求。

    返回 HTTP 状态码；拿不到响应返回 0。
    """
    code = 0
    try:
        s = socket.create_connection(("127.0.0.1", PORT), timeout=10)
    except Exception:
        return 0
    try:
        req = "POST %s HTTP/1.1\r\nHost: 127.0.0.1\r\n" % path
        req += "".join(h + "\r\n" for h in header_lines) + "\r\n"
        try:
            s.sendall(req.encode("utf-8"))
        except Exception:
            pass  # 服务端可能已响应并关闭连接，仍尝试读取
        s.settimeout(5)
        raw = b""
        while b"\r\n\r\n" not in raw:
            try:
                chunk = s.recv(4096)
            except Exception:
                break
            if not chunk:
                break
            raw += chunk
        first = raw.split(b"\r\n", 1)[0].decode("utf-8", "ignore")
        parts = first.split(" ")
        if len(parts) > 1 and parts[1].isdigit():
            code = int(parts[1])
    finally:
        try:
            s.close()
        except Exception:
            pass
    return code


def start_bridge():
    """在测试专用端口启动桥接（避开桌面端正在用的 9100）。"""
    global PORT, BASE
    for candidate in (19100, 19101, 19102, 19103):
        try:
            bb.browser_bridge_stop()
        except Exception:
            pass
        tok = bb.browser_bridge_start({
            "browser_bridge_port": candidate,
            "browser_bridge_token": TOKEN,
        })
        if tok:
            PORT = candidate
            BASE = "http://127.0.0.1:%d" % candidate
            return True
    return False


def main():
    print("== 浏览器桥接安全测试 ==")

    if not start_bridge():
        print("  [FAIL] 桥接启动失败（端口 19100-19103 均不可用）")
        print("\n结果：PASS=0  FAIL=1")
        return 1
    print(f"  [INFO] 桥接已启动于 127.0.0.1:{PORT}")

    # #756：把等待确认的时限压到 1s，"未确认"分支不必真等 12 秒
    bb.DELIVERY_WAIT = 1

    try:
        # ---------- #755 认证收紧 ----------
        print("-- 认证（#755）--")
        st, _ = post("/page", {"title": "x", "text": "hi"})
        check("无 token 被拒(401)", st == 401, f"status={st}")

        st, _ = post("/page", {"title": "x", "text": "hi"}, token="wrong-token")
        check("错误 token 被拒(401)", st == 401, f"status={st}")

        # 核心回归：token 放查询参数不再被接受（此前会通过 → 泄露面）
        bb.set_event_callback(lambda k, p: None)
        st, _ = post("/page?token=%s" % TOKEN, {"title": "x", "text": "hi"})
        check("?token= 查询参数被拒(401)", st == 401, f"status={st}")

        # ---------- #756 诚实回执 ----------
        print("-- 投递回执（#756）--")
        bb.set_event_callback(lambda k, p: None)   # 主程序不回报 → 未确认
        st, body = post("/page", {"title": "测试页", "text": "正文内容" * 5}, token=TOKEN)
        check("未确认返回 202 accepted", st == 202, f"status={st} body={body}")
        check("未确认 delivered=false", isinstance(body, dict) and body.get("delivered") is False,
              f"body={body}")
        check("回执带 delivery_id", isinstance(body, dict) and len(str(body.get("delivery_id", ""))) == 32,
              f"body={body}")

        # 主程序回报成功 → 200 delivered=true
        def _cb_ok(kind, payload):
            bb.report_delivery(payload.get("delivery_id", ""), True)

        bb.set_event_callback(_cb_ok)
        st, body = post("/page", {"title": "测试页", "text": "正文内容" * 5}, token=TOKEN)
        check("注入成功返回 200", st == 200, f"status={st} body={body}")
        check("注入成功 delivered=true", isinstance(body, dict) and body.get("delivered") is True,
              f"body={body}")

        # 主程序回报失败（如被草稿保护拦截）→ 200 但 delivered=false + detail
        def _cb_fail(kind, payload):
            bb.report_delivery(payload.get("delivery_id", ""), False, "draft protected")

        bb.set_event_callback(_cb_fail)
        st, body = post("/page", {"title": "测试页", "text": "正文内容" * 5}, token=TOKEN)
        check("注入失败返回 200", st == 200, f"status={st} body={body}")
        check("注入失败 delivered=false（不假成功）",
              isinstance(body, dict) and body.get("delivered") is False, f"body={body}")
        check("注入失败回传失败原因",
              isinstance(body, dict) and "draft protected" in str(body.get("detail", "")),
              f"body={body}")

        # ---------- #754 请求加固 ----------
        print("-- 请求加固（#754）--")
        st, _ = post("/page", {}, token=TOKEN)
        check("空 payload 被拒(400)", st == 400, f"status={st}")

        c = raw_status("/page", [
            "X-Bridge-Token: %s" % TOKEN,
            "Content-Type: application/json",
            "Content-Length: -1",
        ])
        check("负数 Content-Length 被拒(400)", c == 400, f"status={c}")

        c = raw_status("/page", [
            "X-Bridge-Token: %s" % TOKEN,
            "Content-Type: application/json",
            "Content-Length: 999999999",
        ])
        check("超限 body 被拒(413)", c == 413, f"status={c}")

        # 常量护栏：上限与并发上限不应被无意放宽
        check("MAX_BODY 仍为 256KB", bb.MAX_BODY == 256 * 1024, f"MAX_BODY={bb.MAX_BODY}")
        check("MAX_CONCURRENT 仍为 16", bb.MAX_CONCURRENT == 16, f"MAX_CONCURRENT={bb.MAX_CONCURRENT}")

        # ---------- 既有边界 ----------
        print("-- 既有边界 --")
        req = urllib.request.Request(BASE + "/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            hb = json.loads(r.read().decode("utf-8"))
        check("健康检查免鉴权可访问", hb.get("status") == "ok", str(hb))

        try:
            urllib.request.urlopen(BASE + "/page", timeout=5)
            check("GET /page 应为 404", False, "竟返回 200")
        except urllib.error.HTTPError as e:
            check("GET /page 不存在(404)", e.code == 404, f"status={e.code}")

        # 已配对状态下，/pair 无 token 应被拒（防任意网页重置配对码）
        st, _ = post("/pair", {})
        check("已配对后 /pair 无 token 被拒(401)", st == 401, f"status={st}")

    finally:
        bb.set_event_callback(None)
        try:
            bb.browser_bridge_stop()
        except Exception:
            pass

    print(f"\n结果：PASS={PASS}  FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
