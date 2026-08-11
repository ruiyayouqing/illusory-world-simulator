from __future__ import annotations
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime
from dataclasses import fields as dataclass_fields


def safe_dataclass_from_dict(cls, data: dict):
    """[v10] 安全的 dataclass 反序列化：忽略未知字段，避免 cls(**d) 崩溃"""
    if not data:
        return cls()
    valid_keys = {f.name for f in dataclass_fields(cls)}
    filtered = {k: v for k, v in data.items() if k in valid_keys}
    return cls(**filtered)


class Stats(BaseModel):
    health: int = 100
    max_health: int = 100
    energy: int = 100
    max_energy: int = 100
    strength: int = 5
    agility: int = 5
    intelligence: int = 5
    magic: int = 0
    luck: int = 5


class Social(BaseModel):
    reputation: int = 0
    position: str = "无名氏"
    faction: str = "无"
    gold: int = 100


class InventoryItem(BaseModel):
    name: str
    quantity: int = 1
    item_type: str = "misc"


class Inventory(BaseModel):
    gold: int = 100
    items: list[InventoryItem] = Field(default_factory=list)


class RelationEntry(BaseModel):
    favor: int = 50
    relation_type: str = "陌生人"
    description: str = ""
    interaction_count: int = 0
    last_interaction: str = ""


class LongTermIdentity(BaseModel):
    """长期身份语义核心：双过程记忆的慢整合层"""
    values: list[str] = Field(default_factory=list)           # 价值观
    personality_traits: list[str] = Field(default_factory=list)  # 稳定性格特征
    habits: list[str] = Field(default_factory=list)            # 习惯
    social_records: list[str] = Field(default_factory=list)    # 社交摘要
    knowledge: list[str] = Field(default_factory=list)         # 积累的知识
    consolidation_count: int = 0  # 整合次数


class PlayerMemory(BaseModel):
    short_term: list[str] = Field(default_factory=list)
    long_term_summary: str = ""
    long_term_identity: LongTermIdentity = Field(default_factory=LongTermIdentity)


class PlayerState(BaseModel):
    agent_id: str = "player_01"
    name: str = "无名"
    age: int = 18
    birth_year: int = 1398
    max_age: int = 80

    stats: Stats = Field(default_factory=Stats)
    social: Social = Field(default_factory=Social)
    tags: list[str] = Field(default_factory=lambda: ["普通人"])
    inventory: Inventory = Field(default_factory=Inventory)
    relations: dict[str, RelationEntry] = Field(default_factory=dict)
    memory: PlayerMemory = Field(default_factory=PlayerMemory)

    location: str = "village"
    status_effects: list[str] = Field(default_factory=list)
    current_goal: str = "活下去"

    # [v10++] 角色动态状态（CHIRON 式）：可选字段，由 CharacterStateManager 统一管理
    # 此字段仅用于持久化兜底，运行时优先使用 CharacterStateManager
    dynamic_state: dict = Field(default_factory=dict)

    @field_validator("age", "max_age", mode="before")
    @classmethod
    def parse_age(cls, v):
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            import re
            m = re.search(r'\d+', v)
            if m:
                return int(m.group())
        return 18


class NPCState(BaseModel):
    agent_id: str
    name: str
    age: int = 20
    role_type: str = "npc"

    stats: Stats = Field(default_factory=Stats)
    tags: list[str] = Field(default_factory=list)
    personality: str = ""
    speaking_style: str = ""
    dialogue_examples: list[str] = Field(default_factory=list)

    role: str = ""
    role_history: list[dict] = Field(default_factory=list)
    relation_history: list[dict] = Field(default_factory=list)

    current_location: str = ""
    status_effects: list[str] = Field(default_factory=list)

    relation_to_player: RelationEntry = Field(default_factory=RelationEntry)
    recent_actions: list[dict] = Field(default_factory=list)

    # [Bug] 每日行动限制：记录上次行动的游戏天数，防止NPC一天内多次行动/搬家
    last_action_day: int = 0
    # [v1.2] 时序轮询：记录上次行动的时段标识（格式 "day:time_slot"），
    # 让 NPC 每个时段最多行动1次，配合 daily_routine 实现时段感知
    # 空字符串表示从未行动过（老存档兼容）
    last_action_turn: str = ""

    mbti_type: str = ""  # MBTI 性格类型（如 INTJ），影响决策风格

    ai_behavior: dict = Field(default_factory=lambda: {
        "personality_traits": [],
        "current_goal": "",
        "long_term_goal": "",
        "short_term_goals": [],
        "decision_style": "normal"
    })

    # [v10.1] NPC对玩家的印象（人物卡闭环核心）
    impression_of_player: dict = Field(default_factory=lambda: {
        "summary": "",  # 对玩家的总体印象
        "known_traits": [],  # 观察到的玩家特质
        "memorable_interactions": [],  # 难忘的互动（最多5条）
        "trust_level": 50,  # 信任度 0-100
        "last_updated_day": 0,
        "interaction_count": 0,
    })

    # [v10++] 角色动态状态（CHIRON 式）：可选字段，由 CharacterStateManager 统一管理
    # 此字段仅用于持久化兜底，运行时优先使用 CharacterStateManager
    dynamic_state: dict = Field(default_factory=dict)

    # [NovelRoleplay] 小说扮演扩展：支持「未来角色」全量注入 + 概率登场 + 知识边界
    # is_dormant=True 的 NPC 不会出现在场景中、不会被 NPCAgent 主循环处理，
    # 直到登场条件满足后才会被激活。
    is_dormant: bool = False
    # [功能二] 玩家手动隐藏的 NPC：不出现在列表/聊天/场景中，不被主循环处理，
    # 但数据完整保留（血缘/关系/记忆不动），可随时恢复。区别于 is_dormant（系统自动休眠）。
    hidden: bool = False
    # [v1.2] 休眠开始日（is_dormant 翻转为 True 时记录，唤醒时用于计算休眠时长）
    # 唤醒时调用 LLM 做时间跳跃推演，输入休眠时长 + 期间世界大事，补齐断层
    dormant_since_day: int = 0
    # 登场条件：原著章节/地点/事件/概率，满足任一组合即可激活
    # {"min_chapter": int, "locations": [str], "trigger_events": [str],
    #  "probability_per_day": float, "min_day_offset": int}
    appearance_conditions: dict = Field(default_factory=dict)
    # 知识边界（目标感知模式）：NPC 知道自己的目标和动机，但不知道具体未来事件
    # {"knows_goals": [str], "knows_facts": [str], "forbidden_knowledge": [str]}
    knowledge_scope: dict = Field(default_factory=dict)
    # 原著中的出场章节（用于判断是否为「未来角色」）
    original_chapter: int = -1
    # 原著未来事件摘要（仅系统层使用，不直接进入 NPC 的可见记忆）
    # 格式：[{"chapter": int, "event": str, "importance": float}, ...]
    original_future: list = Field(default_factory=list)

    # [v1.3] NPC 性格演化轨迹（全模式通用）
    # 记录性格转折点：重大事件触发的性格重塑
    # 格式：[{"turn": int, "day": int, "trauma": str, "from_traits": [str],
    #        "to_traits": [str], "narrative": str, "trigger_event": str}, ...]
    personality_history: list[dict] = Field(default_factory=list)
    # [v1.3] NPC 私密档案（全模式通用，普通模式由开关控制）
    # 格式：[{"fact": str, "type": "secret/past/preference/wish", "created_day": int}, ...]
    private_facts: list[dict] = Field(default_factory=list)
    # [v1.3] 标记是否已生成过私密档案（避免重复生成）
    private_facts_generated: bool = False

    # [v12.7] NPC 时间戳（回滚用）：记录 NPC 首次/最近出场的 turn
    # - first_seen_turn: 首次出现在叙事中的 turn；-1 = 世界初始 NPC（回滚永不删除）
    # - last_seen_turn: 最近一次出现在叙事中的 turn；-1 = 从未出场
    # 回滚到 target_turn 时：first_seen_turn > target 的 NPC 视为该轮新增 → 移除；
    # last_seen_turn > target 的恢复为 target（仅回退出场记录，不删除既有 NPC）
    first_seen_turn: int = -1
    last_seen_turn: int = -1

    # ===== [v1.5 第二期] 动机/立场/血缘 =====
    # 6 类动机：survival/social/career/exploration/legacy/transcendence
    # 格式：[{"type": str, "intensity": int, "target": str, "triggered_day": int,
    #        "decay_rate": float, "reason": str}, ...]
    motivations: list[dict] = Field(default_factory=list)

    # 立场名誉（懒加载默认值，老存档兼容）
    alignment: str = "中庸"            # 仁善/刚正/中庸/无畏/桀骜/狂邪/唯我
    faction_id: str | None = None      # 所属势力 id
    personal_reputation: int = 0       # 个人名誉 -100~+100
    # 对各势力的声望：{faction_id: standing -100~+100}
    faction_standing: dict[str, int] = Field(default_factory=dict)

    # 血缘家族（懒加载默认值，老存档兼容）
    family_id: str | None = None
    parents: list[str] = Field(default_factory=list)   # NPC id 列表
    spouse: str | None = None                          # NPC id
    children: list[str] = Field(default_factory=list)  # NPC id 列表
    siblings: list[str] = Field(default_factory=list)  # NPC id 列表（含同门）

    def record_role_change(self, new_role: str, reason: str, day: int):
        """记录一次身份变更"""
        old_role = self.role
        if old_role and old_role != new_role:
            self.role_history.append({
                "from": old_role, "to": new_role,
                "reason": reason, "day": day
            })
        self.role = new_role

    def record_relation_change(self, new_relation: str, reason: str, day: int):
        """记录一次与玩家关系的变更"""
        old_rel = self.relation_to_player.relation_type
        if old_rel and old_rel != new_relation:
            self.relation_history.append({
                "from": old_rel, "to": new_relation,
                "reason": reason, "day": day
            })

    def get_identity_summary(self) -> str:
        """生成用于注入LLM的身份摘要"""
        parts = [self.name]
        if self.role:
            parts.append(f"职业={self.role}")
        if self.relation_to_player.relation_type and self.relation_to_player.relation_type != "陌生人":
            parts.append(f"关系={self.relation_to_player.relation_type}")
        if self.personality:
            parts.append(f"性格={self.personality[:40]}")
        if self.speaking_style:
            parts.append(f"说话={self.speaking_style[:30]}")
        # 显示最近一次身份变更
        if self.role_history:
            last_change = self.role_history[-1]
            parts.append(f"(第{last_change['day']}天从{last_change['from']}变为{self.role})")
        return " | ".join(parts)

    @field_validator("age", mode="before")
    @classmethod
    def parse_age(cls, v):
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            import re
            m = re.search(r'\d+', v)
            if m:
                return int(m.group())
        return 20


class Faction(BaseModel):
    power: int = 50
    stability: int = 50
    # [v1.5 第二期] 立场名誉系统扩展（向后兼容，老存档默认值）
    ideology: str = "中庸"               # 意识形态：仁善/刚正/中庸/无畏/桀骜/狂邪/唯我
    leader: str = ""                      # 族长/首领 NPC id
    members: list[str] = Field(default_factory=list)  # 成员 NPC id 列表
    enemies: list[str] = Field(default_factory=list)  # 敌对势力 id 列表
    allies: list[str] = Field(default_factory=list)   # 盟友势力 id 列表


class AMMPoolState(BaseModel):
    """单个商品的 AMM 流动性池状态"""
    commodity_reserve: float = 1000.0   # 商品储备量
    currency_reserve: float = 100000.0  # 货币储备量
    constant_product: float = 0.0       # 恒定乘积 k = commodity * currency
    total_trades: int = 0               # 累计交易次数
    volume_24h: float = 0.0             # 24h交易量（货币计价）

    def model_post_init(self, __context):
        if self.constant_product == 0.0:
            self.constant_product = self.commodity_reserve * self.currency_reserve


class Economy(BaseModel):
    base_prices: dict[str, float] = Field(default_factory=dict)
    supply_demand: dict[str, float] = Field(default_factory=dict)
    inflation_rate: float = 1.0
    # AMM 做市商系统
    amm_pools: dict[str, AMMPoolState] = Field(default_factory=dict)
    macro_price_index: float = 1.0      # 宏观价格指数
    food_inflation: float = 1.0         # 食品通胀率
    non_food_inflation: float = 1.0     # 非食品通胀率
    price_history: list[dict] = Field(default_factory=list)  # 价格历史快照


class MacroEvent(BaseModel):
    event_id: str
    event_type: str
    description: str
    affected_locations: list[str] = Field(default_factory=list)
    affected_agents: list[str] = Field(default_factory=list)
    impact_level: int = 5
    start_day: int = 0
    end_day: Optional[int] = None

    @field_validator("impact_level", mode="before")
    @classmethod
    def coerce_impact(cls, v):
        return int(float(v)) if v is not None else 5

    @field_validator("start_day", "end_day", mode="before")
    @classmethod
    def coerce_day(cls, v):
        return int(float(v)) if v is not None else v


class WorldState(BaseModel):
    world_id: str = ""
    world_type: str = "historical"
    world_name: str = "未知世界"
    description: str = ""
    current_day: int = 1
    current_time: str = "清晨"
    crisis_level: int = 0

    current_year: int = 1400
    current_month: int = 1
    current_day_of_month: int = 1
    era_name: str = ""
    era_year: int = 1

    factions: dict[str, Faction] = Field(default_factory=dict)
    locations: dict[str, dict] = Field(default_factory=dict)
    active_events: list[MacroEvent] = Field(default_factory=list)
    event_history_summary: str = ""
    economy: Economy = Field(default_factory=Economy)

    weather: str = "晴朗"
    season: str = "春季"

    # [v9] 金手指开关
    golden_finger: bool = False

    def get_full_date(self) -> str:
        if self.era_name:
            return f"{self.era_name}{self.era_year}年{self.current_month}月{self.current_day_of_month}日"
        return f"{self.current_year}年{self.current_month}月{self.current_day_of_month}日"

    def get_date_display(self) -> str:
        time_names = {"清晨": "卯时", "上午": "巳时", "中午": "午时",
                      "下午": "未时", "傍晚": "酉时", "深夜": "子时"}
        time_cn = time_names.get(self.current_time, self.current_time)
        return f"{self.get_full_date()} {time_cn}"


class SaveMeta(BaseModel):
    current_turn: int = 0
    current_day: int = 1
    current_time: str = "06:00"
    phase: str = "player_input"
    phase_history: list[dict] = Field(default_factory=list)
    save_type: str = "auto"
    save_timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    save_time_display: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))


class SaveManifest(BaseModel):
    world_id: str
    world_name: str
    world_type: str
    description: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    created_at_display: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    last_saved_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    last_saved_at_display: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    version: str = "0.2.0"
    total_turns: int = 0
    current_day: int = 1
    player_name: str = ""
    player_age: int = 18


class WorldDef(BaseModel):
    world_name: str = "未知世界"
    world_type: str = "historical"
    description: str = ""
    initial_event: str = ""
    rules: dict = Field(default_factory=dict)


class LocationDef(BaseModel):
    location_code: str
    location_name: str
    description: str = ""
    detail: str = ""
    special_actions: list[str] = Field(default_factory=list)

    @field_validator("special_actions", mode="before")
    @classmethod
    def _normalize_special_actions(cls, v):
        if not v:
            return []
        result = []
        for item in v:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                action = item.get("action", "")
                condition = item.get("condition", "")
                if condition:
                    result.append(f"{action}（条件：{condition}）")
                else:
                    result.append(action)
            else:
                result.append(str(item))
        return result


# ── [v10] 新增数据模型 ──────────────────────────────────────

class MemoryEntry(BaseModel):
    """[v10] 带重要性评分的记忆条目"""
    text: str
    importance: float = 0.5           # 0.0-1.0 重要性评分
    emotional_weight: float = 0.0     # 情感权重（高情感事件更难忘）
    access_count: int = 0             # 被检索次数
    last_accessed_turn: int = 0       # 最近一次被检索的回合
    created_turn: int = 0
    created_day: int = 0
    memory_type: str = "narrative"    # narrative/event/dialogue/lesson
    tags: list[str] = Field(default_factory=list)


class ButterflyApproval(BaseModel):
    """[v10] 蝴蝶效应审批记录"""
    approval_id: str
    turn: int
    day: int
    player_action: str
    impact_score: float
    impact_type: str
    description: str
    proposed_consequences: list[str] = Field(default_factory=list)
    status: str = "pending"           # pending/approved/rejected/modified
    player_decision: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class LearningRecord(BaseModel):
    """[v10] 闭环学习记录"""
    record_id: str
    turn: int
    day: int
    lesson_type: str                  # preference/quality/consistency
    content: str
    importance: float = 0.5
    applied_count: int = 0
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class NpcDialogueSession(BaseModel):
    """[v1.6] NPC-NPC 对话会话记录（用于持久化与「江湖见闻」展示）"""
    session_id: str
    day: int
    time: str = ""
    location: str = ""
    location_name: str = ""
    scene_type: str = ""              # encounter/discuss/conflict/smalltalk/confide/group
    scene_name: str = ""
    participants: list[dict] = Field(default_factory=list)
    # 每条 dialogue: {speaker, content, emotion, action}
    dialogue: list[dict] = Field(default_factory=list)
    summary: str = ""
    relation_change: dict = Field(default_factory=dict)
    topic_tags: list[str] = Field(default_factory=list)
    player_witnessed: bool = False
    # 传闻流用：是否已传播给玩家
    delivered_to_player: bool = False
