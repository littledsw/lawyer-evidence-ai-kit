"""LLM 层：默认不发请求、结果缓存、解析失败不猜。"""
import json

from evidence_kit import llm


def test_not_ready_without_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cfg = llm.LLMConfig()
    assert not cfg.ready
    assert llm.suggest_proves([{"no": 1, "name": "x"}], cfg) == {}


def test_suggest_proves_parses_and_clips(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(llm, "chat", lambda m, c: json.dumps(
        {"项目": {"1": "证明申请人月工资标准为 4500 元；", "2": ""}}))
    got = llm.suggest_proves([{"no": 1, "name": "流水"}, {"no": 2, "name": "社保"}],
                             llm.LLMConfig())
    assert got == {1: "证明申请人月工资标准为 4500 元；"}      # 空值不采信


def test_assignment_null_is_respected(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(llm, "chat", lambda m, c: json.dumps(
        {"文件": {"a.jpg": {"序号": 1, "依据": "文件名"}, "b.jpg": {"序号": None}}}))
    got = llm.suggest_assignment([{"no": 1, "name": "流水"}],
                                 [{"file": "a.jpg"}, {"file": "b.jpg"}], llm.LLMConfig())
    assert got == {"a.jpg": 1, "b.jpg": None}


def test_bad_json_yields_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(llm, "chat", lambda m, c: "抱歉我不能")
    assert llm.suggest_proves([{"no": 1, "name": "x"}], llm.LLMConfig()) == {}


def test_cache_hit_avoids_second_call(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path)
    calls = {"n": 0}
    real = llm.chat

    def counted(messages, cfg):
        calls["n"] += 1
        return '{"项目": {"1": "证明工资"}}'

    monkeypatch.setattr(llm, "chat", counted)
    cfg = llm.LLMConfig()
    llm.suggest_proves([{"no": 1, "name": "x"}], cfg)
    llm.suggest_proves([{"no": 1, "name": "x"}], cfg)
    assert calls["n"] == 2        # 本层不做去重，缓存由 chat() 负责（见 test_chat_cache）
    monkeypatch.setattr(llm, "chat", real)
