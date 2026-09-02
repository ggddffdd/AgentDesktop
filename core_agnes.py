# -*- coding: utf-8 -*-
"""跨目录桥接：统一视频内核（video-agent/core 为权威来源）。

让 AgentDesktop 复用 video-agent/core/agnes.py 的 AgnesClient，
根除两份 Agnes 视频客户端逻辑（提交/轮询/下载/限流/三模式/口播注入）。

定位策略：
  - 工作区内：本文件所在目录的 ../video-agent （与 AgentDesktop 同工作区）
  - 回退：用户级共享内核 ~/.workbuddy/video_core （若日后把 core 迁到稳定位置）
视频生成相关图片归一化、dialogue 注入、双端点轮询都在 core 内完成，
本文件只负责把 core 包放进 sys.path 并 re-export AgnesClient / AgnesError。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_core_parent():
    """定位包含 `core` 包的父目录（core 包位置随运行形态变化）。

    候选顺序：
      1. 开发态：本文件所在目录的 ../video-agent （与 AgentDesktop 同工作区）
      2. 冻结态：exe 同目录下的 core 包（PyInstaller 把 video-agent/core 打成 core 随附）
      3. 回退：用户级共享内核 ~/.workbuddy/video_core （若日后把 core 迁到稳定位置）
    """
    candidates = [
        os.path.normpath(os.path.join(_HERE, "..", "video-agent")),
        _HERE,
        os.path.join(os.path.expanduser("~"), ".workbuddy", "video_core"),
    ]
    for c in candidates:
        if os.path.isdir(os.path.join(c, "core")):
            return c
    return candidates[0]


_VA = _find_core_parent()

if os.path.isdir(os.path.join(_VA, "core")) and _VA not in sys.path:
    # 追加（非插入最前）：AgentDesktop 自身的模块（config 等）仍优先解析，
    # 避免 video-agent 顶层同名模块被误导入。
    sys.path.append(_VA)

from core.agnes import AgnesClient, AgnesError  # noqa: E402,F401

__all__ = ["AgnesClient", "AgnesError"]
