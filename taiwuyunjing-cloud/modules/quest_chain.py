"""
[v9] 任务链系统 — 多步骤任务，支持分支选择、道德困境、长期后果

数据模型：
- QuestChain: 任务链（包含多个步骤）
- QuestStep: 单个步骤（包含完成条件和分支选择）
- QuestChoice: 分支选择（包含后果和道德倾向）
- ActiveQuest: 玩家当前进行中的任务状态

功能：
- LLM动态生成任务链
- 预置示例任务链
- 条件检测（标签/物品/位置/关系）
- 长期后果延迟触发
- 道德倾向追踪
"""
from __future__ import annotations
import json
import logging
import uuid
from enum import Enum
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM
    from .schemas import PlayerState, WorldState, NPCState

logger = logging.getLogger("chronoverse.quest_chain")


# ── 数据模型 ──────────────────────────────────────────────

class MoralAlignment(str, Enum):
    GOOD = "good"
    NEUTRAL = "neutral"
    EVIL = "evil"


class QuestChoice(BaseModel):
    """任务分支选择"""
    choice_id: str
    text: str
    description: str = ""
    next_step_id: str  # 指向下一步的step_id，""表示任务结束
    consequences: dict = Field(default_factory=dict)
    # consequences 示例：
    # {"gold": -100, "tags_add": ["善良"], "tags_remove": ["冷酷"],
    #  "favor_changes": {"张三": 20}, "reputation": 10}
    moral_alignment: MoralAlignment = MoralAlignment.NEUTRAL
    moral_score: int = 0  # -100(极恶) 到 +100(极善)


class QuestStep(BaseModel):
    """任务链单个步骤"""
    step_id: str
    title: str
    description: str  # 步骤描述（展示给玩家）
    scene_narrative: str = ""  # 场景叙事（LLM生成或预置）
    conditions: list[str] = Field(default_factory=list)
    # conditions 格式：
    # "tag:穿越者" - 需要标签
    # "item:玉佩" - 需要物品
    # "location:清风寺" - 需要在某地
    # "favor:张三:50" - 需要与某NPC好感度>=50
    # "gold:100" - 需要金币>=100
    # "level:3" - 需要等级>=3
    auto_complete: bool = False  # 条件满足时自动完成
    choices: list[QuestChoice] = Field(default_factory=list)
    on_enter_effects: dict = Field(default_factory=dict)
    # 进入步骤时触发的效果


class QuestChain(BaseModel):
    """任务链定义"""
    chain_id: str
    name: str
    description: str
    category: str = "main"  # main/side/faction/personal
    steps: dict[str, QuestStep] = Field(default_factory=dict)
    start_step_id: str  # 起始步骤
    prerequisites: list[str] = Field(default_factory=list)
    # prerequisites 格式同 conditions
    long_term_effects: list[dict] = Field(default_factory=list)
    # 长期后果：延迟N天后触发
    # [{"delay_days": 3, "effects": {"tags_add": ["通缉犯"]}, "narrative": "..."}]
    time_limit_days: Optional[int] = None  # 可选时间限制
    repeatable: bool = False
    moral_impact: bool = False  # 是否追踪道德倾向


class ActiveQuest(BaseModel):
    """玩家进行中的任务状态"""
    chain_id: str
    current_step_id: str
    started_day: int
    choices_made: list[dict] = Field(default_factory=list)
    # [{"step_id": "xxx", "choice_id": "yyy", "day": 10}]
    moral_score: int = 0  # 累计道德分数
    status: str = "active"  # active/completed/failed/abandoned
    completion_day: Optional[int] = None


# ── 任务链管理器 ──────────────────────────────────────────

class QuestChainManager:
    """任务链管理器"""

    def __init__(self, llm: "BaseLLM" = None, data_dir: str = None):
        self.llm = llm
        self.definitions: dict[str, QuestChain] = {}  # chain_id -> QuestChain
        self.active_quests: dict[str, ActiveQuest] = {}  # chain_id -> ActiveQuest
        self.completed_chains: list[str] = []
        self.pending_effects: list[dict] = []  # 延迟触发的效果
        self._data_dir = Path(data_dir) if data_dir else None

        # 加载预置任务链
        self._load_builtin_chains()

    def _load_builtin_chains(self):
        """加载预置任务链"""
        for chain in BUILTIN_QUEST_CHAINS:
            self.definitions[chain.chain_id] = chain
            logger.debug("加载预置任务链: %s", chain.name)

    def get_available_chains(self, player: "PlayerState",
                              world: "WorldState",
                              npcs: dict[str, "NPCState"] = None) -> list[QuestChain]:
        """获取当前可接取的任务链"""
        available = []
        for chain_id, chain in self.definitions.items():
            # 跳过已完成的不可重复任务
            if chain_id in self.completed_chains and not chain.repeatable:
                continue
            # 跳过已接取的
            if chain_id in self.active_quests:
                continue
            # 检查前置条件
            if self._check_conditions(chain.prerequisites, player, world, npcs):
                available.append(chain)
        return available

    def start_chain(self, chain_id: str, player: "PlayerState",
                    world: "WorldState") -> Optional[ActiveQuest]:
        """开始任务链"""
        chain = self.definitions.get(chain_id)
        if not chain:
            return None

        quest = ActiveQuest(
            chain_id=chain_id,
            current_step_id=chain.start_step_id,
            started_day=world.current_day,
        )
        self.active_quests[chain_id] = quest

        # 触发起始步骤的 on_enter_effects
        start_step = chain.steps.get(chain.start_step_id)
        if start_step and start_step.on_enter_effects:
            self._apply_effects(start_step.on_enter_effects, player, {})

        logger.info("任务链开始: %s (步骤: %s)", chain.name, chain.start_step_id)
        return quest

    def make_choice(self, chain_id: str, choice_id: str,
                    player: "PlayerState", world: "WorldState",
                    npcs: dict[str, "NPCState"] = None) -> dict:
        """做出任务选择"""
        quest = self.active_quests.get(chain_id)
        chain = self.definitions.get(chain_id)
        if not quest or not chain:
            return {"error": "任务不存在"}

        step = chain.steps.get(quest.current_step_id)
        if not step:
            return {"error": "步骤不存在"}

        # 找到选择
        choice = None
        for c in step.choices:
            if c.choice_id == choice_id:
                choice = c
                break
        if not choice:
            return {"error": f"选项 {choice_id} 不存在"}

        # 记录选择
        quest.choices_made.append({
            "step_id": quest.current_step_id,
            "choice_id": choice_id,
            "day": world.current_day,
        })

        # 应用后果
        self._apply_effects(choice.consequences, player, npcs or {})

        # 更新道德分数
        quest.moral_score += choice.moral_score

        # 处理长期后果
        for effect in chain.long_term_effects:
            self.pending_effects.append({
                "chain_id": chain_id,
                "trigger_day": world.current_day + effect.get("delay_days", 0),
                "effects": effect.get("effects", {}),
                "narrative": effect.get("narrative", ""),
            })

        # 推进步骤
        if choice.next_step_id:
            quest.current_step_id = choice.next_step_id
            next_step = chain.steps.get(choice.next_step_id)
            if next_step and next_step.on_enter_effects:
                self._apply_effects(next_step.on_enter_effects, player, npcs or {})
            return {
                "status": "continue",
                "next_step": next_step.model_dump() if next_step else None,
                "narrative": choice.description,
            }
        else:
            # 任务完成
            quest.status = "completed"
            quest.completion_day = world.current_day
            self.completed_chains.append(chain_id)
            del self.active_quests[chain_id]
            logger.info("任务链完成: %s (道德分数: %d)", chain.name, quest.moral_score)
            return {
                "status": "completed",
                "chain_name": chain.name,
                "moral_score": quest.moral_score,
                "narrative": choice.description,
            }

    def check_auto_complete(self, player: "PlayerState",
                             world: "WorldState",
                             npcs: dict[str, "NPCState"] = None) -> list[dict]:
        """检查自动完成的步骤"""
        results = []
        for chain_id, quest in list(self.active_quests.items()):
            chain = self.definitions.get(chain_id)
            if not chain:
                continue
            step = chain.steps.get(quest.current_step_id)
            if step and step.auto_complete:
                if self._check_conditions(step.conditions, player, world, npcs):
                    # 自动完成，使用第一个选择（如果有）
                    if step.choices:
                        result = self.make_choice(
                            chain_id, step.choices[0].choice_id, player, world, npcs
                        )
                        results.append(result)
        return results

    def check_pending_effects(self, current_day: int) -> list[dict]:
        """检查并触发延迟效果"""
        triggered = []
        remaining = []
        for effect in self.pending_effects:
            if current_day >= effect["trigger_day"]:
                triggered.append(effect)
            else:
                remaining.append(effect)
        self.pending_effects = remaining
        return triggered

    def _check_conditions(self, conditions: list[str],
                           player: "PlayerState",
                           world: "WorldState",
                           npcs: dict[str, "NPCState"] = None) -> bool:
        """检查条件列表（AND逻辑）"""
        for cond in conditions:
            if not self._check_single_condition(cond, player, world, npcs):
                return False
        return True

    def _check_single_condition(self, cond: str,
                                 player: "PlayerState",
                                 world: "WorldState",
                                 npcs: dict[str, "NPCState"] = None) -> bool:
        """检查单个条件"""
        parts = cond.split(":")
        if len(parts) < 2:
            return True

        cond_type = parts[0]
        cond_value = parts[1]

        if cond_type == "tag":
            return cond_value in player.tags
        elif cond_type == "item":
            return any(item.name == cond_value for item in player.inventory.items)
        elif cond_type == "location":
            return player.location == cond_value
        elif cond_type == "favor":
            if len(parts) >= 3 and npcs:
                npc_name = parts[1]
                min_favor = int(parts[2])
                for npc in npcs.values():
                    if npc.name == npc_name:
                        rel = player.relations.get(npc.agent_id)
                        return rel.favor >= min_favor if rel else False
            return False
        elif cond_type == "gold":
            return player.social.gold >= int(cond_value)
        elif cond_type == "level":
            # 需要level_system支持
            return True
        elif cond_type == "day":
            return world.current_day >= int(cond_value)
        elif cond_type == "reputation":
            return player.social.reputation >= int(cond_value)

        return True

    def _apply_effects(self, effects: dict, player: "PlayerState",
                        npcs: dict[str, "NPCState"]):
        """应用效果"""
        if "gold" in effects:
            player.social.gold = max(0, player.social.gold + effects["gold"])
        if "health" in effects:
            player.stats.health = max(0, min(player.stats.max_health,
                                              player.stats.health + effects["health"]))
        if "energy" in effects:
            player.stats.energy = max(0, min(player.stats.max_energy,
                                              player.stats.energy + effects["energy"]))
        if "reputation" in effects:
            player.social.reputation = max(0, player.social.reputation + effects["reputation"])
        if "tags_add" in effects:
            for tag in effects["tags_add"]:
                if tag not in player.tags and len(player.tags) < 30:
                    player.tags.append(tag)
        if "tags_remove" in effects:
            for tag in effects["tags_remove"]:
                if tag in player.tags:
                    player.tags.remove(tag)
        if "favor_changes" in effects:
            for npc_name, delta in effects["favor_changes"].items():
                for npc in npcs.values():
                    if npc.name == npc_name:
                        rel = player.relations.get(npc.agent_id)
                        if rel:
                            rel.favor = max(0, min(100, rel.favor + delta))
                        break

    def get_active_quests_summary(self) -> list[dict]:
        """获取当前进行中的任务摘要"""
        summaries = []
        for chain_id, quest in self.active_quests.items():
            chain = self.definitions.get(chain_id)
            if chain:
                step = chain.steps.get(quest.current_step_id)
                summaries.append({
                    "chain_id": chain_id,
                    "name": chain.name,
                    "current_step": step.title if step else "?",
                    "description": step.description if step else "",
                    "choices": [
                        {"id": c.choice_id, "text": c.text, "alignment": c.moral_alignment}
                        for c in (step.choices if step else [])
                    ],
                    "moral_score": quest.moral_score,
                    "days_active": quest.started_day,
                })
        return summaries

    def to_dict(self) -> dict:
        """序列化"""
        return {
            "active": {k: v.model_dump() for k, v in self.active_quests.items()},
            "completed": self.completed_chains,
            "pending_effects": self.pending_effects,
        }

    def from_dict(self, data: dict):
        """反序列化"""
        for k, v in data.get("active", {}).items():
            self.active_quests[k] = ActiveQuest(**v)
        self.completed_chains = data.get("completed", [])
        self.pending_effects = data.get("pending_effects", [])


# ── 预置任务链 ──────────────────────────────────────────────

BUILTIN_QUEST_CHAINS: list[QuestChain] = [
    QuestChain(
        chain_id="qingsong_temple",
        name="清风寺之谜",
        description="传闻清风寺中藏有上古秘宝，但近日寺中怪事频发...",
        category="main",
        start_step_id="step1",
        prerequisites=["tag:穿越者"],
        steps={
            "step1": QuestStep(
                step_id="step1",
                title="寺中异闻",
                description="你听闻清风寺近日怪事频发，决定前往一探究竟。",
                scene_narrative="清风寺坐落于城外青山之中，古木参天，香火鼎盛。然而近日，寺中僧人夜间常闻诡异钟声，佛像竟会自行移位...",
                choices=[
                    QuestChoice(
                        choice_id="A",
                        text="直接进入寺庙探查",
                        description="你大步走入清风寺，向住持表明来意。",
                        next_step_id="step2",
                        consequences={"tags_add": ["清风寺探索"]},
                    ),
                    QuestChoice(
                        choice_id="B",
                        text="先在山下打探消息",
                        description="你在山脚茶馆坐下，向老板打听清风寺的近况。",
                        next_step_id="step2",
                        consequences={"tags_add": ["谨慎行事"], "favor_changes": {"茶馆老板": 10}},
                    ),
                ],
            ),
            "step2": QuestStep(
                step_id="step2",
                title="密室发现",
                description="你在寺庙深处发现了一间被封印的密室。",
                scene_narrative="穿过重重回廊，你来到寺庙后院。一堵看似普通的墙壁上，隐约可见古老的符文...",
                conditions=["tag:清风寺探索"],
                auto_complete=True,
                choices=[
                    QuestChoice(
                        choice_id="A",
                        text="破解封印进入密室",
                        description="你运起灵力，试图破解封印。",
                        next_step_id="step3",
                        consequences={"energy": -30},
                        moral_alignment=MoralAlignment.NEUTRAL,
                    ),
                    QuestChoice(
                        choice_id="B",
                        text="向住持报告发现",
                        description="你决定不擅自行动，向住持禀报。",
                        next_step_id="step3",
                        consequences={"reputation": 15, "favor_changes": {"住持": 20}},
                        moral_alignment=MoralAlignment.GOOD,
                    ),
                ],
            ),
            "step3": QuestStep(
                step_id="step3",
                title="秘宝真相",
                description="密室中的真相出乎你的意料...",
                scene_narrative="密室中并无金银财宝，只有一本泛黄的古籍和一盏长明灯。古籍上记载着...",
                choices=[
                    QuestChoice(
                        choice_id="A",
                        text="带走古籍",
                        description="你将古籍收入怀中，这或许是改变命运的契机。",
                        next_step_id="",
                        consequences={"tags_add": ["清风秘籍"], "gold": 50},
                        moral_alignment=MoralAlignment.NEUTRAL,
                        moral_score=-10,
                    ),
                    QuestChoice(
                        choice_id="B",
                        text="留给寺庙",
                        description="这等秘籍应由寺庙保管，你不便带走。",
                        next_step_id="",
                        consequences={"reputation": 30, "favor_changes": {"住持": 30}},
                        moral_alignment=MoralAlignment.GOOD,
                        moral_score=20,
                    ),
                    QuestChoice(
                        choice_id="C",
                        text="毁掉古籍",
                        description="此等秘籍若落入歹人之手后果不堪设想，不如毁去。",
                        next_step_id="",
                        consequences={"tags_add": ["决断之人"], "reputation": -10},
                        moral_alignment=MoralAlignment.NEUTRAL,
                        moral_score=0,
                    ),
                ],
            ),
        },
    ),

    QuestChain(
        chain_id="merchant_crisis",
        name="商路危机",
        description="城中商队屡遭劫匪袭击，商会会长请你出手相助。",
        category="side",
        start_step_id="step1",
        prerequisites=["tag:穿越者", "gold:50"],
        moral_impact=True,
        steps={
            "step1": QuestStep(
                step_id="step1",
                title="商会求助",
                description="商会会长找到你，希望你能帮忙解决商路劫匪问题。",
                scene_narrative="商会会长满面愁容：'近来商路不宁，我们的货物被劫了三次，损失惨重...'",
                choices=[
                    QuestChoice(
                        choice_id="A",
                        text="义不容辞，免费帮忙",
                        description="你拍着胸脯答应，分文不取。",
                        next_step_id="step2",
                        consequences={"favor_changes": {"商会会长": 20}, "reputation": 10},
                        moral_alignment=MoralAlignment.GOOD,
                        moral_score=20,
                    ),
                    QuestChoice(
                        choice_id="B",
                        text="可以帮忙，但要报酬",
                        description="你表示可以帮忙，但需要合理的报酬。",
                        next_step_id="step2",
                        consequences={"tags_add": ["商路契约"]},
                        moral_alignment=MoralAlignment.NEUTRAL,
                    ),
                    QuestChoice(
                        choice_id="C",
                        text="这不关我的事",
                        description="你婉拒了商会会长的请求。",
                        next_step_id="",
                        consequences={"favor_changes": {"商会会长": -15}},
                        moral_alignment=MoralAlignment.NEUTRAL,
                        moral_score=-5,
                    ),
                ],
            ),
            "step2": QuestStep(
                step_id="step2",
                title="剿匪还是招安",
                description="你查明了劫匪的藏身之处，现在需要决定如何处理。",
                scene_narrative="经过数日打探，你发现劫匪藏在城外的黑风寨中。令人意外的是，这些人原本都是附近的农民...",
                choices=[
                    QuestChoice(
                        choice_id="A",
                        text="武力剿灭",
                        description="你决定以雷霆手段剿灭劫匪。",
                        next_step_id="step3",
                        consequences={"tags_add": ["剿匪英雄"], "reputation": 20, "gold": 100},
                        moral_alignment=MoralAlignment.GOOD,
                        moral_score=10,
                    ),
                    QuestChoice(
                        choice_id="B",
                        text="招安收编",
                        description="你尝试说服劫匪投降，给他们一条生路。",
                        next_step_id="step3",
                        consequences={"tags_add": ["仁义之名"], "favor_changes": {"劫匪首领": 30}},
                        moral_alignment=MoralAlignment.GOOD,
                        moral_score=30,
                    ),
                    QuestChoice(
                        choice_id="C",
                        text="与劫匪合作分赃",
                        description="你暗中与劫匪达成协议，一起分赃。",
                        next_step_id="step3",
                        consequences={"gold": 300, "tags_add": ["暗中勾结"]},
                        moral_alignment=MoralAlignment.EVIL,
                        moral_score=-40,
                    ),
                ],
            ),
            "step3": QuestStep(
                step_id="step3",
                title="尘埃落定",
                description="商路危机解决了，但后续影响才刚刚开始...",
                choices=[
                    QuestChoice(
                        choice_id="A",
                        text="接受商会感谢",
                        description="你接受了商会的感谢和报酬。",
                        next_step_id="",
                        consequences={"gold": 50, "reputation": 5},
                    ),
                ],
            ),
        },
        long_term_effects=[
            {
                "delay_days": 7,
                "effects": {"tags_add": ["商路已通"]},
                "narrative": "商路恢复畅通，城中物价逐渐回落。",
            },
            {
                "delay_days": 30,
                "effects": {},
                "narrative": "你剿匪的事迹在民间传开，成为了人们茶余饭后的谈资。",
            },
        ],
    ),
]
