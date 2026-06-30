"""
[v9] NPC 年度生命周期演化系统（重构版）

核心改进：
1. 关系约束：已婚NPC不能再次结婚，妻子/丈夫角色不能变
2. 地点中文化：所有location使用中文名
3. LLM辅助推演：用AI生成符合设定的演化叙事
4. 标签驱动：NPC标签决定可触发的事件类型
5. 设定保护：核心身份（妻子/丈夫/父母）不可被演化改变
"""

from __future__ import annotations
import random
import logging
from typing import TYPE_CHECKING
from .schemas import NPCState, WorldState
from .prompt_utils import resolve_location_name  # [Bug] location code → display name

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM

logger = logging.getLogger("chronoverse.npc_evolution")


# ============================================================
# 事件类型定义（带约束条件）
# ============================================================

# 不可重复的标签（有了就不能再触发）
EXCLUSIVE_TAGS = {
    "已婚": ["marriage"],          # 已婚不能再结婚
    "已故": [],                     # 已故NPC不参与演化
    "退休": ["career_advance"],     # 退休后不能再升职
    "离家": ["leave_home"],        # 已经离家的不能再次离家
}

# 核心身份标签（不可被演化系统修改）
CORE_IDENTITY_TAGS = {
    "妻子", "丈夫", "母亲", "父亲", "儿子", "女儿",
    "主角之妻", "主角之夫", "主角之父", "主角之母",
}

# 事件约束：检查NPC是否满足触发条件
EVENT_CONSTRAINTS = {
    "marriage": lambda npc, player_state: (
        "已婚" not in npc.tags
        and "已故" not in npc.tags
        and npc.age >= 16
        # 不能是玩家的妻子/丈夫
        and "妻子" not in npc.tags
        and "丈夫" not in npc.tags
    ),
    "first_child": lambda npc, player_state: (
        "已婚" in npc.tags
        and "已故" not in npc.tags
        and "为人父母" not in npc.tags
        and npc.age >= 18
    ),
    "child_birth": lambda npc, player_state: (
        "已婚" in npc.tags
        and "已故" not in npc.tags
        and npc.age >= 18 and npc.age <= 45
    ),
    "career_advance": lambda npc, player_state: (
        "已故" not in npc.tags
        and "退休" not in npc.tags
        and npc.age >= 18
    ),
    "start_business": lambda npc, player_state: (
        "已故" not in npc.tags
        and "商人" not in npc.tags
        and npc.age >= 18
    ),
    "relocate": lambda npc, player_state: (
        "已故" not in npc.tags
        and "离家" not in npc.tags
    ),
    "illness": lambda npc, player_state: (
        "已故" not in npc.tags
        and "病人" not in npc.tags
    ),
    "accident": lambda npc, player_state: (
        "已故" not in npc.tags
    ),
    "death_illness": lambda npc, player_state: (
        "已故" not in npc.tags
        and npc.age >= 30
    ),
    "death_old_age": lambda npc, player_state: (
        "已故" not in npc.tags
        and npc.age >= 60
    ),
    "retire": lambda npc, player_state: (
        "已故" not in npc.tags
        and "退休" not in npc.tags
        and npc.age >= 55
    ),
    "leave_home": lambda npc, player_state: (
        "已故" not in npc.tags
        and "离家" not in npc.tags
        and npc.age >= 16 and npc.age <= 25
    ),
}


# ============================================================
# 年龄分组基准概率
# ============================================================

AGE_GROUP_PROBS = {
    (0, 12): {"grow_up": 1.0, "start_apprentice": 0.03},
    (13, 17): {"start_apprentice": 0.08, "leave_home": 0.04},
    (18, 25): {"marriage": 0.10, "first_child": 0.06, "join_faction": 0.06,
               "start_business": 0.05, "relocate": 0.06},
    (26, 35): {"marriage": 0.08, "child_birth": 0.10, "career_advance": 0.08,
               "start_business": 0.05, "relocate": 0.05, "accident": 0.02},
    (36, 50): {"child_birth": 0.04, "career_advance": 0.06,
               "wealth_change": 0.08, "relocate": 0.03, "illness": 0.04},
    (51, 65): {"illness": 0.08, "death_illness": 0.015, "retire": 0.06,
               "child_marriage": 0.05, "wealth_change": 0.06},
    (66, 80): {"illness": 0.15, "death_illness": 0.05, "death_old_age": 0.03,
               "retire": 0.04},
    (81, 999): {"illness": 0.20, "death_old_age": 0.10, "death_illness": 0.08},
}

PERSONALITY_MODIFIERS = {
    "冒险": {"relocate": 2.0, "accident": 1.5, "start_business": 1.3},
    "谨慎": {"relocate": 0.3, "accident": 0.5, "start_business": 0.6},
    "豪爽": {"marriage": 1.3},
    "孤僻": {"marriage": 0.3, "child_birth": 0.2, "join_faction": 0.1},
    "野心": {"career_advance": 2.5, "start_business": 2.0, "relocate": 1.8},
    "懒惰": {"career_advance": 0.2, "start_business": 0.1},
    "善良": {"marriage": 1.2},
    "贪婪": {"wealth_change": 1.5, "start_business": 1.3},
    "好斗": {"accident": 2.0},
}

TAG_EVENT_BOOSTS = {
    "修士": {"death_old_age": 0.01, "death_illness": 0.1, "relocate": 2.0},
    "武者": {"accident": 1.5, "death_illness": 0.7},
    "商人": {"wealth_change": 2.0, "relocate": 1.5, "start_business": 1.5},
    "官员": {"career_advance": 1.5, "relocate": 1.8},
    "农民": {"relocate": 0.2},
    "难民": {"relocate": 3.0, "accident": 2.0, "illness": 2.0},
    "病人": {"illness": 3.0, "death_illness": 3.0},
    "富人": {"marriage": 1.5, "wealth_change": 1.3},
    "穷人": {"marriage": 0.5, "illness": 1.5},
}


# ============================================================
# 事件效果定义
# ============================================================

EVENT_EFFECTS = {
    "marriage": {
        "desc_template": "{name}与当地一位志同道合的人结为连理",
        "tag_add": ["已婚"],
    },
    "first_child": {
        "desc_template": "{name}的第一个孩子出生了，是个健康的婴儿",
        "tag_add": ["为人父母"],
    },
    "child_birth": {
        "desc_template": "{name}的家庭迎来了一个新生命",
        "tag_add": [],
    },
    "start_business": {
        "desc_template": "{name}开了一家自己的店铺，做起了小生意",
        "tag_add": ["商人"],
        "role_change": "商人",
    },
    "career_advance": {
        "desc_template": "{name}在事业上取得了突破，地位有所提升",
        "tag_add": [],
    },
    "relocate": {
        "desc_template": "{name}收拾行囊，搬到了{new_location}",
        "tag_add": [],
        "location_change": True,
    },
    "retire": {
        "desc_template": "{name}年事已高，放下了手中的活计，开始安享晚年",
        "tag_add": ["退休"],
    },
    "wealth_change": {
        "desc_template": "{name}的财运发生了变化",
        "tag_add": [],
    },
    "illness": {
        "desc_template": "{name}不幸染上了疾病，身体状况下滑",
        "tag_add": ["病人"],
        "health_change": -30,
    },
    "accident": {
        "desc_template": "{name}遭遇了一场意外",
        "tag_add": [],
        "health_change": -20,
    },
    "death_illness": {
        "desc_template": "{name}因病去世，享年{age}岁",
        "tag_add": [],
        "is_death": True,
    },
    "death_old_age": {
        "desc_template": "{name}寿终正寝，安详地离开了人世，享年{age}岁",
        "tag_add": [],
        "is_death": True,
    },
    "grow_up": {
        "desc_template": "{name}又长大了一岁",
        "tag_add": [],
    },
    "leave_home": {
        "desc_template": "{name}离开了家乡，去闯荡世界",
        "tag_add": ["离家"],
        "location_change": True,
    },
}


# ============================================================
# 地点名称翻译
# ============================================================

LOCATION_CN = {
    "market": "集市", "government_office": "衙门", "temple": "寺庙",
    "inn": "客栈", "shop": "店铺", "manor": "府邸", "village": "村庄",
    "school": "学堂", "hospital": "医馆", "prison": "牢房",
    "military": "军营", "forest": "山林", "river": "河边",
}


def _translate_location(loc: str) -> str:
    """将英文location ID翻译为中文"""
    if not loc:
        return "某处"
    # 先查表
    for eng, cn in LOCATION_CN.items():
        if eng in loc.lower():
            return cn
    # 下划线替换
    result = loc.replace("_", "")
    return result or "某处"


# ============================================================
# LLM推演prompt
# ============================================================

NPC_EVOLUTION_PROMPT = """你是一个虚拟世界的NPC推演器。请根据以下信息，为NPC生成一段合理的年度生活变化。

【NPC信息】
姓名: {npc_name}
年龄: {npc_age}岁
身份: {npc_role}
标签: {npc_tags}
性格: {npc_personality}
当前位置: {npc_location}

【与主角的关系】
{relation_info}

【世界背景】
{world_context}

【约束规则 - 必须遵守】
1. {npc_name}的核心身份标签（如"妻子""丈夫""父亲""母亲"）不可改变
2. 如果{npc_name}是主角的妻子/丈夫，绝不能写其与他人结婚或出轨
3. 如果{npc_name}已有"已婚"标签，不能再触发结婚事件
4. 如果{npc_name}已有"已故"标签，只能写祭奠/回忆相关内容
5. 所有地点必须使用中文名称，禁止出现英文

【推演要求】
请生成{npc_name}这一年的生活变化，要求：
1. 符合其身份、性格、年龄
2. 与主角的关系保持不变（除非剧情特别需要）
3. 变化要合理，不要太戏剧化
4. 100字以内

直接输出叙事文本，不要JSON。"""


# ============================================================
# 主类
# ============================================================

class NpcLifeEvolution:
    """[v9] NPC年度生命周期演化引擎（重构版）"""

    def __init__(self, llm: "BaseLLM" = None):
        self.evolution_log: list[dict] = []
        self.dead_npcs: dict[str, dict] = {}
        self.last_year_evolved: int = 0
        self.llm = llm  # [v9] 注入LLM用于AI推演

    def evolve_year(self, npcs: dict[str, NPCState], world_state: WorldState,
                    known_locations: list[str] = None,
                    player_state=None) -> list[dict]:
        """
        [v9] 对所有NPC执行一年一度的生命演化。
        
        改进：
        - 检查关系约束（妻子不能嫁别人）
        - 地点中文化
        - 可选LLM推演模式
        """
        current_year = world_state.current_day // 365
        if current_year <= self.last_year_evolved:
            return []
        self.last_year_evolved = current_year

        year_events = []

        for npc_id, npc in npcs.items():
            if npc_id in self.dead_npcs:
                continue
            if "已故" in npc.tags:
                continue

            events = self._roll_npc_events(npc, world_state, known_locations, player_state)
            for event in events:
                self._apply_event(npc, npc_id, event, world_state, known_locations)
                event["npc_id"] = npc_id
                event["npc_name"] = npc.name
                event["year"] = current_year
                year_events.append(event)
                self.evolution_log.append(event)

        return year_events

    def evolve_single_npc_llm(self, npc: NPCState, player_state,
                               world_state: WorldState) -> str:
        """
        [v9] 用LLM为单个NPC生成年度推演叙事。
        比纯概率骰子更符合设定。
        """
        if not self.llm:
            return ""
        if "已故" in npc.tags:
            return ""

        # 构建关系信息
        relation_info = "无特殊关系"
        if player_state and player_state.relations:
            for nid, rel in player_state.relations.items():
                if nid == npc.name:
                    relation_info = f"与主角的关系: {rel.relation_type}，好感度: {rel.favor}"
                    break

        # 构建世界背景
        world_context = (
            f"{world_state.world_name}，第{world_state.current_day}天，"
            f"{world_state.season}，{world_state.weather}"
        )

        prompt = NPC_EVOLUTION_PROMPT.format(
            npc_name=npc.name,
            npc_age=npc.age,
            npc_role=npc.role or "普通居民",
            npc_tags=", ".join(npc.tags) if npc.tags else "无",
            npc_personality=npc.personality or "普通",
            npc_location=resolve_location_name(npc.current_location, world_state),  # [Bug] location code → display name
            relation_info=relation_info,
            world_context=world_context,
        )

        try:
            narrative = self.llm.chat(prompt, temperature=0.7, max_tokens=1024)
            return narrative
        except Exception as e:
            logger.warning("LLM NPC推演失败: %s", e)
            return ""

    def _roll_npc_events(self, npc: NPCState, world_state: WorldState,
                         known_locations: list[str] = None,
                         player_state=None) -> list[dict]:
        """[v9] 根据NPC属性掷骰子，增加约束检查"""
        age = npc.age
        events = []

        # 找到年龄分组
        age_probs = {}
        for (lo, hi), probs in AGE_GROUP_PROBS.items():
            if lo <= age <= hi:
                age_probs = dict(probs)
                break

        if not age_probs:
            return events

        # 应用性格修正
        for kw, mods in PERSONALITY_MODIFIERS.items():
            if kw in (npc.personality or "") or kw in "".join(npc.tags):
                for event_type, factor in mods.items():
                    if event_type in age_probs:
                        age_probs[event_type] *= factor

        # 应用标签修正
        for tag in npc.tags:
            if tag in TAG_EVENT_BOOSTS:
                for event_type, factor in TAG_EVENT_BOOSTS[tag].items():
                    if event_type in age_probs:
                        age_probs[event_type] *= factor

        # 健康影响
        if npc.stats.health < 50:
            age_probs["illness"] = age_probs.get("illness", 0) * 1.5
            age_probs["death_illness"] = age_probs.get("death_illness", 0) * 1.5

        # 掷骰并检查约束
        for event_type, prob in age_probs.items():
            # [v9] 检查事件约束
            constraint = EVENT_CONSTRAINTS.get(event_type)
            if constraint and not constraint(npc, player_state):
                continue  # 不满足约束，跳过

            if random.random() < min(prob, 0.95):
                event = {"type": event_type}
                effect = EVENT_EFFECTS.get(event_type, {})

                desc = effect.get("desc_template", "{name}发生了一件大事")
                desc = desc.replace("{name}", npc.name).replace("{age}", str(npc.age))

                if effect.get("location_change") and known_locations:
                    new_loc = random.choice(known_locations)
                    # [v9] 地点中文化
                    new_loc_cn = _translate_location(new_loc)
                    desc = desc.replace("{new_location}", new_loc_cn)
                    event["new_location"] = new_loc

                event["description"] = desc
                events.append(event)
                # [Bug] 单NPC每年最多触发1个重大生活事件，避免"搬家+做生意"同时发生
                break

        return events

    def _apply_event(self, npc: NPCState, npc_id: str, event: dict,
                     world_state: WorldState, known_locations: list[str] = None):
        """[v9] 将事件效果应用到NPC状态，增加身份保护"""
        effect = EVENT_EFFECTS.get(event["type"], {})

        # 标签（保护核心身份标签）
        for tag in effect.get("tag_add", []):
            # [v9] 检查是否与核心身份冲突
            if tag in CORE_IDENTITY_TAGS:
                continue  # 演化系统不能添加核心身份标签
            if tag not in npc.tags:
                npc.tags.append(tag)

        # 生命变化
        health_delta = effect.get("health_change", 0)
        if health_delta != 0:
            npc.stats.health = max(1, min(npc.stats.max_health,
                                          npc.stats.health + health_delta))

        # 位置变化
        if effect.get("location_change"):
            new_loc = event.get("new_location", "")
            if new_loc and new_loc != npc.current_location:
                npc.current_location = new_loc

        # 职业变化（不能改变核心身份相关的职业）
        if effect.get("role_change"):
            new_role = effect["role_change"]
            # [v9] 检查是否与核心身份冲突
            if not any(tag in npc.tags for tag in CORE_IDENTITY_TAGS):
                npc.record_role_change(new_role,
                                       event.get("description", ""),
                                       world_state.current_day)

        # 死亡
        if effect.get("is_death"):
            death_day = world_state.current_day
            self.dead_npcs[npc_id] = {
                "name": npc.name,
                "death_day": death_day,
                "death_age": npc.age,
                "cause": event["type"],
                "description": event["description"],
                "last_location": npc.current_location,
                "tags": list(npc.tags),
            }
            npc.stats.health = 0
            if "已故" not in npc.tags:
                npc.tags.append("已故")

    def get_dead_npc_info(self, npc_id: str) -> dict | None:
        return self.dead_npcs.get(npc_id)

    def get_evolution_summary(self, npc_id: str) -> list[dict]:
        return [e for e in self.evolution_log if e.get("npc_id") == npc_id]

    def to_dict(self) -> dict:
        return {
            "evolution_log": self.evolution_log[-200:],
            "dead_npcs": self.dead_npcs,
            "last_year_evolved": self.last_year_evolved,
        }

    def from_dict(self, data: dict):
        self.evolution_log = data.get("evolution_log", [])
        self.dead_npcs = data.get("dead_npcs", {})
        self.last_year_evolved = data.get("last_year_evolved", 0)
