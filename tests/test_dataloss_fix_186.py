# -*- coding: utf-8 -*-
"""P1-6 legion_chat 读败不清零 + P1-7 memory_store 空串回写 + P1-9 视频产物体积
回归测试（v4.186.0 审查修复）。

直接用项目解释器跑：python tests/test_dataloss_fix_186.py
不依赖 pytest，沿用项目 check() 累积计数 + main() 返回 0/1 的约定。
"""
import os
import sys
import io
import json
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_P = _F = 0
def check(name, cond, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("  PASS %s %s" % (name, detail))
    else:
        _F += 1
        print("  FAIL %s %s" % (name, detail))


def main():
    import memory_store as ms

    print("=== [A] memory_store 正常 append 回归（读-改-写链路没被修坏）===")
    tmp = tempfile.mkdtemp(prefix="ms_probe_")
    try:
        mem_path = os.path.join(tmp, "memory.md")
        ms.MEMORY_PATH = mem_path          # 指向临时文件，不碰真实数据
        with open(mem_path, "w", encoding="utf-8") as f:
            f.write("## 2026-09-01 10:00\n旧记忆条目A\n")
        r1 = ms.append_memory("新记忆条目B")
        check("首次追加返回成功文案", r1.startswith("已写入"), "-> %s" % r1[:30])
        txt = open(mem_path, encoding="utf-8").read()
        check("旧记忆仍在（没被清空）", "旧记忆条目A" in txt)
        check("新记忆已写入", "新记忆条目B" in txt)
        r2 = ms.append_memory("新记忆条目C")
        txt2 = open(mem_path, encoding="utf-8").read()
        check("二次追加累积不丢", txt2.count("##") == 3, "-> %d 个条目头" % txt2.count("##"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=== [B] P1-7: 读失败时拒绝追加（修复前会把空历史回写清空全部记忆）===")
    orig_ex = ms._read_text_file_ex
    ms._read_text_file_ex = lambda p: False      # 模拟权限/IO/解密失败
    try:
        tmp2 = tempfile.mkdtemp(prefix="ms_probe2_")
        try:
            mem_path2 = os.path.join(tmp2, "memory.md")
            ms.MEMORY_PATH = mem_path2
            # 预置一份"磁盘上有内容但读不到"的状态：直接写文件绕过写路径
            with open(mem_path2, "w", encoding="utf-8") as f:
                f.write("## 2026-09-01 10:00\n磁盘上真实存在的旧记忆\n")
            r = ms.append_memory("试图追加的新记忆")
            check("返回拒绝文案（非'已写入'）", "拒绝" in r, "-> %s" % r[:40])
            raw = open(mem_path2, encoding="utf-8").read()
            check("磁盘旧记忆未被覆盖", "磁盘上真实存在的旧记忆" in raw)
            check("新记忆未混入", "试图追加的新记忆" not in raw)
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
    finally:
        ms._read_text_file_ex = orig_ex

    print("=== [C] _read_text_file_ex 三态语义 ===")
    tmp3 = tempfile.mkdtemp(prefix="ms_probe3_")
    try:
        p_missing = os.path.join(tmp3, "nope.md")
        p_ok = os.path.join(tmp3, "ok.md")
        p_bad = os.path.join(tmp3, "bad.md")
        with open(p_ok, "w", encoding="utf-8") as f:
            f.write("正常内容")
        with open(p_bad, "wb") as f:
            f.write(b"\xff\xfe\x00\xd8\x00\xd9")   # 非法 utf-8
        check("不存在 → None", ms._read_text_file_ex(p_missing) is None)
        check("正常 → str", ms._read_text_file_ex(p_ok) == "正常内容")
        check("读失败 → False", ms._read_text_file_ex(p_bad) is False)
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

    print("=== [D] P1-6: legion_chat 读史失败 → 禁回写（不覆盖旧文件）===")
    import legion_chat as lc
    # 绕过 __init__（Qt 依赖），只测 _load_history / _save_history 逻辑
    obj = lc.LegionChatPanel.__new__(lc.LegionChatPanel)
    obj._proj_id = "probe_proj"
    obj.history = []
    obj._history_load_failed = False
    # 指向一个坏档路径，让 open 抛 OSError
    probe_dir = tempfile.mkdtemp(prefix="lc_probe_")
    try:
        chat_file = os.path.join(probe_dir, "probe_proj.json")
        with open(chat_file, "w", encoding="utf-8") as f:
            f.write('{"history": [{"role": "user", "content": "磁盘旧对话"}]}')
        obj._chat_path = lambda: chat_file
        # 1) 模拟读失败：临时把 open 换成必抛 OSError 的版本
        real_open = io.open
        def boom_open(*a, **k):
            if a and str(a[0]) == chat_file and "r" in (a[1] if len(a) > 1 else "r"):
                raise PermissionError("文件被占用（模拟 Defender 瞬时锁）")
            return real_open(*a, **k)
        import builtins
        builtins.open = boom_open
        try:
            obj._load_history()
        finally:
            builtins.open = real_open
        check("读失败置 _history_load_failed", getattr(obj, "_history_load_failed", False))
        check("history 未被清空标记污染（保持[])或保留", isinstance(obj.history, list))
        # 2) 读失败后 _save_history 必须拒绝写盘
        obj.history = [{"role": "user", "content": "本轮新消息"}]
        obj._save_history()
        raw = open(chat_file, encoding="utf-8").read()
        check("旧文件未被空/半份 history 覆盖", "磁盘旧对话" in raw)
        check("新消息没混进文件", "本轮新消息" not in raw)
        # 3) 对照组：正常坏档（JSONDecodeError）应改名留底+允许清空
        with open(chat_file, "w", encoding="utf-8") as f:
            f.write("{broken json")
        obj2 = lc.LegionChatPanel.__new__(lc.LegionChatPanel)
        obj2._proj_id = "probe_proj"
        obj2.history = [{"role": "user", "content": "旧内存"}]
        obj2._history_load_failed = False
        obj2._chat_path = lambda: chat_file
        obj2._load_history()
        check("坏档改名留底", os.path.isfile(chat_file + ".bad." + "20260930") or
              any(f.startswith("probe_proj.json.bak.") is False and f.startswith("probe_proj.json.bad.") for f in os.listdir(probe_dir)))
        check("坏档允许清空重建", obj2.history == [])
        check("坏档不置禁写标志", not getattr(obj2, "_history_load_failed", False))
        # 4) 正常读档路径没被修坏
        with open(chat_file, "w", encoding="utf-8") as f:
            json.dump({"history": [{"role": "你", "content": "正常旧对话"}]}, f)
        obj3 = lc.LegionChatPanel.__new__(lc.LegionChatPanel)
        obj3._proj_id = "probe_proj"
        obj3.history = []
        obj3._history_load_failed = False
        obj3._chat_path = lambda: chat_file
        # 渲染回放需要 Qt：只验证 history 装载，跳过渲染部分
        obj3._render_divider = lambda *a, **k: None
        obj3._render_card = lambda *a, **k: None
        try:
            obj3._load_history()
        except Exception:
            pass
        check("正常坏档外路径可装载 history", any(
            m.get("content") == "正常旧对话" for m in obj3.history))
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)

    print("=== [E] P1-9: 视频完整性守卫源码契约断言（真语句，防注释级假绿）===")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    def has_stmt(path, stmt):
        with io.open(os.path.join(root, path), "r", encoding="utf-8-sig") as f:
            return any(l.strip() == stmt for l in f)
    check("tools.py tool_video_gen 体积拒绝语句",
          has_stmt("tools.py", "if 0 <= _vsz < 10 * 1024:"))
    check("video_pipeline 定义 _MIN_CLIP_BYTES",
          has_stmt("video_pipeline.py", "_MIN_CLIP_BYTES = 10 * 1024"))
    check("video_pipeline 使用 _MIN_CLIP_BYTES 判定",
          has_stmt("video_pipeline.py", "_clip_ok = os.path.getsize(clip) >= _MIN_CLIP_BYTES"))
    ag_path = os.path.join(os.path.dirname(root), "video-agent", "core", "agnes.py")
    if os.path.isfile(ag_path):
        with io.open(ag_path, "r", encoding="utf-8-sig") as f:
            check("core/agnes download got/total 完整性校验",
                  any("got != total" in l for l in f))
    else:
        # v4.239.1（CI 红 → 修）：该判据锚的是仓库外的 video-agent 源码（本机相邻目录），
        # CI 干净 clone 上不存在 → 跳过这一条，不 FileNotFoundError 假红。
        print("  [SKIP] video-agent/core/agnes.py 不在——外部依赖缺失，本条跳过（不冒充通过）")

    print("\n汇总：PASS=%d FAIL=%d" % (_P, _F))
    return 1 if _F else 0


if __name__ == "__main__":
    sys.exit(main())
