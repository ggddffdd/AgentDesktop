# -*- coding: utf-8 -*-
"""v4.288.5：执行端瞬断自动重试（护栏加固）单元测试。

不依赖 exec_tool 全链路（那需要真实注册工具 + cfg），直接验证两块核心逻辑：
  1. `_is_transient_error` 异常分类（瞬断 True / 确定性 False / HTTPError 429+5xx True、4xx False）
  2. `_exec_handler_with_retry` 有界重试（瞬断成功 / 重试用尽 / 确定性不重试 / 首次成功）
time.sleep 全部 mock 成瞬时，避免真实等待。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools
import urllib.error


def _patch_sleep():
    _orig = time.sleep
    time.sleep = lambda *_a, **_k: None
    return _orig


def test_is_transient_classification():
    import socket
    assert tools._is_transient_error(TimeoutError()) is True
    assert tools._is_transient_error(ConnectionError()) is True
    assert tools._is_transient_error(ConnectionResetError()) is True
    assert tools._is_transient_error(BrokenPipeError()) is True
    assert tools._is_transient_error(socket.timeout()) is True
    assert tools._is_transient_error(urllib.error.URLError("boom")) is True
    # HTTPError：429 / 5xx 瞬断；404 / 401 / 403 确定性
    assert tools._is_transient_error(
        urllib.error.HTTPError(None, 404, "nf", None, None)) is False
    assert tools._is_transient_error(
        urllib.error.HTTPError(None, 401, "unauth", None, None)) is False
    assert tools._is_transient_error(
        urllib.error.HTTPError(None, 429, "rl", None, None)) is True
    assert tools._is_transient_error(
        urllib.error.HTTPError(None, 503, "s", None, None)) is True
    # 确定性错误一律不重试
    assert tools._is_transient_error(ValueError()) is False
    assert tools._is_transient_error(KeyError("x")) is False
    assert tools._is_transient_error(FileNotFoundError()) is False
    assert tools._is_transient_error(RuntimeError()) is False
    assert tools._is_transient_error(Exception()) is False
    # 第三方 HTTP 库按类名兜底
    class RequestException(Exception):
        pass
    assert tools._is_transient_error(RequestException()) is True


def test_retry_succeeds_after_transient():
    _orig = _patch_sleep()
    try:
        _calls = {"n": 0}

        def fn():
            _calls["n"] += 1
            if _calls["n"] < 3:
                raise ConnectionError("blip")
            return "ok"

        r, e, attempts = tools._exec_handler_with_retry(
            fn, "t", {}, max_attempts=3)
        assert r == "ok", r
        assert e is None, e
        assert attempts == 3, attempts
        assert _calls["n"] == 3
    finally:
        time.sleep = _orig


def test_retry_exhausts_transient():
    _orig = _patch_sleep()
    try:

        def fn():
            raise TimeoutError("always")

        r, e, attempts = tools._exec_handler_with_retry(
            fn, "t", {}, max_attempts=3)
        assert r is None, r
        assert isinstance(e, TimeoutError), e
        assert attempts == 3, attempts
    finally:
        time.sleep = _orig


def test_no_retry_on_deterministic():
    _orig = _patch_sleep()
    try:

        def fn():
            raise ValueError("bad param")

        r, e, attempts = tools._exec_handler_with_retry(
            fn, "t", {}, max_attempts=3)
        assert r is None, r
        assert isinstance(e, ValueError), e
        assert attempts == 1, attempts  # 确定性错误：不重试
    finally:
        time.sleep = _orig


def test_first_success_no_retry():
    _orig = _patch_sleep()
    try:
        r, e, attempts = tools._exec_handler_with_retry(
            lambda: "ok", "t", {}, max_attempts=3)
        assert r == "ok", r
        assert e is None, e
        assert attempts == 1, attempts
    finally:
        time.sleep = _orig


def test_stop_signal_aborts():
    _orig = _patch_sleep()
    try:
        import threading

        class _Stop:
            def is_set(self):
                return True

        def fn():
            raise ConnectionError("should not retry")

        r, e, attempts = tools._exec_handler_with_retry(
            fn, "t", {}, stop_event=_Stop(), max_attempts=3)
        assert r is None
        assert attempts == 1  # 立即停止，未重试
    finally:
        time.sleep = _orig


if __name__ == "__main__":
    test_is_transient_classification()
    test_retry_succeeds_after_transient()
    test_retry_exhausts_transient()
    test_no_retry_on_deterministic()
    test_first_success_no_retry()
    test_stop_signal_aborts()
    print("PASS=6 FAIL=0")
