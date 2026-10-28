"""可插拔的最小 LLM 调用层（默认关闭）。

用途只有两个，且都**一次批量调用**，不逐条调：
1. 证明目的缺填时的补写草稿（`suggest_proves`）；
2. 文件名没信息、OCR 也没命中时的归组建议（`suggest_assignment`）。

设计约束（用户要求：最小化调用与 token）：
- 默认不发请求，`--llm` 才启用；脚本能做的绝不问模型；
- 只送**摘要**：证据名称 + 该组材料前 N 字 OCR 文本，绝不送原图；
- 结果按输入哈希**落盘缓存**，同一案件重跑不再花钱；
- 输出必须是 JSON，解析失败就当没补（不猜）；补出来的都标 `proves_source="auto"`，
  必须进「待人工确认」清单；
- provider 可换：`--llm-base-url` 指向任何 OpenAI 兼容端点（含本地 ollama/vllm），
  换模型只改 base_url 与 model，代码不动。
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
CACHE_DIR = Path(os.path.expanduser("~/.cache/evidence-kit"))


@dataclass
class LLMConfig:
    base_url: str = DEFAULT_BASE
    model: str = DEFAULT_MODEL
    api_key_env: str = "DEEPSEEK_API_KEY"
    timeout: int = 60
    max_tokens: int = 1200

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()

    @property
    def ready(self) -> bool:
        return bool(self.api_key)


def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key[:24]}.json"


def _hash(payload) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def chat(messages: list[dict], cfg: LLMConfig) -> str:
    """发一次 chat/completions，返回文本。同样的请求命中缓存就不再花钱。"""
    key = _hash({"m": messages, "model": cfg.model})
    cached = _cache_path(key)
    if cached.exists():
        return cached.read_text(encoding="utf-8")

    body = json.dumps({"model": cfg.model, "messages": messages,
                       "temperature": 0.2, "max_tokens": cfg.max_tokens,
                       "response_format": {"type": "json_object"}}).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg.base_url.rstrip('/')}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg.api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"LLM 调用失败 {e.code}：{e.read()[:200]!r}") from e
    except Exception as e:                                       # noqa: BLE001
        raise RuntimeError(f"LLM 调用失败：{e}") from e
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    cached.write_text(text, encoding="utf-8")
    return text


def _json_of(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:                                            # noqa: BLE001
        i, j = text.find("{"), text.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(text[i:j + 1])
            except Exception:                                    # noqa: BLE001
                return {}
        return {}


SYS_PROVES = (
    "你是劳动仲裁卷宗的律师助理。用户给你一份证据目录的证据项（序号+证据名称）与该组材料的文字摘要。"
    "请为**缺少证明目的**的每一项补写一句证明目的。要求："
    "① 只写能从材料摘要里看出来、且属于证据本身能证明的事实；"
    "② 不要编造金额、日期、主体名称；看不出来就写空字符串；"
    "③ 每条不超过 60 字，用「证明……」句式；"
    "④ 只输出 JSON：{\"项目\": {\"序号\": \"证明目的\"}}。"
)

SYS_ASSIGN = (
    "你是劳动仲裁卷宗的律师助理。用户给你若干项证据（序号+证据名称）和一批还没归位的材料文件名"
    "（含少量文字摘要）。请判断每个文件最可能属于哪一项证据。要求："
    "① 只依据文件名与摘要，宁可不判也不要猜；判断不了给 null；"
    "② 只输出 JSON：{\"文件\": {\"文件名\": 序号或null, \"依据\": \"一句话\"}}。"
)


def _clip(text: str, n: int = 240) -> str:
    t = " ".join((text or "").split())
    return t[:n]


def suggest_proves(items: list[dict], cfg: LLMConfig) -> dict[int, str]:
    """items: [{"no": 1, "name": "...", "excerpt": "材料文字摘要"}] → {序号: 证明目的}。"""
    if not items or not cfg.ready:
        return {}
    payload = {"项目": [{"序号": it["no"], "名称": it["name"],
                        "材料摘要": _clip(it.get("excerpt", ""))} for it in items]}
    text = chat([{"role": "system", "content": SYS_PROVES},
                 {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], cfg)
    data = _json_of(text).get("项目") or {}
    out: dict[int, str] = {}
    for k, v in (data.items() if isinstance(data, dict) else []):
        try:
            no = int(str(k).strip())
        except ValueError:
            continue
        if isinstance(v, str) and v.strip():
            out[no] = v.strip()[:120]
    return out


def suggest_assignment(rows: list[dict], files: list[dict], cfg: LLMConfig) -> dict[str, int | None]:
    """rows: [{"no","name"}]；files: [{"file","excerpt"}] → {文件名: 序号 或 None}。"""
    if not rows or not files or not cfg.ready:
        return {}
    payload = {"证据项": [{"序号": r["no"], "名称": r["name"]} for r in rows],
               "未归位材料": [{"文件": f["file"], "摘要": _clip(f.get("excerpt", ""))}
                         for f in files]}
    text = chat([{"role": "system", "content": SYS_ASSIGN},
                 {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], cfg)
    data = _json_of(text).get("文件") or {}
    out: dict[str, int | None] = {}
    for fname, v in (data.items() if isinstance(data, dict) else []):
        no = v.get("序号") if isinstance(v, dict) else v
        try:
            out[str(fname)] = int(no) if no is not None else None
        except (TypeError, ValueError):
            out[str(fname)] = None
    return out
