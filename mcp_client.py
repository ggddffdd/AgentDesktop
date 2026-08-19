# -*- coding: utf-8 -*-
"""MCP stdio 客户端模块 — 管理 MCP 服务器进程并提供工具调用"""

import subprocess
import json
import threading
import logging
import os

log = logging.getLogger("dsdesktop")


class McpClient:
    """MCP stdio 客户端，管理一个 MCP 服务器进程"""

    def __init__(self, name, command, args=None, env=None, cwd=None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd
        self.process = None
        self.tools = []               # 从服务器获取的工具列表（OpenAI function calling 格式）
        self._request_id = 0
        self._pending = {}
        self._lock = threading.Lock()
        self._reader_thread = None
        self._running = False

    def start(self):
        """启动 MCP 服务器进程，发送 initialize 请求，获取 tools/list。
        返回 True/False 表示是否成功启动。
        """
        try:
            cmd = [self.command] + self.args
            merged_env = os.environ.copy()
            merged_env.update(self.env)
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged_env,
                cwd=self.cwd,
                text=False,
            )
        except Exception as e:
            log.error("MCP [%s] 启动进程失败: %s", self.name, e)
            return False

        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        # 发送 initialize 请求
        init_result = self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "deepseek-desktop", "version": "1.0.0"},
        })
        if init_result is None or "error" in init_result:
            log.error("MCP [%s] initialize 失败: %s", self.name, init_result)
            self.stop()
            return False

        # 发送 initialized 通知
        self._send_notification("notifications/initialized", {})

        # 获取 tools/list
        tools_result = self._send_request("tools/list", {})
        if tools_result is None:
            log.error("MCP [%s] tools/list 失败", self.name)
            self.stop()
            return False

        raw_tools = tools_result.get("result", {}).get("tools", [])
        self.tools = self._convert_mcp_tools(raw_tools)
        log.info("MCP [%s] 已连接，发现 %d 个工具", self.name, len(self.tools))
        return True

    # ---- 内部方法 --------------------------------------------------

    def _convert_mcp_tools(self, mcp_tools):
        """将 MCP 工具定义转换为 OpenAI function calling 格式"""
        converted = []
        for t in mcp_tools:
            input_schema = t.get("inputSchema", {})
            parameters = {
                "type": input_schema.get("type", "object"),
                "properties": input_schema.get("properties", {}),
            }
            required = input_schema.get("required")
            if required:
                parameters["required"] = required
            converted.append({
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": parameters,
                },
            })
        return converted

    def call_tool(self, tool_name, arguments):
        """调用 MCP 工具，返回结果字符串"""
        result = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if result is None:
            return f"MCP 工具 [{tool_name}] 调用失败：无响应"
        if "error" in result:
            return f"MCP 工具 [{tool_name}] 调用失败：{result['error']}"
        content = result.get("result", {}).get("content", [])
        if not content:
            return f"MCP 工具 [{tool_name}] 返回空结果"
        texts = []
        for item in content:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
        return "\n".join(texts) if texts else f"MCP 工具 [{tool_name}] 返回非文本内容"

    def stop(self):
        """关闭 MCP 服务器进程"""
        self._running = False
        if self.process:
            try:
                self.process.stdin.close()
            except Exception:
                pass
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
        log.info("MCP [%s] 已关闭", self.name)

    def _reader_loop(self):
        """后台线程读取 stdout 响应（JSON-RPC 换行分隔）"""
        try:
            while self._running and self.process and self.process.stdout:
                line = self.process.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8"))
                except Exception:
                    continue
                rid = msg.get("id")
                if rid is not None:
                    with self._lock:
                        entry = self._pending.pop(rid, None)
                    if entry:
                        entry["result"] = msg
                        entry["event"].set()
        except Exception as e:
            log.debug("MCP [%s] reader 退出: %s", self.name, e)

    def _send_request(self, method, params=None):
        """发送 JSON-RPC 请求，同步等待响应（超时 30s）"""
        if not self.process or self.process.poll() is not None:
            return None
        with self._lock:
            self._request_id += 1
            rid = self._request_id
        req = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": params or {},
        }
        event = threading.Event()
        with self._lock:
            self._pending[rid] = {"event": event, "result": None}
        try:
            line = json.dumps(req, ensure_ascii=False) + "\n"
            self.process.stdin.write(line.encode("utf-8"))
            self.process.stdin.flush()
        except Exception as e:
            log.error("MCP [%s] 发送请求失败: %s", self.name, e)
            with self._lock:
                self._pending.pop(rid, None)
            return None
        if not event.wait(30):
            with self._lock:
                self._pending.pop(rid, None)
            return None
        return event["result"]

    def _send_notification(self, method, params=None):
        """发送 JSON-RPC 通知（无需响应）"""
        if not self.process or self.process.poll() is not None:
            return
        req = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        try:
            line = json.dumps(req, ensure_ascii=False) + "\n"
            self.process.stdin.write(line.encode("utf-8"))
            self.process.stdin.flush()
        except Exception as e:
            log.error("MCP [%s] 发送通知失败: %s", self.name, e)
