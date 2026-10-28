"""材料类型词表（领域词典）：把「文件名」与「证据名称」都归一到同一套**类型概念**。

为什么需要它：只按字面重合匹配会张冠李戴。真实案例里 `查询账户明细（客户台账）.pdf` 是
**银行工资流水**，但「台账」二字与目录第 4 项「中国石化加油IC卡台账对账单」字面重合，
字面匹配会把它塞进加油台账。类型概念 + 类型冲突否决能挡住这类错误。

规则：只用**有判别力**的词（`加油` 而不单用 `台账`；`查询账户明细`/`客户台账` 归银行流水）。
用户可在 `lexicon.yaml` 覆盖（见 `load_overrides`）。
"""
from __future__ import annotations

from pathlib import Path

# concept -> (中文名, 关键词元组)。关键词越长越具体，匹配用「包含」。
CONCEPTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "bank_statement": ("银行流水/账户明细", (
        "查询账户明细", "账户明细", "交易明细", "银行流水", "工资流水", "代发工资",
        "客户查询", "客户台账", "银行", "流水")),
    "social_security": ("社保缴费记录", (
        "社会保险", "社保", "权益记录", "参保", "缴费记录", "养老保险", "医保")),
    "tax": ("个税/纳税记录", (
        "个人所得税", "个税", "纳税", "完税", "税务", "申报表")),
    "oil_ledger": ("加油卡台账", (
        "加油", "IC卡", "ic卡", "中国石化", "中石化", "预付卡", "油卡")),
    "chat": ("微信/聊天记录", ("微信", "聊天", "对话", "群聊", "短消息")),
    "attendance": ("考勤/打卡记录", ("考勤", "打卡", "排班", "出勤")),
    "parking": ("停车缴费", ("停车", "车场", "泊车")),
    "payslip": ("工资条/工资表", ("工资条", "工资表", "工资单", "薪资")),
    "contract": ("劳动合同", ("劳动合同", "聘用合同", "合同书")),
    "id_card": ("身份证明", ("身份证", "居民身份证", "户籍")),
    "award": ("裁决书/判决书", ("裁决书", "裁决", "判决书", "判决")),
    "termination": ("解除/离职材料", ("解除", "离职", "辞退", "开除", "通知书")),
}


def concepts_of(text: str) -> set[str]:
    """文本里出现的类型概念（空集表示认不出类型，此时不做类型否决）。"""
    t = text or ""
    return {cid for cid, (_label, kws) in CONCEPTS.items() if any(k in t for k in kws)}


def label_of(cid: str) -> str:
    return CONCEPTS.get(cid, (cid, ()))[0]


def load_overrides(path: Path | None) -> dict[str, tuple[str, tuple[str, ...]]]:
    """读取用户自定义词表：concept -> [关键词…]，与内置合并（新增概念也行）。"""
    if not path or not Path(path).exists():
        return dict(CONCEPTS)
    import yaml
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    merged = dict(CONCEPTS)
    for cid, spec in (data.get("concepts") or {}).items():
        label = spec[0] if isinstance(spec, list) and spec and isinstance(spec[0], str) else cid
        kws = spec[1] if isinstance(spec, list) and len(spec) > 1 else spec
        merged[cid] = (label, tuple(kws or []))
    return merged
