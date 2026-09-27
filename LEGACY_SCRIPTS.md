# 遗留脚本清单（仓库根目录的 `test_*.py` / `verify_*.py`）

> 标注日期：2026-09-28（v4.175.0 期间整理）

## 为什么要这份清单

统一入口只收集 **`tests/test_*.py`**。根目录这些脚本**从来不会被 `run_all.py` 跑** ——
「有测试」≠「测试会跑」。本目录里就发生过这种事：

> `test_v4102_vision.py` 第 12 行断言着 `_model_supports_vision("deepseek-flash") is True`，
> **一直是红的**，但因为它在根目录，没人知道。等到线上真的出了这个 bug 才发现。
> 更糟的是：跑它的时候，它手写的一张**坏 PNG 被写进了用户的真实会话**，
> 导致那个会话此后**每次带图都 400**（v4.175.0 修的那个事故）。

## 规矩

1. **想让测试真的跑起来** → 放 `tests/test_*.py`，且必须能 `python tests/xxx.py` 独立运行
   （run_all 以 `cwd=仓库根` 跑，但 `sys.path[0]` 是 `tests/`，所以搬进来的文件要显式
   `sys.path.insert(0, 仓库根)`）。
2. **留在根目录的** = 不参与统一回归。分两类：
   - **有危害**（会写真实用户数据 / 真发网络请求）→ 文件头已加 `⚠️` 警示块，勿随手跑。
   - **已过期**（依赖的产物已不存在，跑起来 `FileNotFoundError`）→ 留作历史参考，别当测试用。
3. 跑任何根目录脚本前：**先备份 `~/Documents/小臭玩AI`**（会话 / `logs` / `incoming`）。

---

## 一、已搬进 `tests/`（hermetic 且通过，现在每次回归都会跑）

| 文件 | 说明 |
|---|---|
| `test_audio_regress.py` | 导演台「成片无声」回归（非 utf-8 locale 下 subprocess 读 stderr） |
| `test_hotfix15.py` | 自进化双轨：轨迹自动提炼 + 技能审核队列 |
| `test_v4102_1021_twin_bg.py` | 数字人分身：场景为空时不擅自换背景 |
| `test_v4102_image_compress.py` | `_compress_image_for_api` 缩放/转 JPEG/体积 |
| `test_v4102_twin_v2.py` | 数字人 v2：反僵尸微动作 / 长口播分段 / AI 标识 / VLM 质检 |

> `verify_antifold.py` 虽然也安全且通过，但**它校验的是已打包的 EXE**（没有 dist 就会红），
> 放进统一入口会引入对构建产物的依赖，故留在根目录。

## 二、有危害 —— 留在根目录，文件头已加 ⚠️ 警示（22 个）

| 文件 | 主要危害 |
|---|---|
| `test_v4102_vision.py` | **写真实会话 + 手写坏 PNG**（本次事故源头） |
| `test_v4102_file_marker.py` / `test_v4102_filename_sanitize.py` | 往用户 `incoming/` 写测试图片 |
| `test_v4102_real_qt_stream.py` / `test_v4102_stream_reasoning.py` | 起真实 GUI / 走真实日志 |
| `test_v4102_fix9_done.py` / `test_v4102_fix9_e2e.py` | 走真实调用链 |
| `test_v4102_fix10.py` / `fix11.py` / `fix12.py` / `ref5.py` | 同上 |
| `test_v4102_fix10_api.py` / `fix11_api.py` | 直连线上 API |
| `test_real_api_vision.py` / `test_agnes_video_v25.py` | 直连线上 API（真花钱） |
| `test_browser_route.py` / `test_res_selector.py` | 走真实路由 / 起 GUI |
| `test_v495.py` / `test_v496.py` / `test_v497.py` | 走真实调用链 |
| `verify_v4101_resume.py` | 走真实断点续跑链路 |

## 三、已过期 —— 跑不过，别当测试用（20 个）

大部分失败于 `FileNotFoundError`（要核验的构建产物已不在）：

```
test_v4102_1021_import.py      verify_agnes_v25_pkg.py        verify_da_audio_fix.py
verify_deliverfix_pkg.py       verify_pyz_v467.py             verify_pyz_v468.py
verify_pyz_v470.py             verify_settings_font_pkg.py    verify_v4100.py
verify_v4102_1021_pkg.py       verify_v4102_fix9_pkg.py       verify_v4102_fix10_pkg.py
verify_v4102_fix11_pkg.py      verify_v4102_fix12_pkg.py      verify_v4102_vision_pkg.py
verify_v451.py                 verify_v452.py                 verify_v453.py
verify_v498.py                 verify_v499.py
```

## 四、可安全运行的一次性核验脚本（不进统一入口）

`verify_pyz_v486/487/488/489.py`、`verify_build_v485.py`、`verify_exe_fix.py`、`verify_antifold.py`
—— 都是**构建后**验证「改动有没有真进包」的脚本，跑起来很快且通过，
但它们依赖 `dist/`，不适合塞进每次回归。打包后想复核，单独跑即可。

---

## 附：盘点是怎么做的（可复用）

`run_all.py` 只 glob `tests/test_*.py`，所以：

```python
# 1) 静态危害扫描：看有没有这些痕迹
#    写会话：store.active() / messages = / .send( / _start_stream(
#    写用户目录：incoming / WORKSPACE_DIR / Documents/小臭玩AI
#    网络：urllib / requests / http:// / socket / api.deepseek
#    起 GUI：QApplication
# 2) 只实跑"看起来安全"的（有危害的别跑，跑了就污染）—— rc==0 才算可搬
```
归类原则：**能独立运行、不依赖网络/用户数据 → 搬进 tests/**；
其余留在根目录并明确标注，别让后人误以为"仓库里有测试覆盖"。
