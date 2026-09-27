# v4.175.0：从仓库根目录搬入 tests/ —— 统一入口以 `python tests/xxx.py` 运行，
# 此时 sys.path[0] 是 tests/，必须显式把仓库根加回来，否则 import ui 会失败。
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

"""v4.102 fix4：图片压缩函数测试。
用真实大尺寸图（1920x1080 噪点，模拟真实截图）测 _compress_image_for_api，
确认能缩放到 1568px 内并转 JPEG，体积显著减小（避免 DeepSeek 400）。"""
import os, sys, struct, zlib, random
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)

import ui
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor
from PySide6.QtCore import Qt, QBuffer, QIODevice
import base64

# 生成一张真实大小的"截图"：1920x1080 模拟界面（有文字/色块/不可压）
def make_screenshot_like(w=1920, h=1080, path="/tmp/_test_screen.png"):
    img = QImage(w, h, QImage.Format_RGB32)
    # 模拟真实截图：大量不可压缩噪点（文字/图标/渐变）
    import random
    for y in range(h):
        for x in range(0, w, 1):
            if random.random() < 0.3:  # 30% 噪点
                img.setPixel(x, y, random.randint(0, 0xFFFFFF))
            else:
                img.setPixel(x, y, 0xF0F0F0)
    # 再加 UI 元素
    p = QPainter(img)
    for i in range(300):
        c = QColor(random.randint(0,255), random.randint(0,255), random.randint(0,255))
        p.setBrush(c)
        x = random.randint(0, w-100); y = random.randint(0, h-50)
        p.drawRect(x, y, random.randint(30, 200), random.randint(20, 80))
    p.end()
    img.save(path, "PNG")
    return path

png_path = make_screenshot_like(path=os.path.join(ui.APP_DIR, "_test_screen.png"))
orig_size = os.path.getsize(png_path)
print(f"[orig] {orig_size/1024:.0f} KB")

# 测压缩
result = ui._compress_image_for_api(png_path)
assert result, "压缩应成功"
url, mime, orig_kb, final_kb, compressed = result
print(f"[after] mime={mime} | {orig_kb}KB -> {final_kb}KB | compressed={compressed} | ratio={final_kb/orig_kb*100:.0f}%")

# 关键断言：体积应显著小于原图（特别是原图 >200KB 时）
assert final_kb < orig_kb, f"压缩后应更小，但 {final_kb} >= {orig_kb}"
# 1568px 内：从 URL 的 base64 解码看尺寸
import base64 as b64
prefix, data = url.split(",", 1)
raw = b64.b64decode(data)
# 从 JPEG 头读尺寸：SOF marker
from io import BytesIO
def jpeg_size(raw):
    i = 2
    while i < len(raw):
        if raw[i] != 0xFF: return None
        marker = raw[i+1]
        if marker in (0xC0, 0xC1, 0xC2):
            h = struct.unpack(">H", raw[i+5:i+7])[0]
            w = struct.unpack(">H", raw[i+7:i+9])[0]
            return w, h
        size = struct.unpack(">H", raw[i+2:i+4])[0]
        i += 2 + size
    return None
if mime == "image/jpeg":
    sz = jpeg_size(raw)
    print(f"[dim] JPEG {sz}")
    assert sz and max(sz) <= 1568, f"缩放后最长边应 <=1568, got {sz}"
else:
    print(f"[dim] PNG (未转 JPEG，原图可能已 <200KB)")

# 多张图模拟用户场景：2 张大图压缩后总和应 < 8MB
results = [ui._compress_image_for_api(make_screenshot_like(path=os.path.join(ui.APP_DIR, f"_test_screen_{i}.png"))) for i in range(2)]
total_final = sum(r[3] for r in results)
print(f"\n[2 imgs] total final = {total_final/1024:.0f}KB (DeepSeek 限制 ~10MB)")
assert total_final < 8*1024, f"2张压缩后应<8MB, got {total_final/1024:.0f}KB"

# 清理
for p in [os.path.join(ui.APP_DIR, "_test_screen.png"), os.path.join(ui.APP_DIR, "_test_screen_0.png"), os.path.join(ui.APP_DIR, "_test_screen_1.png")]:
    if os.path.exists(p): os.remove(p)

print("\n=== IMAGE_COMPRESS_OK ===")
