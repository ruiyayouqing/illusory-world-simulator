"""
[v9] 示例插件：回合制战斗系统

功能：
- 回合制战斗：玩家vs敌人，轮流行动
- 技能系统：普攻/技能/防御/逃跑
- 伤害计算：基于属性+随机因子
- 战利品掉落
"""
from __future__ import annotations
import logging
import random
from dataclasses import dataclass, field
from plugins import PluginBase

logger = logging.getLogger("chronoverse.plugin.battle")


@dataclass
class Enemy:
    """敌人数据"""
    name: str
    health: int = 100
    max_health: int = 100
    attack: int = 10
    defense: int = 5
    agility: int = 5
    loot_gold: int = 0
    loot_items: list[str] = field(default_factory=list)
    loot_exp: int = 0


# 预置敌人模板
ENEMY_TEMPLATES = {
    "哥布林": Enemy("哥布林", health=30, attack=5, defense=2, agility=3,
                     loot_gold=5, loot_exp=10),
    "野狼": Enemy("野狼", health=40, attack=8, defense=3, agility=7,
                   loot_gold=0, loot_exp=15),
    "山贼": Enemy("山贼", health=60, attack=12, defense=6, agility=5,
                   loot_gold=20, loot_items=["铁剑"], loot_exp=25),
    "妖兽": Enemy("妖兽", health=100, attack=18, defense=10, agility=8,
                   loot_gold=50, loot_items=["妖丹"], loot_exp=50),
}


class BattleState:
    """战斗状态"""
    def __init__(self, enemy: Enemy):
        self.enemy = enemy
        self.turn = 0
        self.player_hp = 0
        self.player_energy = 0
        self.log: list[str] = []
        self.is_active = True
        self.result: dict = {}


class BattleSystemPlugin(PluginBase):
    name = "battle_system"
    version = "1.0.0"
    description = "回合制战斗系统：支持技能、伤害计算、战利品掉落"
    author = "太虚幻境"

    def on_load(self, engine):
        super().on_load(engine)
        self.active_battles: dict[str, BattleState] = {}
        self.hooks["on_player_input"] = self.on_player_input
        logger.info("战斗系统插件已加载")

    def start_battle(self, player_state, enemy_name: str) -> dict:
        """开始战斗"""
        template = ENEMY_TEMPLATES.get(enemy_name)
        if not template:
            return {"error": f"未知敌人: {enemy_name}"}

        # 复制模板
        enemy = Enemy(
            name=template.name,
            health=template.health + random.randint(-5, 10),
            max_health=template.max_health,
            attack=template.attack + random.randint(-2, 3),
            defense=template.defense,
            agility=template.agility,
            loot_gold=template.loot_gold,
            loot_items=list(template.loot_items),
            loot_exp=template.loot_exp,
        )

        state = BattleState(enemy)
        state.player_hp = player_state.stats.health
        state.player_energy = player_state.stats.energy

        battle_id = f"battle_{id(state)}"
        self.active_battles[battle_id] = state

        state.log.append(f"⚔️ 战斗开始！{player_state.name} VS {enemy.name}")
        state.log.append(f"敌人状态: 生命{enemy.health} 攻击{enemy.attack} 防御{enemy.defense}")

        return {
            "battle_id": battle_id,
            "enemy": {"name": enemy.name, "health": enemy.health, "max_health": enemy.max_health},
            "log": state.log,
            "options": self._get_battle_options(),
        }

    def on_player_input(self, **kwargs):
        """处理战斗中的玩家输入"""
        player_input = kwargs.get("input", "")
        player_state = kwargs.get("player_state")

        # 检查是否有活跃战斗
        for battle_id, state in list(self.active_battles.items()):
            if state.is_active:
                result = self._process_action(battle_id, player_input, player_state)
                data["battle_result"] = result
                break

    def _process_action(self, battle_id: str, action: str, player_state) -> dict:
        """处理战斗行动"""
        state = self.active_battles.get(battle_id)
        if not state or not state.is_active:
            return {"error": "无活跃战斗"}

        state.turn += 1
        enemy = state.enemy

        # 玩家行动
        if "攻击" in action or "打" in action:
            damage = self._calc_damage(
                player_state.stats.strength + player_state.stats.agility // 2,
                enemy.defense
            )
            enemy.health -= damage
            state.log.append(f"第{state.turn}回合: 你攻击{enemy.name}，造成{damage}点伤害！")
        elif "防御" in action:
            state.log.append(f"第{state.turn}回合: 你摆出防御姿态。")
            # 防御时受到的伤害减半
        elif "逃跑" in action:
            escape_chance = 0.3 + (player_state.stats.agility - enemy.agility) * 0.05
            if random.random() < escape_chance:
                state.is_active = False
                state.result = {"outcome": "escaped"}
                state.log.append("你成功逃跑了！")
                return {"status": "escaped", "log": state.log}
            else:
                state.log.append("逃跑失败！")
        elif "技能" in action:
            if state.player_energy >= 20:
                state.player_energy -= 20
                damage = self._calc_damage(
                    player_state.stats.strength * 2 + player_state.stats.intelligence,
                    enemy.defense
                )
                enemy.health -= damage
                state.log.append(f"第{state.turn}回合: 你使用技能！造成{damage}点伤害！")
            else:
                state.log.append("体力不足，无法使用技能！")
        else:
            state.log.append(f"第{state.turn}回合: 你犹豫了一下...")

        # 检查敌人是否死亡
        if enemy.health <= 0:
            state.is_active = False
            state.result = {
                "outcome": "victory",
                "loot_gold": enemy.loot_gold,
                "loot_items": enemy.loot_items,
                "loot_exp": enemy.loot_exp,
            }
            state.log.append(f"🎉 {enemy.name}被击败！")
            if enemy.loot_gold:
                state.log.append(f"获得金币: {enemy.loot_gold}")
            if enemy.loot_items:
                state.log.append(f"获得物品: {', '.join(enemy.loot_items)}")
            return {"status": "victory", "result": state.result, "log": state.log}

        # 敌人行动
        enemy_damage = self._calc_damage(enemy.attack, player_state.stats.strength // 3)
        state.player_hp -= enemy_damage
        state.log.append(f"{enemy.name}攻击你，造成{enemy_damage}点伤害！")

        # 检查玩家是否死亡
        if state.player_hp <= 0:
            state.is_active = False
            state.result = {"outcome": "defeat"}
            state.log.append("💀 你被击败了...")
            return {"status": "defeat", "log": state.log}

        return {
            "status": "continue",
            "player_hp": state.player_hp,
            "enemy_hp": enemy.health,
            "log": state.log,
            "options": self._get_battle_options(),
        }

    def _calc_damage(self, attack: int, defense: int) -> int:
        """计算伤害"""
        base = max(1, attack - defense)
        variance = random.uniform(0.8, 1.2)
        return max(1, int(base * variance))

    def _get_battle_options(self) -> list[dict]:
        """返回战斗选项"""
        return [
            {"id": "A", "text": "普通攻击", "type": "attack"},
            {"id": "B", "text": "使用技能（消耗20体力）", "type": "skill"},
            {"id": "C", "text": "防御", "type": "defend"},
            {"id": "D", "text": "逃跑", "type": "flee"},
        ]


def register(engine, register_hook_fn):
    """插件注册函数"""
    plugin = BattleSystemPlugin()
    plugin.on_load(engine)
    for hook_name, handler in plugin.hooks.items():
        register_hook_fn(hook_name, handler)
    return plugin
