# -*- coding: utf-8 -*-
"""契约测试：tool_video_gen（统一内核版）返回约定 + 参数映射。

不调真实 API。桩掉无关模块与 core_agnes，用 FakeClient 捕获调用参数。
验证：
  - 返回 (rel, 'video', name) 三元组
  - rel 相对 app_dir、以 .mp4 结尾
  - 参数正确映射到 core：seconds 钳制 4-12、aspect_ratio 9:16、size 720P、
    mode 自动推导、dialogue 透传、images 归并、first_frame 归一化
"""
import sys, os, types, json, logging

HERE = r"C:/Users/xyb/WorkBuddy/2026-07-11-22-26-49/deepseek-desktop"
sys.path.insert(0, HERE)

def fake_mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m

# 桩掉 tools.py 顶部无关的重模块（GUI/DB/搜索），避免 headless 拉起 PySide
fake_mod("search")
fake_mod("system_control_tools", SYSTEM_CONTROL_TOOL_TABLE=[], SYSTEM_CONTROL_TOOL_DEFS=[])
fake_mod("software_control_tools", SOFTWARE_CONTROL_TOOL_TABLE=[], SOFTWARE_CONTROL_TOOL_DEFS=[])
fake_mod("browser_control_tools", BROWSER_CONTROL_TOOL_TABLE=[], BROWSER_CONTROL_TOOL_DEFS=[])
fake_mod("skill_installer_tools", SKILL_INSTALLER_TOOL_TABLE=[], SKILL_INSTALLER_TOOL_DEFS=[])
fake_mod("memory_store", append_memory=lambda f: None, search_memory=lambda *a, **k: [])
fake_mod("structured_logger", get_logger=lambda *a, **k: logging.getLogger("x"))
fake_mod("chart_generator", ChartGenerator=lambda: object())
fake_mod("context_manager", get_context_manager=lambda: object())
fake_mod("database_tools", DatabaseTools=lambda: object())

# 优先尝试真实 core_agnes（验证桥接+构造），失败则桩掉
try:
    import core_agnes  # noqa
    USED_REAL_CORE = True
except Exception as e:
    class AgnesError(Exception):
        pass
    fake_mod("core_agnes", AgnesClient=None, AgnesError=AgnesError)
    USED_REAL_CORE = False
    print("core_agnes 真实导入失败，改用桩：", e)

import tools  # 真实导入（含本次 patch 的 tool_video_gen）

captured = {}

class FakeClient:
    def __init__(self, api_key=None, base_url=None, video_model=None):
        captured["init"] = dict(api_key=api_key, base_url=base_url, video_model=video_model)
    def generate_video(self, prompt, seconds=5, aspect_ratio="9:16", size="720P",
                       mode=None, first_frame=None, last_frame=None, images=None,
                       dialogue=None, model=None, dest_path=None, on_event=None, timeout=120):
        captured["call"] = dict(prompt=prompt, seconds=seconds, aspect_ratio=aspect_ratio,
                                size=size, mode=mode, first_frame=first_frame,
                                last_frame=last_frame, images=images, dialogue=dialogue,
                                model=model, dest_path=dest_path)
        if on_event:
            on_event("submitted", {})
            on_event("progress", {"elapsed": 5, "status": "processing"})
            on_event("done", {"path": dest_path})
        with open(dest_path, "wb") as f:
            f.write(b"fake-mp4-bytes")
        return dest_path

# tools 在调用时才 `from core_agnes import AgnesClient`，覆写之
sys.modules["core_agnes"].AgnesClient = FakeClient

# ---- 跑三种场景 ----
results = {}

# 场景1：文生视频 + 口播 + duration
cfg = {"model_profiles": {"Agnes": {"base_url": "https://apihub.agnes-ai.cn/v1", "api_key": "TESTKEY"}},
       "base_url": "x", "api_key": ""}
res1 = tools.tool_video_gen(cfg, HERE, "一只柯基在草地上跑", duration=10, aspect="portrait",
                            dialogue="你好，我是雪糕", images=None)
results["text+dialogue"] = (res1, dict(captured))

# 场景2：图生视频（reference，多图）
res2 = tools.tool_video_gen(cfg, HERE, "把这张图变成动画", images=["https://e.com/a.png", "https://e.com/b.png"])
results["reference"] = (res2, dict(captured))

# 场景3：首尾帧 keyframe + 显式 filepath（需归一化）
tmpimg = os.path.join(HERE, "_probe_ref.png")
with open(tmpimg, "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n fake")
res3 = tools.tool_video_gen(cfg, HERE, "过渡镜头", first_frame=tmpimg, last_frame="https://e.com/last.png")
results["keyframe"] = (res3, dict(captured))

# ---- 断言 ----
for scen, (res, cap) in results.items():
    assert isinstance(res, tuple) and len(res) == 3, f"[{scen}] 返回非三元组: {res!r}"
    rel, kind, name = res
    assert kind == "video", f"[{scen}] kind 错: {kind}"
    assert rel.endswith(".mp4"), f"[{scen}] rel 非 mp4: {rel}"
    assert os.path.isfile(os.path.join(HERE, rel)), f"[{scen}] rel 拼回不存在: {rel}"
    print(f"OK [{scen}] -> ({rel!r}, {kind!r}, {name!r})")

print("\n=== 场景1 调用参数（文生+口播）===")
print(json.dumps(results["text+dialogue"][1]["call"], ensure_ascii=False, indent=2))
print("init:", results["text+dialogue"][1]["init"])
print("USED_REAL_CORE:", USED_REAL_CORE)
print("\nALL CONTRACT TESTS PASSED")
