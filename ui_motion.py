# -*- coding: utf-8 -*-
"""ui_motion.py —— 系统「减弱动效」偏好的读取层（v4.210.2）

## 为什么需要这么一个文件

`UI_QA_CHECKLIST.md §4` 给的配方是：

    animate = QGuiApplication.styleHints().animate()

**这条 API 在 PySide6 6.11.1 里不存在** —— `QStyleHints` 的 28 个属性里没有
`animate`、也没有 `animationDuration`（实测 `AttributeError`；`QStyleHints
.accessibility()` 返回的 `QAccessibilityHints` 目前只暴露 `contrastPreference`）。
照抄那条配方会当场崩，而不是「读不到就当允许」。Qt 6 至今没有把 reduced-motion
开关暴露成 API，所以只能读平台真值。

## 平台真值

    Windows   SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, ...)
              即「设置 → 辅助功能 → 视觉效果 → 动画效果」这一个开关
    其他平台  无统一接口 → 返回 True（保持现状，绝不无端把动效关掉）

## 失败一律 fail-open

读不到偏好 ≠ 用户要关动效。任何异常（非 Windows、ctypes 被拦、系统调用失败）
都返回 True，行为与加这个模块之前**完全一致** —— 这样它永远不可能成为
「某个平台上线后动效忽然全没了」的原因。

## 明确不做

**不改时长类 timeout**。toast 的自动消失时间（success 2500ms / error 常显）
是「信息可读性」不是「动效」，跟着动画一起归零会把提示变成闪现。
本模块只服务 `>150ms 的过渡动画` 这一类（UI_QA §4）。
"""
import sys

# SystemParametersInfo 的 action 码：客户端区域动画是否开启（BOOL 出参）
SPI_GETCLIENTAREAANIMATION = 0x1042

# 手工覆盖：None = 跟随系统；True/False = 强制。
# 提供它是为了两件事：① 让判据能确定性地测两条分支，不必去改系统设置；
# ② 将来若要加「本应用内自带开关」，落点已经在这儿了。
_override = None


def set_override(flag):
    """强制开/关动效（None 恢复跟随系统）。返回本次之前的取值。"""
    global _override
    old = _override
    _override = None if flag is None else bool(flag)
    return old


def motion_allowed():
    """当前是否允许播动画。True=允许（默认），False=用户要求减弱动效。"""
    if _override is not None:
        return _override
    if not sys.platform.startswith("win"):
        return True
    try:
        import ctypes
        v = ctypes.c_int(0)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(v), 0)
        if not ok:
            return True          # 调用失败 → fail-open
        return bool(v.value)
    except Exception:            # noqa: BLE001 —— 读不到偏好不是错误
        return True


def scale_ms(ms):
    """动画时长换算：允许动效 → 原值；要求减弱 → 0（瞬间到位，不渐变）。

    调用方约定：拿到 0 时**必须**直接把终值一次性写上，而不是启动一个 0ms 计时器
    （后者只是把渐变压缩到一帧，仍会走一遍逐帧路径）。
    """
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return 0
    if ms <= 0:
        return 0
    return ms if motion_allowed() else 0
