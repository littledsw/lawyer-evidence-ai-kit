"""造一份合成演示材料（不含任何真实当事人信息），用于跑通链路与回归。

跑：python examples/make_demo.py      → examples/demo/{materials,catalog.yaml}
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
DEMO = ROOT / "demo"
MAT = DEMO / "materials"
SONGTI = "/System/Library/Fonts/Supplemental/Songti.ttc"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(SONGTI, size, index=1 if bold else 4)


def chat_shot(path: Path, day: str, lines: list[tuple[str, str]], w: int = 1170, h: int = 2532) -> None:
    """仿微信聊天截图（竖屏整屏，JPEG）。lines: [(说话人, 文本)]"""
    im = Image.new("RGB", (w, h), (237, 237, 237))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w, 180], fill=(247, 247, 247))
    d.text((w // 2 - 60, 60), "王经理", font=font(46, True), fill=(20, 20, 20))
    d.text((w // 2 - 70, 118), day, font=font(34), fill=(120, 120, 120))
    y = 220
    for who, text in lines:
        me = who == "我"
        f = font(40)
        tw = d.textlength(text, font=f)
        bw = int(tw) + 60
        x0 = w - 60 - bw if me else 60
        color = (149, 236, 105) if me else (255, 255, 255)
        d.rounded_rectangle([x0, y, x0 + bw, y + 96], radius=14, fill=color)
        d.text((x0 + 30, y + 24), text, font=f, fill=(15, 15, 15))
        if not me:
            d.text((60, y - 36), who, font=font(30), fill=(110, 110, 110))
        y += 150
    im.save(path, "JPEG", quality=95, subsampling=0)


def statement_shot(path: Path, rows: list[tuple[str, str, str]], w: int = 1654, h: int = 2339) -> None:
    """仿银行流水（A4 300dpi 竖版，JPEG）"""
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    d.text((w // 2 - 200, 90), "个人账户交易明细", font=font(52, True), fill=(0, 0, 0))
    d.text((80, 200), "户名：崔某某    账号：6222 **** **** 1234", font=font(34), fill=(0, 0, 0))
    y = 300
    d.line([70, y, w - 70, y], fill=(0, 0, 0), width=3)
    for head, x in (("交易日期", 90), ("摘要", 380), ("金额(元)", 900), ("余额(元)", 1250)):
        d.text((x, y + 20), head, font=font(34, True), fill=(0, 0, 0))
    y += 90
    d.line([70, y, w - 70, y], fill=(0, 0, 0), width=3)
    for date, memo, amt in rows:
        y += 20
        d.text((90, y), date, font=font(32), fill=(0, 0, 0))
        d.text((380, y), memo, font=font(32), fill=(0, 0, 0))
        d.text((900, y), amt, font=font(32), fill=(0, 0, 0))
        d.text((1250, y), "……", font=font(32), fill=(0, 0, 0))
        y += 70
    im.save(path, "JPEG", quality=95, subsampling=0)


def long_shot(path: Path, w: int = 1170, h: int = 9200) -> None:
    """超长滚动截图（用于验证切片不重编码）"""
    im = Image.new("RGB", (w, h), (237, 237, 237))
    d = ImageDraw.Draw(im)
    for i in range(h // 240):
        y = 40 + i * 240
        d.rounded_rectangle([60, y, w - 60, y + 150], radius=14, fill="white")
        d.text((90, y + 40), f"第 {i + 1} 条台账记录  加油 300 元", font=font(38), fill=(15, 15, 15))
    im.save(path, "JPEG", quality=95, subsampling=0)


def social_pdf(path: Path, months: int = 3) -> None:
    """仿社保缴费记录（PDF，每页一个月）"""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(TTFont("D", SONGTI, subfontIndex=4))
    c = canvas.Canvas(str(path), pagesize=A4)
    for i in range(months):
        c.setFont("D", 16)
        c.drawString(60, 780, "社会保险个人缴费记录")
        c.setFont("D", 11)
        for j in range(8):
            c.drawString(60, 730 - j * 26, f"2025-0{i + 1}  养老保险  单位缴纳 16%  个人缴纳 8%  第 {j + 1} 行")
        c.showPage()
    c.save()


def main() -> None:
    random.seed(7)
    for sub in ("01_银行工资流水", "02_社保缴费记录", "04_加油台账", "05_停车缴费"):
        (MAT / sub).mkdir(parents=True, exist_ok=True)

    statement_shot(MAT / "01_银行工资流水" / "流水_p1.jpg",
                   [("2025-01-10", "工资", "4,500.00"), ("2025-02-10", "工资", "4,500.00"),
                    ("2025-03-10", "工资", "4,500.00"), ("2025-04-10", "工资", "4,500.00")])
    statement_shot(MAT / "01_银行工资流水" / "流水_p2.jpg",
                   [("2025-05-10", "工资", "4,500.00"), ("2025-06-10", "工资", "4,500.00")])
    social_pdf(MAT / "02_社保缴费记录" / "社保缴费记录.pdf", months=3)

    # 故意乱序命名，验证「按文件名自然序」与后续按时间排序
    shots = [
        ("2025年9月2日 21:14", [("王经理", "明早六点半到车队"), ("我", "好")]),
        ("2025年8月28日 19:40", [("王经理", "周六加一趟，算加班"), ("我", "收到")]),
        ("2025年9月9日 06:12", [("王经理", "今天跑三趟")]),
        ("2025年8月30日 07:05", [("我", "到车队了"), ("王经理", "直接去装油")]),
    ]
    order = [3, 1, 4, 2]
    for idx, k in enumerate(order, start=1):
        day, lines = shots[k - 1]
        chat_shot(MAT / "04_加油台账" / f"微信_{idx}.jpg", day, lines)

    long_shot(MAT / "04_加油台账" / "台账长截图.jpg")
    statement_shot(MAT / "05_停车缴费" / "停车缴费_1.jpg",
                   [("2025-09-01", "停车费", "15.00"), ("2025-09-03", "停车费", "15.00")])

    catalog = DEMO / "catalog.yaml"
    catalog.write_text('''# 演示目录：结构对应「证据目录」截图的四列（序号 / 名称 / 证明目的 / 页数）
case:
  title: 证据目录
  submitter: 崔某某
  agent: 王某某
  date: ""
groups:
  - proves: |
      1:证明申请人月工资标准为 4500 元；
      2:证明工资由劳务公司实际发放；
    rows:
      - no: 1
        name: 银行工资流水
        files: 01_银行工资流水
  - proves: |
      1:证明 2012 年 9 月 15 日至 2025 年 9 月 30 日期间，各劳务公司轮流为申请人缴纳社会保险；
      2:证明劳动关系主体多次变更，工作存续期间，非因申请人个人原因变更用工主体；
    rows:
      - no: 2
        name: 社保缴费记录
        files: 02_社保缴费记录
  - proves: 证明申请人在某公司担任司机职位并实际接受其工作管理和安排。
    rows:
      - no: 4
        name: 中国石化加油IC卡台账对账单
        files: 04_加油台账
      - no: 5
        name: 停车缴费截图
        files: 05_停车缴费
''', encoding="utf-8")
    print(f"材料：{MAT}\n目录：{catalog}")


if __name__ == "__main__":
    main()
