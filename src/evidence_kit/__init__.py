"""evidence-kit：把「证据目录 + 一包格式各异的材料」排成一份可提交的证据册 PDF。

不变量（改代码前先看这几条）：
1. 原件字节不动：JPEG 直通 DCTDecode、PNG 走 FlateDecode，不重压缩、不裁切、不旋转；
2. 页数只从分页计划出：目录里的 `1-5` 必须等于渲染后实测的页区间（`layout.verify`）；
3. 页脚 18mm 留白，页码只画在那里，永不压内容；
4. 版面恒为 A4 竖版；
5. 材料解析不了 → 报错并列进报告，绝不静默丢弃。
"""

__version__ = "0.1.0"

from .model import CaseInfo, Catalog, Group, Row, Unit  # noqa: F401
