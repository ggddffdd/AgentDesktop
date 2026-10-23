# -*- coding: utf-8 -*-
"""v4.289.0 回归：浏览器搜索引擎优先级清单抽配置。

验证：
- config.DEFAULT_CONFIG / load_config 提供 browser_search_engines 默认清单。
- browser_search 在 engines=None 时从 cfg 读取清单（顺序生效）。
- 清单里出现 _SEARCH_PAGE 未登记的引擎被安全跳过（不崩）。
- 显式传入 engines 覆盖 cfg。
- cfg 缺失/异常时回退写死默认值。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import browser_control_tools as bct


def _capture_urls(cfg, query="测试查询"):
    """mock 掉 CDP 与 runner，返回 browser_search 实际尝试的搜索引擎 URL 顺序。"""
    urls = []

    def _fake_runner(*a, **k):
        # _run_runner("read", url, cfg=..., app_dir=..., links=True, ...)
        urls.append(a[1])
        return {"ok": False, "error": "mock-runner"}

    with mock.patch.object(bct, "_ensure_cdp", return_value=(True, "")), \
         mock.patch.object(bct, "_run_runner", side_effect=_fake_runner):
        bct.browser_search(cfg, query, engines=None)
    return urls


class BrowserEngineConfigTest(unittest.TestCase):
    def test_default_in_default_config(self):
        self.assertEqual(
            config.DEFAULT_CONFIG.get("browser_search_engines"),
            ["duckduckgo", "google", "bing"])

    def test_default_via_load_config(self):
        cfg = config.load_config()
        self.assertEqual(cfg.get("browser_search_engines"),
                         ["duckduckgo", "google", "bing"])

    def test_custom_order_from_cfg(self):
        urls = _capture_urls({"browser_search_engines": ["bing", "duckduckgo"]})
        self.assertEqual(len(urls), 2)
        self.assertIn("bing.com", urls[0])
        self.assertIn("duckduckgo.com", urls[1])

    def test_unknown_engine_skipped(self):
        urls = _capture_urls({"browser_search_engines": ["bogus_engine", "duckduckgo"]})
        # bogus 未登记 → 跳过，不崩；只剩 duckduckgo 被尝试
        self.assertEqual(len(urls), 1)
        self.assertIn("duckduckgo.com", urls[0])

    def test_explicit_engines_overrides_cfg(self):
        # 显式传 engines=["google"] 应覆盖 cfg 里的 ["bing"]（browser_search 仅在
        # engines is None 时才读 cfg），且不尝试 bing。
        out_urls = []
        with mock.patch.object(bct, "_ensure_cdp", return_value=(True, "")), \
             mock.patch.object(bct, "_run_runner",
                               side_effect=lambda *a, **k: (out_urls.append(a[1]) or {"ok": False, "error": "m"})):
            bct.browser_search({"browser_search_engines": ["bing"]}, "x",
                               engines=["google"])
        self.assertEqual(len(out_urls), 1)
        self.assertIn("google.com", out_urls[0])

    def test_fallback_when_cfg_missing(self):
        urls = _capture_urls({})
        self.assertEqual(len(urls), 3)
        self.assertIn("duckduckgo.com", urls[0])
        self.assertIn("google.com", urls[1])
        self.assertIn("bing.com", urls[2])

    def test_fallback_when_cfg_bad_type(self):
        urls = _capture_urls({"browser_search_engines": ""})  # 非 list
        self.assertEqual(len(urls), 3)
        self.assertIn("duckduckgo.com", urls[0])


def run_tests():
    suite = unittest.TestLoader().loadTestsFromTestCase(BrowserEngineConfigTest)
    res = unittest.TextTestRunner(verbosity=1).run(suite)
    n_pass = res.testsRun - len(res.failures) - len(res.errors)
    n_fail = len(res.failures) + len(res.errors)
    print(f"PASS={n_pass} FAIL={n_fail}")
    return n_fail


if __name__ == "__main__":
    sys.exit(1 if run_tests() else 0)
