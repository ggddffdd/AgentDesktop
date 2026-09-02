# -*- coding: utf-8 -*-
"""真实生图端到端验证：用 tools.py 里 _gen_agnes_image 的真实函数体 + 真实配置 key，
打一次 Agnes 实时调用，确认 1920x1080 横版封面真能产出且尺寸精确。
不 import 整个 tools 模块（避免重依赖），用 AST 抽取四个相关函数。
"""
import os
import sys
import ast
import json
import re
import types
import urllib.request
import urllib.error
import shutil
from datetime import datetime
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "tools.py")
CFG = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI", "config.json")

src = open(TOOLS, encoding="utf-8").read()
tree = ast.parse(src)
need = ("_agnes_creds", "_gen_agnes_image", "_save_gen_image", "_letterbox_to_size", "_size_to_tier_ratio")
ns = {
    "os": os, "json": json, "shutil": shutil,
    "datetime": datetime, "time": time, "urllib": urllib, "re": re,
}
# 最小 search_mod 替身：提供 download_bytes（Agnes 返回 URL，需下载成文件）
search_mod = types.SimpleNamespace()


def _download_bytes(url, timeout=80):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


search_mod.download_bytes = _download_bytes
ns["search_mod"] = search_mod

# 需要一并带过来的模块级常量（档位表/比例表/防加字后缀/要字关键词），
# 否则被抽取的函数体里引用它们会 NameError（这是本 harness 的限制，非产品 bug）。
need_consts = ("_AGNES_TIERS", "_AGNES_RATIOS", "_WANT_TEXT_RE", "_ANTI_TEXT_SUFFIX")

for node in tree.body:
    take = False
    if isinstance(node, ast.FunctionDef) and node.name in need:
        take = True
    elif isinstance(node, ast.Assign):
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        take = any(n in need_consts for n in names)
    if take:
        code = compile(ast.Module(body=[node], type_ignores=[]), "tools.py", "exec")
        exec(code, ns)

missing = [c for c in need_consts if c not in ns]
assert not missing, f"模块级常量未抽到: {missing}"

# 产物目录（用临时目录，避免污染真实产物库）
TMP = os.path.join(HERE, "_real_img_test")
PRODUCTS_DIR = os.path.join(TMP, "产物")
ns["PRODUCTS_DIR"] = PRODUCTS_DIR
os.makedirs(os.path.join(PRODUCTS_DIR, "图片"), exist_ok=True)

cfg = json.load(open(CFG, encoding="utf-8"))

prompt = "夜景下的城市天际线封面，高楼灯火通明，前景是江河倒影，广角横版宽幅构图，画面有纵深感，现代商务感，适合做文章封面"
size = "1920x1080"
print("TIER_RATIO_1920x1080 =", ns["_size_to_tier_ratio"](1920, 1080))
print("TIER_RATIO_1024x768 =", ns["_size_to_tier_ratio"](1024, 768))

# 抓取真正发给 Agnes 的 payload（证明 anti-text 后缀 + 档位转换进了请求），同时放行真实请求出图。
_orig_urlopen = urllib.request.urlopen
def _capture_urlopen(req, *a, **k):
    try:
        body = req.data.decode("utf-8")
        pd = json.loads(body)
        print("PAYLOAD_MODEL:", pd.get("model"))
        print("PAYLOAD_SIZE:", pd.get("size"), "RATIO:", pd.get("ratio"))
        p = pd.get("prompt", "")
        print("PAYLOAD_PROMPT_TAIL:", p[-60:] if len(p) > 60 else p)
        print("PAYLOAD_HAS_ANTITEXT:", "强制约束" in p)
    except Exception as e:
        print("CAPTURE_WARN:", repr(e))
    return _orig_urlopen(req, *a, **k)
urllib.request.urlopen = _capture_urlopen

print(">>> 调用 _gen_agnes_image(真实 Agnes 通道) size=", size)
try:
    res = ns["_gen_agnes_image"](cfg, TMP, prompt, size=size, progress=None)
except Exception as e:
    print("EXCEPTION:", type(e).__name__, e)
    sys.exit(3)

print("<<< 返回:", res)
if not isinstance(res, tuple):
    print("RESULT_NOT_IMAGE:", res)
    sys.exit(4)

rel = res[0]
fpath = os.path.join(TMP, rel)
from PIL import Image
im = Image.open(fpath).convert("RGB")
print("SAVED_FILE:", fpath)
print("REAL_SIZE:", im.size)
ok = im.size == (1920, 1080)
print("\nREAL_GEN_1920x1080_OK =", ok)
sys.exit(0 if ok else 5)
