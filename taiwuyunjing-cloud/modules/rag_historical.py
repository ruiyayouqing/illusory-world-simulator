from __future__ import annotations
import json
from collections import deque
from pathlib import Path
from .schemas import PlayerState, WorldState
from .llm.base_llm import BaseLLM


HISTORICAL_KNOWLEDGE_BASE = {
    "historical": {
        "明朝": {
            "dynasty": "明朝",
            "era_range": "1368-1644",
            "key_facts": [
                "明朝开国皇帝朱元璋，年号洪武",
                "永乐年间(1403-1424)朱棣迁都北京，郑和下西洋",
                "锦衣卫是皇帝的亲军情报机构",
                "内阁制度在永乐年间成型",
                "明朝科举以八股文取士",
                "永乐大典是世界上最大的百科全书",
                "明朝中后期资本主义萌芽出现",
                "张居正改革推行一条鞭法",
                "明朝灭亡于1644年李自成攻入北京",
            ],
            "culture": [
                "明朝服饰以汉族传统为主，官员穿补服",
                "明朝饮食文化发达，茶文化盛行",
                "明朝文学以四大名著中的三部为代表",
                "明朝建筑以紫禁城为代表",
            ],
            "locations": {
                "南京": "明朝初年首都，朱元璋定都于此",
                "北京": "永乐年后首都，紫禁城所在地",
                "苏州": "明朝经济文化重镇，丝绸业发达",
                "杭州": "西湖所在地，文人墨客聚集",
            },
        },
        "唐朝": {
            "dynasty": "唐朝",
            "era_range": "618-907",
            "key_facts": [
                "唐朝开国皇帝李渊，年号武德",
                "贞观之治(627-649)唐太宗李世民",
                "开元盛世(713-741)唐玄宗李隆基",
                "安史之乱(755-763)唐朝由盛转衰",
                "唐朝科举制度完善，进士科最重要",
                "唐朝长安是当时世界最大城市",
            ],
            "culture": [
                "唐朝诗歌达到巅峰，李白杜甫白居易",
                "唐朝开放包容，胡汉文化融合",
                "唐朝女性地位相对较高",
            ],
        },
        "宋朝": {
            "dynasty": "宋朝",
            "era_range": "960-1279",
            "key_facts": [
                "北宋定都开封，南宋定都杭州",
                "宋朝经济繁荣，GDP占世界一半以上",
                "活字印刷术、火药、指南针在宋朝成熟",
                "宋朝文人地位最高，重文轻武",
                "岳飞是南宋抗金名将",
            ],
        },
    }
}


class RAGHistoricalStore:
    def __init__(self, llm: BaseLLM, memory_store=None):
        self.llm = llm
        self.memory_store = memory_store
        self.loaded_eras: dict[str, dict] = {}
        # [Bug L5] 使用 deque(maxlen=100) 限制大小，防止 injected_facts 无界增长
        self.injected_facts: deque = deque(maxlen=100)

    def load_era_knowledge(self, era_name: str, world_type: str = "historical"):
        if world_type != "historical":
            return

        for dynasty_key, data in HISTORICAL_KNOWLEDGE_BASE.get("historical", {}).items():
            if dynasty_key in era_name or era_name in dynasty_key:
                self.loaded_eras[era_name] = data
                if self.memory_store:
                    for fact in data.get("key_facts", []):
                        self.memory_store.add_event_memory(0, "historical_fact", fact, "reference")
                    for fact in data.get("culture", []):
                        self.memory_store.add_event_memory(0, "historical_culture", fact, "reference")
                return

        self.loaded_eras[era_name] = {
            "dynasty": era_name,
            "era_range": "",
            "key_facts": [],
            "culture": [],
        }

    def retrieve_relevant_facts(self, query: str, era_name: str = "",
                                n_results: int = 5) -> list[str]:
        if self.memory_store and self.memory_store.collection.count() > 0:
            results = self.memory_store.search_memory(query, n_results=n_results)
            historical = [r["text"] for r in results
                          if "historical" in r.get("metadata", {}).get("type", "")]
            if historical:
                return historical[:n_results]

        if era_name and era_name in self.loaded_eras:
            era = self.loaded_eras[era_name]
            all_facts = era.get("key_facts", []) + era.get("culture", [])
            if all_facts:
                return all_facts[:n_results]

        return []

    def generate_historical_context(self, player: PlayerState,
                                    world_state: WorldState,
                                    current_event: str = "") -> str:
        if world_state.world_type != "historical":
            return ""

        era = world_state.era_name or world_state.world_name
        facts = self.retrieve_relevant_facts(current_event or world_state.event_history_summary,
                                             era, n_results=5)

        if not facts:
            return ""

        facts_text = "\n".join([f"- {f}" for f in facts])

        prompt = f"""你是一位历史顾问。根据以下真实历史资料，为当前场景提供准确的历史背景参考。

【当前场景】
{current_event or '世界事件'}

【相关历史资料】
{facts_text}

【要求】
1. 提取与当前场景最相关的历史事实
2. 用简短的中文描述（50-100字）
3. 确保历史准确性，不要编造
4. 如果当前场景不涉及历史，返回空字符串

直接输出历史参考文本，或返回空字符串。"""

        context = self.llm.chat(prompt, temperature=0.3, max_tokens=0)
        if context:
            self.injected_facts.append({
                "era": era,
                "facts_used": facts[:3],
                "context_generated": context[:100],
            })
        return context

    def validate_historical_claim(self, claim: str, era_name: str = "") -> dict:
        facts = self.retrieve_relevant_facts(claim, era_name, n_results=3)
        if not facts:
            return {"valid": True, "confidence": 0.5, "note": "无参考数据"}

        facts_text = "; ".join(facts)
        prompt = f"""验证以下历史说法是否准确。

【待验证说法】
{claim}

【参考历史资料】
{facts_text}

【输出JSON格式】
{{"valid": true/false, "confidence": 0-1的置信度, "note": "简要说明"}}

只输出JSON。"""
        return self.llm.chat_json(prompt, temperature=0.2)

    def get_era_info(self, era_name: str) -> dict:
        return self.loaded_eras.get(era_name, {})

    def get_injected_count(self) -> int:
        return len(self.injected_facts)
