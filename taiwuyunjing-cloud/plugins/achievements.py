"""
[v9] 示例插件：成就/奖杯系统

功能：
- 追踪玩家里程碑
- 自动检测成就完成条件
- 成就通知
- 成就统计
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from plugins import PluginBase

logger = logging.getLogger("chronoverse.plugin.achievements")


@dataclass
class Achievement:
    """成就定义"""
    id: str
    name: str
    description: str
    category: str = "general"  # general/combat/social/explore/economy
    icon: str = "🏆"
    condition_type: str = ""  # tag/day/gold/reputation/kill_count/visit_count
    condition_value: int = 0
    condition_tag: str = ""
    unlocked: bool = False
    unlock_day: int = 0


# 预置成就列表
BUILTIN_ACHIEVEMENTS: list[Achievement] = [
    # 通用成就
    Achievement(id="first_day", name="初来乍到", description="度过第一天",
                icon="🌅", condition_type="day", condition_value=1),
    Achievement(id="survive_month", name="月余光阴", description="生存超过30天",
                icon="📅", condition_type="day", condition_value=30),
    Achievement(id="survive_year", name="岁月如梭", description="生存超过365天",
                icon="📆", condition_type="day", condition_value=365),

    # 经济成就
    Achievement(id="first_gold", name="第一桶金", description="获得100金币",
                icon="💰", condition_type="gold", condition_value=100,
                category="economy"),
    Achievement(id="rich", name="富甲一方", description="拥有1000金币",
                icon="💎", condition_type="gold", condition_value=1000,
                category="economy"),
    Achievement(id="tycoon", name="一方巨贾", description="拥有10000金币",
                icon="👑", condition_type="gold", condition_value=10000,
                category="economy"),

    # 声望成就
    Achievement(id="known", name="小有名气", description="声望达到50",
                icon="⭐", condition_type="reputation", condition_value=50,
                category="social"),
    Achievement(id="famous", name="名扬天下", description="声望达到500",
                icon="🌟", condition_type="reputation", condition_value=500,
                category="social"),
    Achievement(id="legend", name="传说之人", description="声望达到2000",
                icon="✨", condition_type="reputation", condition_value=2000,
                category="social"),

    # 标签成就
    Achievement(id="traverser", name="穿越者", description="获得穿越者标签",
                icon="🌀", condition_type="tag", condition_tag="穿越者",
                category="general"),
    Achievement(id="warrior", name="武者之路", description="获得武者相关标签",
                icon="⚔️", condition_type="tag", condition_tag="武",
                category="combat"),
    Achievement(id="mage", name="法师之道", description="获得法师相关标签",
                icon="🔮", condition_type="tag", condition_tag="法师",
                category="combat"),

    # 探索成就
    Achievement(id="explorer", name="探索者", description="访问5个不同地点",
                icon="🗺️", condition_type="visit_count", condition_value=5,
                category="explore"),
    Achievement(id="wanderer", name="流浪者", description="访问10个不同地点",
                icon="🧭", condition_type="visit_count", condition_value=10,
                category="explore"),
]


class AchievementsPlugin(PluginBase):
    name = "achievements"
    version = "1.0.0"
    description = "成就/奖杯系统：追踪里程碑，自动检测完成条件"
    author = "太虚幻境"

    def on_load(self, engine):
        super().on_load(engine)
        self.achievements: dict[str, Achievement] = {}
        self.visited_locations: set[str] = set()
        self.kill_count: int = 0
        # [P1-4] 初始化新成就列表，供 on_turn_end 写入
        self.new_achievements: list[dict] = []

        # 加载预置成就
        for ach in BUILTIN_ACHIEVEMENTS:
            self.achievements[ach.id] = Achievement(
                id=ach.id, name=ach.name, description=ach.description,
                category=ach.category, icon=ach.icon,
                condition_type=ach.condition_type,
                condition_value=ach.condition_value,
                condition_tag=ach.condition_tag,
            )

        self.hooks["on_turn_end"] = self.on_turn_end
        self.hooks["on_world_event"] = self.on_world_event
        self.hooks["on_player_death"] = self.on_player_death
        logger.info("成就系统插件已加载，共%d个成就", len(self.achievements))

    def on_turn_end(self, **kwargs):
        """回合结束时检查成就"""
        player_state = kwargs.get("player_state")
        world_state = kwargs.get("world_state")
        if not player_state or not world_state:
            return

        newly_unlocked = []

        for ach in self.achievements.values():
            if ach.unlocked:
                continue

            unlocked = False

            if ach.condition_type == "day":
                unlocked = world_state.current_day >= ach.condition_value
            elif ach.condition_type == "gold":
                unlocked = player_state.social.gold >= ach.condition_value
            elif ach.condition_type == "reputation":
                unlocked = player_state.social.reputation >= ach.condition_value
            elif ach.condition_type == "tag":
                unlocked = any(ach.condition_tag in tag for tag in player_state.tags)
            elif ach.condition_type == "visit_count":
                unlocked = len(self.visited_locations) >= ach.condition_value

            if unlocked:
                ach.unlocked = True
                ach.unlock_day = world_state.current_day
                newly_unlocked.append(ach)
                logger.info("🏆 成就解锁: %s - %s", ach.icon, ach.name)

        # 记录访问的地点
        if player_state.location:
            self.visited_locations.add(player_state.location)

        # [P1-4] 修复 'data' 未定义 bug：将新解锁的成就存到实例属性
        if newly_unlocked:
            self.new_achievements = [
                {"icon": a.icon, "name": a.name, "description": a.description}
                for a in newly_unlocked
            ]

    def on_world_event(self, **kwargs):
        """世界事件时检查成就"""
        # 可以在此添加特殊事件相关成就
        pass

    def on_player_death(self, **kwargs):
        """玩家死亡时记录"""
        # 可以添加死亡相关成就
        pass

    def get_all_achievements(self) -> list[dict]:
        """获取所有成就状态"""
        return [
            {
                "id": ach.id,
                "name": ach.name,
                "description": ach.description,
                "icon": ach.icon,
                "category": ach.category,
                "unlocked": ach.unlocked,
                "unlock_day": ach.unlock_day,
            }
            for ach in self.achievements.values()
        ]

    def get_unlocked_count(self) -> tuple[int, int]:
        """返回 (已解锁数, 总数)"""
        unlocked = sum(1 for a in self.achievements.values() if a.unlocked)
        return unlocked, len(self.achievements)

    def get_stats(self) -> dict:
        """返回成就统计"""
        unlocked, total = self.get_unlocked_count()
        by_category = {}
        for ach in self.achievements.values():
            cat = ach.category
            if cat not in by_category:
                by_category[cat] = {"total": 0, "unlocked": 0}
            by_category[cat]["total"] += 1
            if ach.unlocked:
                by_category[cat]["unlocked"] += 1

        return {
            "total": total,
            "unlocked": unlocked,
            "percentage": round(unlocked / total * 100, 1) if total > 0 else 0,
            "by_category": by_category,
            "visited_locations": len(self.visited_locations),
            "kill_count": self.kill_count,
        }


def register(engine, register_hook_fn):
    """插件注册函数"""
    plugin = AchievementsPlugin()
    plugin.on_load(engine)
    for hook_name, handler in plugin.hooks.items():
        register_hook_fn(hook_name, handler)
    return plugin
