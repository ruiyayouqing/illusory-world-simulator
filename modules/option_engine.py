from __future__ import annotations
from .schemas import PlayerState, WorldState
from .llm.base_llm import BaseLLM


OPTION_QUALITY_PROMPT = """你是游戏选项设计大师。根据当前场景和玩家状态，生成3个有深度、有差异化的选项。

【当前场景】
{scene_description}

【玩家完整状态】
姓名: {player_name}，{player_age}岁
身份: {player_position}
位置: {location}
标签: {tags}
属性: 力量{strength} 敏捷{agility} 智力{intelligence} 幸运{luck}
生命: {health}/{max_health} 体力: {energy}/{max_energy}
金币: {gold} 声望: {reputation}
状态: {status_effects}
关系: {relations}

【世界状态】
第{day}天 {time} | {season} | {weather}
危机等级: {crisis_level}/10

【选项设计原则】
1. 选项A（稳健）：安全稳妥，适合当前状态，低风险
2. 选项B（王道）：中规中矩，有一定挑战，中等风险
3. 选项C（骚操作）：出人意料，可能是搞笑/作死/神来之笔，高风险高回报

【属性依赖】
- 选项效果应与玩家属性相关（如力量高则战斗选项成功率高）
- 标签影响选项类型（"穿越者"可出现现代知识选项，"谨慎"可出现保守选项）
- 年龄影响选项（年长者有"人生经验"选项，年轻者有"热血冲动"选项）

【网文风格】
- 选项C可以是玩梗、骚操作、打破常规
- 选项要具体可执行，不要模糊描述
- 可以有情感选项（如"保护某人"、"牺牲自己"）

【输出JSON格式】
{{
    "options": [
        {{
            "id": "A",
            "text": "具体的选项描述",
            "type": "action/move/talk/search/rest/custom/fight/trade",
            "risk": "low/medium/high",
            "needs_dice": false,
            "dice_stat": "",
            "dice_difficulty": 0,
            "stat_bonus": "strength/agility/intelligence/luck/none",
            "bonus_value": 0,
            "hint": "选项效果提示",
            "tags_required": [],
            "age_min": 0,
            "age_max": 999
        }}
    ]
}}

只输出JSON。"""


class OptionEngine:
    def __init__(self, llm: BaseLLM):
        self.llm = llm

    def generate_options(self, scene_description: str, player: PlayerState,
                         world_state: WorldState = None) -> list[dict]:
        relations_text = ", ".join([
            f"{k}(好感{v.favor})" for k, v in player.relations.items()
        ]) or "无"

        ws = world_state
        # [Bug] 使用 location_name（如"汴京城"）而非 location code（如"bianjing"）
        loc_name = player.location
        if ws and hasattr(ws, 'locations') and player.location in ws.locations:
            loc_obj = ws.locations[player.location]
            if isinstance(loc_obj, dict):
                loc_name = loc_obj.get('location_name') or loc_obj.get('name') or player.location
            elif hasattr(loc_obj, 'location_name'):
                loc_name = loc_obj.location_name or player.location
            elif hasattr(loc_obj, 'name'):
                loc_name = loc_obj.name or player.location
        prompt = OPTION_QUALITY_PROMPT.format(
            scene_description=scene_description,
            player_name=player.name,
            player_age=player.age,
            player_position=player.social.position,
            location=loc_name,
            tags=", ".join(player.tags),
            strength=player.stats.strength,
            agility=player.stats.agility,
            intelligence=player.stats.intelligence,
            luck=player.stats.luck,
            health=player.stats.health,
            max_health=player.stats.max_health,
            energy=player.stats.energy,
            max_energy=player.stats.max_energy,
            gold=player.social.gold,
            reputation=player.social.reputation,
            status_effects=", ".join(player.status_effects) if player.status_effects else "正常",
            relations=relations_text,
            day=ws.current_day if ws else 1,
            time=ws.current_time if ws else "上午",
            season=ws.season if ws else "春季",
            weather=ws.weather if ws else "晴朗",
            crisis_level=ws.crisis_level if ws else 0,
        )
        response = self.llm.chat_json(prompt, temperature=0.85)
        options = response.get("options", [])

        filtered = []
        for opt in options:
            if self._check_requirements(opt, player):
                filtered.append(opt)

        if len(filtered) < 3:
            filtered.extend(self._fallback_options(player)[len(filtered):])

        return filtered[:3]

    def _check_requirements(self, option: dict, player: PlayerState) -> bool:
        tags_required = option.get("tags_required", [])
        if tags_required:
            if not any(t in player.tags for t in tags_required):
                return False

        age_min = option.get("age_min", 0)
        age_max = option.get("age_max", 999)
        if not (age_min <= player.age <= age_max):
            return False

        return True

    def apply_stat_bonus(self, option: dict, player: PlayerState) -> int:
        stat = option.get("stat_bonus", "none")
        bonus = option.get("bonus_value", 0)
        if stat == "none":
            return 0
        value = getattr(player.stats, stat, 0)
        return bonus + (value // 5)

    def _fallback_options(self, player: PlayerState) -> list[dict]:
        loc = player.location if player.location else "附近"
        # 根据玩家属性生成更有针对性的回退选项
        if player.stats.intelligence >= 20:
            c_text = "仔细分析当前局势，找出关键信息和突破口"
            c_stat = "intelligence"
        elif player.stats.strength >= 25:
            c_text = "用果断的行动打破僵局，展示你的决心"
            c_stat = "strength"
        else:
            c_text = "尝试用出人意料的方式推进局面"
            c_stat = "luck"
        return [
            {"id": "A", "text": f"仔细观察{loc}周围的情况，搜集信息",
             "type": "search", "risk": "low", "needs_dice": False,
             "stat_bonus": "intelligence", "bonus_value": 0,
             "hint": "了解当前处境，可能发现线索"},
            {"id": "B", "text": "理清思绪，根据已有信息制定应对策略",
             "type": "action", "risk": "medium", "needs_dice": False,
             "stat_bonus": "intelligence", "bonus_value": 0,
             "hint": "冷静思考可能找到更好的方案"},
            {"id": "C", "text": c_text, "type": "custom",
             "risk": "high", "needs_dice": True, "dice_stat": c_stat,
             "dice_difficulty": 12, "stat_bonus": c_stat,
             "bonus_value": 2, "hint": "高风险高回报"},
        ]
