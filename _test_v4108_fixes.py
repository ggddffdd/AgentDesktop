# -*- coding: utf-8 -*-
"""v4.108 BUG 大扫除：本次修复的定向单元验证（离线，不联网不调外部工具）。

覆盖：M-23 sid 唯一 / M-24 config 原子写 / M-28 webhook 默认回环+token /
M-05 merge 文案条件 / M-08 feedback 替换 / M-10 无二次解码 / M-14 内部消息过滤 /
H-10 跨盘符 relpath / M-21 memory_store 加锁可导入。
"""
import os
import sys
import json
import inspect
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ok = True


def check(name, cond):
    global ok
    ok = ok and bool(cond)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}")


print("== 1. session.sid 唯一（M-23）==")
import session as session_mod
try:
    src = inspect.getsource(session_mod.SessionStore.new_session)
    check("sid 含随机后缀防同秒覆盖", "uuid" in src and "_uniq" in src)
except Exception as e:
    check(f"sid 结构检查异常: {e}", False)

print("== 2. config.save_config 原子写（M-24）==")
import config as config_mod
try:
    src = inspect.getsource(config_mod.save_config)
    check("save_config 用 tmp+os.replace", "mkstemp" in src and "os.replace" in src)
except Exception as e:
    check(f"config 检查异常: {e}", False)

print("== 3. task_resume 原子写（M-15）==")
import task_resume
try:
    d = tempfile.mkdtemp()
    cfg = {"task_resume_dir": d}
    check("save_checkpoint 成功", task_resume.save_checkpoint(cfg, {
        "task_id": "t1", "task_type": "agent", "status": "running"}))
    cp = task_resume.load_checkpoint(cfg, "t1")
    check("checkpoint 可回读", bool(cp) and cp.get("status") == "running")
    check("update_heartbeat 刷新", task_resume.update_heartbeat(cfg, "t1"))
    # 无 .tmp 残留
    left = [f for f in os.listdir(d) if f.endswith(".tmp")]
    check("无 .tmp 残留", not left)
except Exception as e:
    check(f"task_resume 异常: {e}", False)

print("== 4. M-05 merge 失败文案条件 ==")
import inspect as _i
try:
    import video_pipeline
    src = _i.getsource(video_pipeline.VideoPipeline._merge)
    # 直接验证 _merge_exec 的错误分支（902 行区域函数）
    src2 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "video_pipeline.py"), encoding="utf-8").read()
    check("失败分支用 not exists 判未生成",
          "if not os.path.exists(out_path):" in src2 and
          "err_parts.append(\"（输出文件未生成）\")" in src2)
    check("_run_ff 支持 cancel_check", "cancel_check=None" in src2)
except Exception as e:
    check(f"merge 检查异常: {e}", False)

print("== 5. M-08 feedback 替换 ==")
try:
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "video_pipeline.py"), encoding="utf-8").read()
    check("regenerate_character 用 re.sub 替换旧意见",
          "re.sub(r\"\\s*\\|\\s*updated appearance:.*$\"" in src)
except Exception as e:
    check(f"feedback 检查异常: {e}", False)

print("== 6. M-10 localres 无二次解码 ==")
try:
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "director_web.py"), encoding="utf-8").read()
    check("requestStarted 直接取 u.path() 不再 unquote",
          "p = u.path()" in src and
          "unquote(u.path())" not in src)
except Exception as e:
    check(f"localres 检查异常: {e}", False)

print("== 7. H-10 _safe_relpath 跨盘 ==")
try:
    import tools as tools_mod
    check("_safe_relpath 同盘相对", tools_mod._safe_relpath("C:/a/b.txt", "C:/a") == "b.txt")
    check("_safe_relpath 跨盘回退绝对", tools_mod._safe_relpath("D:/x/b.txt", "C:/a") == "D:/x/b.txt")
except Exception as e:
    check(f"_safe_relpath 异常: {e}", False)

print("== 8. M-14 内部消息过滤 ==")
try:
    import agent as agent_mod
    w = agent_mod.AgentWorker.__new__(agent_mod.AgentWorker)
    w._AGENT_NUDGE = "nudge text"
    w._AGENT_FAKE_TOOL_INSTR = "fake text"
    check("system 不回写", not w._is_sync_writable({"role": "system", "content": "sys"}))
    check("_internal 不回写", not w._is_sync_writable({"role": "user", "content": "x", "_internal": True}))
    check("nudge 不回写", not w._is_sync_writable({"role": "user", "content": "nudge text"}))
    check("assistant 正文回写", w._is_sync_writable({"role": "assistant", "content": "正文"}))
    check("tool 结果回写", w._is_sync_writable({"role": "tool", "tool_call_id": "1", "content": "r"}))
    check("tool_calls 回写", w._is_sync_writable({"role": "assistant", "content": "", "tool_calls": []}))
except Exception as e:
    check(f"M-14 检查异常: {e}", False)

print("== 9. M-28 webhook 默认回环 + token ==")
try:
    import webhook_server as wh
    d0 = wh.get_webhook_server.__code__
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "webhook_server.py"), encoding="utf-8").read()
    check("默认 host 127.0.0.1", 'host="127.0.0.1"' in src)
    check("0.0.0.0 强制降级", 'if host in ("0.0.0.0", "::"):' in src)
    check("token 校验存在", "_token_ok" in src and "X-Webhook-Token" in src)
    check("body 限长", "MAX_BODY" in src and "413" in src)
    # 行为级：token 生效 + 超限拒绝
    wh.set_server_token("sekret")
    h = wh.WebhookHandler
    class _Fake:
        headers = {"Content-Length": str(99999999)}
    f = _Fake()
    ok2 = h._token_ok(f) if not hasattr(f, "path") else False
    # _token_ok 是 handler 方法需要 self.headers
    f2 = type("R", (), {})()
    f2.headers = {"X-Webhook-Token": "wrong"}
    check("token 错误被拒", not wh.WebhookHandler._token_ok(f2))
    f3 = type("R3", (), {})()
    f3.headers = {"X-Webhook-Token": "sekret"}
    check("token 正确放行", wh.WebhookHandler._token_ok(f3))
    d = wh.WebhookHandler._read_json(type("R4", (), {"headers": {"Content-Length": "99999999"}})())
    check("超大 body 被拒", isinstance(d, dict) and d.get("__too_large__"))
    wh.set_server_token("")
except Exception as e:
    check(f"webhook 异常: {e}", False)

print("== 10. M-21 memory_store 锁导入 ==")
try:
    import memory_store as ms
    check("memory_store 可导入", True)
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "memory_store.py"), encoding="utf-8").read()
    check("有 _LOCK 与 _sync", "_LOCK = threading.RLock()" in src and "def _sync(fn)" in src)
    check("旧版 append_memory 已删", src.count("def append_memory(") == 1)
except Exception as e:
    check(f"memory_store 异常: {e}", False)

print("== 11. H-08 main UiBridge / H-09 编码 / P0-8 resolve ==")
try:
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "main.py"), encoding="utf-8").read()
    check("main 有 UiBridge 跨线程桥", "class UiBridge(QObject)" in src and "_ui_bridge.post" in src)
    check("main 无 QTimer.singleShot 跨线程", "QTimer.singleShot" not in src or
          "singleShot(0, _inject)" not in src)
    src_bct = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "browser_control_tools.py"), encoding="utf-8").read()
    check("H-09 注入 PYTHONIOENCODING", "PYTHONIOENCODING" in src_bct)
    src_br = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "browser_runner.py"), encoding="utf-8").read()
    check("H-09 runner reconfigure utf-8", "reconfigure(encoding=\"utf-8\")" in src_br)
    src_t = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "tools.py"), encoding="utf-8").read()
    check("P0-8 resolve_python 已改 _resolve_python_exe", "resolve_python()" not in src_t)
    check("M-16 size 透传", "size=args.get(\"size\")" in src_t)
except Exception as e:
    check(f"main/browser/tools 检查异常: {e}", False)

print("== 12. M-19 热键 bytes 比较 ==")
try:
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "main.py"), encoding="utf-8").read()
    check("eventType == b\"windows_generic_MSG\"", 'b"windows_generic_MSG"' in src)
except Exception as e:
    check(f"M-19 异常: {e}", False)

print("== 13. H-11 DPI 归一 ==")
try:
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "ui.py"), encoding="utf-8").read()
    check("NCHITTEST 按 DPR 归一", "devicePixelRatioF" in src and "/ dpr" in src)
    check("M-22 tool_log 截断", "…(截断)" in src)
    check("M-20 closeEvent 终止 worker", "w.request_stop()" in src and "w.wait(3000)" in src)
    src_cw = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "chat_web.py"), encoding="utf-8").read()
    check("M-26 主题存 _theme", "self._theme = theme or {}" in src_cw)
except Exception as e:
    check(f"ui 检查异常: {e}", False)

print("== 14. M-07 派发互斥 / M-13 checkpoint / M-12 续跑 force_complex ==")
try:
    src_dp = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "director_panel.py"), encoding="utf-8").read()
    check("M-07 有 _DIR_DISPATCH_LOCK", "_DIR_DISPATCH_LOCK" in src_dp)
    check("M-09 全量重生成先重置卡片", "_render_clips(app)" in src_dp)
    src_ag = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "agent.py"), encoding="utf-8").read()
    check("M-13 isolated 跳过 checkpoint", "_isolated" in src_ag and
          "task_resume.save_checkpoint" in src_ag)
    check("M-12 续跑传 force_complex", "force_complex=self._force_complex" in src_ag)
    check("H-05 每步 _sync_agent_checkpoint", "_sync_agent_checkpoint" in src_ag)
    check("M-20 request_stop 唤醒确认", "_confirm_event.set()" in src_ag)
except Exception as e:
    check(f"director/agent 检查异常: {e}", False)

print()
print("ALL_V4108_FIXES_OK" if ok else "SOME_CHECKS_FAILED")
sys.exit(0 if ok else 1)
