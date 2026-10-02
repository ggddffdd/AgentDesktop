# 最小 QGraphicsView 探针：三坑实测报告

> 节点画布设计稿 **第 0 步**。这一步不产出任何功能，只产出「以后正式画布该怎么写」的约束。
> 被测对象：`probe_graphics.py`（单文件）｜判据：`tests/test_probe_graphics.py`（63 条）
> 判据有效性：`_perturb_probe_graphics.py`（16/16 通过）
> 时间：2026-10-03｜环境：PySide6 6.11.1 / Qt6 / Python 3.12.10 / PyInstaller 6.19.0 / Win11

---

## 0. 一句话结论

| 坑 | 结论 | 关键证据 |
|---|---|---|
| ① windowed 打包 | **真危险，但可防**。双击启动的 exe 里 `sys.stdout`/`sys.stderr` 确实是 `None` | `stdout_none=True`（Start-Process 启动，等价资源管理器双击） |
| ② ffmpeg 子进程 | **可行**，三件套做对即可，无需随包附带 ffmpeg | 打包态下 `--version` rc=0、抽帧 rc=0、无控制台态 rc=0 |
| ③ 高 DPI | **逻辑几何完全不动**，只有物理像素放大；抽帧图必须补 DPR | 四档 `viewport_size` 恒 `[624,264]`；`grab` 物理 640→800→960→1280 |

---

## 1. 坑① windowed 打包：stdout 真的是 None

### 实测（同一份打包 exe，三种启动方式）

| 启动方式 | `stdout_none` | 说明 |
|---|---|---|
| git-bash 直接跑 exe | **False** | ⚠️ 假环境：bash 悄悄传了句柄 |
| Python `subprocess`（DETACHED_PROCESS） | **False** | ⚠️ 还是假环境 |
| PowerShell `Start-Process`（等价双击） | **True** | ✅ 真·无控制台 |

**这一条最值钱**：想测 windowed，**不要用 bash / subprocess 起 windowed exe** —— 它们会把 stdout 句柄传下去，于是你以为测过了，其实测了个寂寞。要到 `stdout_none = True` 才算真的进到了那个环境。

因此探针的输出全部走 `self_emit()`：先写内存留底 → 尝试写 stdout → 无论如何写日志文件。实测即使 stdout/stderr 都是 None，日志（`_probe_graphics.log`）依然完整记录 6 次运行的全部指标。

补充：`main.py` 现有的早期崩溃兜底（写在 import PySide6 之前）是同一思路，值得保留；**任何将来新加的模块级 print 都要按同样标准审一遍**。

---

## 2. 坑② ffmpeg 子进程：三件套，缺一不可

```python
subprocess.run(args,
    stdin=subprocess.DEVNULL,        # ① windowed 下父进程没有控制台句柄
    capture_output=True,
    encoding="utf-8", errors="replace",  # ② 不写就按 GBK 解 UTF-8 → UnicodeDecodeError
    creationflags=_NO_WINDOW,        # ③ Windows 隐藏子窗口，否则每抽一帧闪黑框
    startupinfo=si)                  #    同上（STARTF_USESHOWWINDOW + SW_HIDE）
```

写法规格与 `ui.py` / `voice.py` / `video_pipeline.py` 完全一致（`_NO_WINDOW`），以后正式画布照抄这一段即可。

实测（打包 exe，1.0 / 1.5 两档）：`ffmpeg --version` rc=0、抽帧出 627 字节 PNG rc=0、**在无控制台态下再跑一次仍 rc=0**。ffmpeg 走系统 PATH（本机是 WinGet 装的 8.1），打包不需要额外 datas。

---

## 3. 坑③ 高 DPI：逻辑不动，物理放大

四档 `QT_SCALE_FACTOR` 实跑（源码模式 + 打包模式结论一致）：

| 档位 | DPR | viewport 逻辑 | sceneRect | 节点 / 连线 / proxy（逻辑） | grab 物理像素 |
|---|---|---|---|---|---|
| 1.0 | 1.0 | `[624,264]` | `640×280` | 不变 | `640×280` |
| 1.25 | 1.25 | `[624,264]` | `640×280` | 不变 | `800×350` |
| 1.5 | 1.5 | `[624,264]` | `640×280` | 不变 | `960×420` |
| 2.0 | 2.0 | `[624,264]` | `640×280` | 不变 | `1280×560` |

**跨档位逐字段相等**：`viewport_size` / `scene_rect` / `node_rects` / `line` / `proxy_widget_size` / `proxy_scene_rect` / `transform_m11` —— 9 个字段在四档下完全一致（判据 C2 组逐条比对，不是只比一个总数）。

### 两个必须记住的陷阱

**(a) `QWidget.grab()` 返回的是物理像素，不是逻辑像素。**
它的 `width()` 已经是 逻辑×DPR（1.5 档就是 960），并且 pixmap 自带 `devicePixelRatio=1.5`。把它当逻辑尺寸用、或者再乘一次 DPR，都会得到 1.5 倍的错位。逻辑尺寸要自己除回去：`width / devicePixelRatio`。

**(b) ffmpeg 抽出来的图没有 DPR 概念，不补就更"大"。**
对照实验（同一张 320×180 抽帧图，目标逻辑宽 160）：

| 档位 | 没补 DPR（错写法） | 补了 DPR（对写法） |
|---|---|---|
| 1.0 | 160.0 | 160.0 |
| 1.25 | **200.0** | 160.0 |
| 1.5 | **240.0** | 160.0 |
| 2.0 | **320.0** | 160.0 |

不补就随缩放越走越偏：150% 屏上比预期宽 50%，200% 屏上宽一倍 —— 而且是「画面看着正常但占位不对」这个类 bug 最难被人怀疑到 DPR 头上。另外注意：Qt6 里 `QPixmap.setDevicePixelRatio()` **不会改变 `width()` 的返回值**（仍是设备像素宽），所以别指望靠比较 `width()` 来验证有没有补成功 —— 要看它进场景后的 `boundingRect()`。

---

## 4. 对后续 7 步的约束（这是本报告唯一的"上层"结论）

1. **画布坐标全程用逻辑像素**，任何缩放换算都交给 Qt；业务代码里不许出现 `* devicePixelRatio()` 的手工换算（唯一例外：给 `QPixmap` 标 DPR 时按需放大源图）。
2. **缩略图管线固定为**：ffmpeg 抽帧 → `pixmap.setDevicePixelRatio(dpr)` → 按逻辑尺寸 `scaled(160*dpr, 90*dpr)` → 标回 DPR。这条链路在 `probe_graphics.py` 里已经跑通，正式画布直接搬。
3. **画布是可以塞真 QWidget 的**（`QGraphicsProxyWidget` 实测 geometry 跨档位稳定）—— 意味着节点下拉、开关、进度条都可以直接用现成的 Qt 控件写，不必自己画。
4. **任何画布相关的新模块**：不许裸 `print`，一律走项目现有的日志/状态通道；新增仓外子进程调用一律复刻第 2 节那三件套。
5. 探针留在仓库里不是因为它还要用，而是因为它是判据的**被测物**：`tests/test_probe_graphics.py` 会读它源码做静态断言、再真跑它三档缩放。删掉它，63 条判据全线崩红。

---

## 5. 怎么复现

```bash
python probe_graphics.py                       # GUI：真开一个画布窗口看一眼
python probe_graphics.py --selftest            # 离屏自检 + 打印全部指标
python probe_graphics.py --selftest --scale 1.5 --json a.json
python tests/test_probe_graphics.py            # 63 条判据（含真跑三档）
python _perturb_probe_graphics.py              # 判据有效性：16/16

# windowed 真证据（必须用 Start-Process，bash 跑是假环境）
Start-Process .\probe_graphics.exe -ArgumentList '--selftest','--scale','1.5','--json','r.json'
```

### 判据构成

- A 组 22 条（静态）：逐条锁定三坑的防御写法，在函数体切片内判定（不在全文件搜关键词）
- B 组 27 条（行为）：三档真跑，每档 9 项
- C 组 14 条（缩放）：跨档位逐字段比对 + DPR 变化 + 物理像素单调 + 对照组有效

### 诚实标注的未知区

- 全部离屏（`QT_QPA_PLATFORM=offscreen`）完成，**没有在真实 125%/150% 显示器上用肉眼复核过**。逻辑/物理的量值关系是实测的，但「渲染观感是否在字体、描边虚化上有差异」没有被验证 —— 真要上画布前，建议在 150% 屏上跑一次 GUI 模式肉眼看一眼。
- `QGraphicsView` 未测 OpenGL 后端（`setViewport(QOpenGLWidget)`）。本项目 main.py 里 WebEngine 是禁 GPU 的，画布走软件光栅化性能如何未测 —— 节点数量上百后才需要关心。
