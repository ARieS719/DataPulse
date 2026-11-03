"""生成 GitHub 社交预览图（1280x640，需在 GitHub 仓库 Settings → Social preview 手动上传）。"""

from __future__ import annotations

import sys

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640
BG = (13, 17, 23)
WHITE = (240, 246, 252)
GRAY = (139, 148, 158)
BLUE = (88, 166, 255)
GREEN = (63, 185, 80)
BORDER = (48, 54, 61)
PANEL = (22, 27, 34)


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


YAHEI = "C:/Windows/Fonts/msyh.ttc"
BOLD = "C:/Windows/Fonts/msyhbd.ttc"


def main(out: str = "docs/social-preview.png") -> int:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_title = font(BOLD, 104)
    f_sub = font(YAHEI, 38)
    f_stack = font(YAHEI, 26)
    f_metric_num = font(BOLD, 44)
    f_metric_lbl = font(YAHEI, 22)
    f_foot = font(YAHEI, 20)

    d.text((80, 96), "DataPulse", font=f_title, fill=WHITE)
    d.text((84, 226), "写入前质量熔断 · 湖仓数仓平台", font=f_sub, fill=GRAY)

    # 状态条
    d.rectangle((80, 300, 520, 310), fill=BLUE)

    d.text(
        (84, 336),
        "PySpark · Apache Iceberg (WAP) · Airflow · SQLGlot · FastAPI · Streamlit",
        font=f_stack,
        fill=BLUE,
    )

    # 三个指标卡
    cards = [
        ("1.0", "混沌召回（8/8）"),
        ("0.0", "误报率（阴性对照）"),
        ("100 万", "行全链路实测"),
    ]
    x = 84
    for num, lbl in cards:
        d.rounded_rectangle((x, 400, x + 330, 524), radius=14, fill=PANEL, outline=BORDER, width=2)
        d.text((x + 24, 416), num, font=f_metric_num, fill=GREEN)
        d.text((x + 24, 480), lbl, font=f_metric_lbl, fill=GRAY)
        x += 360

    d.text(
        (84, 560),
        "ODS → DWD → DWS → ADS  ·  审计分支零暴露  ·  fast_forward 原子发布",
        font=f_foot,
        fill=GRAY,
    )
    d.text((84, 590), "github.com/ARieS719/DataPulse", font=f_foot, fill=BLUE)

    img.save(out, "PNG", optimize=True)
    print(f"saved: {out} ({img.size[0]}x{img.size[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "docs/social-preview.png"))
