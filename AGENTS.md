# evidence-kit · 给接手 Agent 的说明

## 这个项目是什么

输入 = 一份证据目录（序号 / 证据材料名称 / 证明目的 / 页数）+ 一包格式各异的证据材料；
输出 = 一份 PDF：目录表在前，**全部证据原件按序连编页码**在后，目录里的页数就是实测区间。
独立项目，不绑死劳动仲裁；主要跑在 macOS，稳定后再做 mac app 与跨平台。

## 硬规矩

1. **原件字节不动**：JPEG 走 DCTDecode 直通、PNG 走 FlateDecode。禁止 PIL 重存图片进 PDF，
   禁止裁切/锐化/旋转。切片用「重复引用 + 裁剪窗」（`render._draw_image_unit`），不许先裁剪再存盘。
2. **页数只能来自 `layout.plan()`**：任何「先填个页数再排版」的做法都是伪造证据形式要件。
   `layout.verify()` 必须保持可被证伪——改坏分页就应抛错。
3. **版面恒 A4 竖版**；页码只画在页脚 18mm 留白内。
4. **材料解析不了 → 报错并进报告**（`pipeline.errors`）。静默跳过 = 证据悄悄丢了，是致命缺陷。
5. **不替用户做实体判断**：证明目的、当事人信息、页数以外的目录内容一律不编造；
   自动补写的必须标 `proves_source: auto` 并进 `needs_review`。
6. 不联网、不调用 LLM。能脚本化的一律脚本化（用户隐私与 token 成本两条红线）。

## 代码地图

| 文件 | 职责 |
| --- | --- |
| `model.py` | `Unit`（一页材料）/ `Row`（序号行）/ `Group`（共享证明目的与页数的一组行）/ `Catalog` |
| `catalog.py` | 目录读写：yaml/json、`auto_catalog`（文件夹即目录）、`dump_yaml`（出草稿给人改） |
| `ocr.py` | macOS Vision 本地 OCR：逐行文字 + 坐标 + 置信度（`with_chars` 给字符级坐标） |
| `table.py` | OCR 行 → 目录表格：列边界、序号锚点、合并单元格、折行还原 |
| `docx_catalog.py` | Word 版目录（含 vMerge 合并单元格 → 证据组） |
| `sorting.py` | 组内按时间排序：文件名 → 图内文字 → 不猜；核对表按排完后的顺序输出 |
| `match.py` | 平铺文件 → 证据行：序号前缀、共有词（2/3 字滑窗）、专属词、OCR 加分；歧义/零分交人工 |
| `sources.py` | 材料发现与探测：图片尺寸、PDF 页数、HEIC 转码、超长截图切片、有效 DPI |
| `layout.py` | 版面常量 + `plan()`（组不跨页、切片独占页）+ `verify()` |
| `render.py` | reportlab 画目录页与证据页；pypdf 合并 PDF 原件并叠页脚 |
| `pipeline.py` | 串起来 + 出 `report.json` / 核对报告 |
| `cli.py` | `build` / `dump` 两个子命令 |

## 测试

```bash
.venv/bin/python -m pytest -q      # 17 项，<2s
```

测试名就是踩过的坑：`test_slice_always_owns_a_page`、`test_groups_never_share_a_page`、
`test_low_resolution_is_blocked`、`test_build_pages_and_passthrough`（JPEG 流逐字节相等）、
`test_unsupported_file_is_reported_not_dropped`。改抽取/排版代码前先读测试名。

## 已知坑

- reportlab 默认用 ASCII85 包一层二进制流（体积 +25%，流不再是原字节），必须保持
  `rl_config.useA85 = 0`。
- 画图时画布已平移到单元格原点，`_draw_image_unit` 里不能再加页边距（加过一次，右侧 12mm 被裁掉）。
- 组末页不满时剩下的单元要放回整宽（`_content_page` 里的 `n`），否则单页被迫缩成半格。
- `--compact` 与切片冲突：切片本身填满一页，永远独占。
- 排序只能在**行内**做，不能跨行（同一组的两行是两项不同证据；实测把停车的截图混进聊天记录）。
- 报告里的排序核对表要按**排完之后**的顺序输出，否则用户看到的还是旧顺序。
- reportlab 不做中文避头尾：`wrap_cjk` 在标点会起行时把**前一个字一起带下去**，
  千万别用「让标点溢出」的办法——Paragraph 会二次断行，出现标点独占一行。
- OCR 会把**相邻两格的文字读成一条**（实测 `中国石化加油IC卡台账 作管理和安排。`）：必须按列边界
  用字符级坐标切开，否则名称里混进证明目的。
- 列边界不能用表头词的中点（表头在列里居中，宽列会偏进列内 100px）；用**内容左边缘众数**，
  且只能用表头以下的行参与计算（表头之上是居中标题，会把边界拉飞）。
- OCR 偶尔漏掉序号数字（缩到 60% 时「1」消失）→ 用名称列行块做备用锚点，否则两行证据并成一行。
- 扫描折行不是换行：上一行没有句读、下一行不是「1:」编号，就必须接回去。
- 平铺归位宁可「认不出」也不能瞎猜：一个文件归错证据就是张冠李戴。专属词（只在一行名称里出现的词）
  才是强信号，共有词只在没有更强信号时加权。名字里同时出现两个不同证据的专属词 → 判为歧义。
- PDF 原件用 `Transformation().translate(...).scale(s)`（先缩后平移）；顺序反了会跑出纸外，
  且要减掉 mediabox 左下原点。
