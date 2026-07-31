"""
[v1.5 第二期] 血缘传承系统

设计要点：
  1. 仅在 NPC 自然死亡（老死/病死）时触发传承
  2. NPCState 新增 family_id/parents/spouse/children/siblings 字段
  3. 继承顺序：长子 → 长女 → 配偶 → 徒弟 → 家族其他成员 → 无继承人
  4. 继承内容：personal_reputation ×0.8、faction_standing 全量、stats ×0.6
  5. 无继承人 → 触发 legacy 动机 → NPC 临终前主动找玩家托付（由 WorldTick 处理）

Family 数据结构（仅内存归档，不持久化）：
  {
    "family_id": "fam_xxx",
    "name": "张家",
    "founding_day": 1,
    "head": "npc_a",         # 族长 NPC id
    "members": ["npc_a", "npc_b", "npc_c"],
    "reputation": 50,         # 家族声望
    "traditions": ["尚武", "崇文"]
  }
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import NPCState, WorldState

logger = logging.getLogger("chronoverse.family")


class FamilyRegistry:
    """家族注册表（仅内存，重启清空）

    每个世界共享一个 FamilyRegistry 实例。
    """

    def __init__(self):
        self.families: dict[str, dict] = {}  # family_id → Family dict
        self._npc_to_family: dict[str, str] = {}  # npc_id → family_id（索引）

    def create_family(self, name: str, founding_day: int, head_npc_id: str,
                      traditions: list[str] = None) -> str:
        """创建新家族

        Returns:
            family_id
        """
        import uuid
        family_id = "fam_" + uuid.uuid4().hex[:10]
        self.families[family_id] = {
            "family_id": family_id,
            "name": name,
            "founding_day": founding_day,
            "head": head_npc_id,
            "members": [head_npc_id],
            "reputation": 50,
            "traditions": traditions or [],
        }
        self._npc_to_family[head_npc_id] = family_id
        return family_id

    def add_member(self, family_id: str, npc_id: str) -> bool:
        """添加家族成员"""
        fam = self.families.get(family_id)
        if not fam:
            return False
        if npc_id not in fam["members"]:
            fam["members"].append(npc_id)
        self._npc_to_family[npc_id] = family_id
        return True

    def remove_member(self, npc_id: str) -> None:
        """移除家族成员（死亡时调用）"""
        family_id = self._npc_to_family.pop(npc_id, None)
        if not family_id:
            return
        fam = self.families.get(family_id)
        if not fam:
            return
        if npc_id in fam["members"]:
            fam["members"].remove(npc_id)
        # 若族长离开，需要选举新族长
        if fam["head"] == npc_id:
            if fam["members"]:
                fam["head"] = fam["members"][0]
            else:
                # 家族消亡
                del self.families[family_id]
                logger.info("Family %s (%s) extinguished", family_id, fam["name"])

    def get_family_of(self, npc_id: str) -> dict | None:
        """获取 NPC 所属家族"""
        family_id = self._npc_to_family.get(npc_id)
        if not family_id:
            return None
        return self.families.get(family_id)

    def get_family_members(self, npc_id: str, exclude_self: bool = True,
                           exclude_dead: set = None) -> list[str]:
        """获取 NPC 的家族成员列表

        Args:
            npc_id: 目标 NPC
            exclude_self: 是否排除自己
            exclude_dead: 排除的 NPC id 集合（已死亡的）
        """
        fam = self.get_family_of(npc_id)
        if not fam:
            return []
        members = list(fam["members"])
        if exclude_self and npc_id in members:
            members.remove(npc_id)
        if exclude_dead:
            members = [m for m in members if m not in exclude_dead]
        return members

    def update_reputation(self, family_id: str, delta: int) -> None:
        """调整家族声望"""
        fam = self.families.get(family_id)
        if not fam:
            return
        fam["reputation"] = max(-100, min(100, fam["reputation"] + delta))


# ===== 传承逻辑 =====

class InheritanceService:
    """NPC 自然死亡时的传承服务"""

    def __init__(self, family_registry: FamilyRegistry):
        self.family_registry = family_registry

    def find_heir(self, deceased: "NPCState", all_npcs: dict[str, "NPCState"],
                  dead_npcs: set = None) -> "NPCState | None":
        """寻找继承人

        继承顺序：
          1. 长子（children 中第一个男性 tag "儿子"）
          2. 长女（children 中第一个女性 tag "女儿"）
          3. 配偶（spouse）
          4. 徒弟（siblings 含 "师弟/师妹/徒弟" tag 的 NPC，或同门 NPC）
          5. 家族其他成员
          6. 无继承人 → 返回 None
        """
        dead_npcs = dead_npcs or set()
        deceased_id = deceased.agent_id

        # 1. 长子
        for child_id in (deceased.children or []):
            child = all_npcs.get(child_id)
            if not child or child_id in dead_npcs:
                continue
            if any(t in (child.tags or []) for t in ("儿子", "长子")):
                return child

        # 2. 长女
        for child_id in (deceased.children or []):
            child = all_npcs.get(child_id)
            if not child or child_id in dead_npcs:
                continue
            if any(t in (child.tags or []) for t in ("女儿", "长女")):
                return child

        # 3. 配偶
        if deceased.spouse:
            spouse = all_npcs.get(deceased.spouse)
            if spouse and deceased.spouse not in dead_npcs:
                return spouse

        # 4. 徒弟/同门：查 siblings（同门）或家族成员中带"徒弟"/"弟子"标签的
        for sib_id in (deceased.siblings or []):
            sib = all_npcs.get(sib_id)
            if not sib or sib_id in dead_npcs:
                continue
            if any(t in (sib.tags or []) for t in ("徒弟", "弟子", "师弟", "师妹")):
                return sib

        # 5. 家族其他成员
        family_members = self.family_registry.get_family_members(
            deceased_id, exclude_self=True, exclude_dead=dead_npcs,
        )
        for member_id in family_members:
            member = all_npcs.get(member_id)
            if member and member_id not in dead_npcs:
                return member

        # 6. 无继承人
        return None

    def inherit(self, deceased: "NPCState", heir: "NPCState",
                world_state: "WorldState") -> dict:
        """执行传承：把 deceased 的部分属性继承给 heir

        Returns:
            传承记录 dict
        """
        import copy

        # 1. personal_reputation × 0.8
        old_rep = getattr(deceased, "personal_reputation", 0)
        inherited_rep = int(old_rep * 0.8)
        heir.personal_reputation = getattr(heir, "personal_reputation", 0) + inherited_rep

        # 2. faction_standing 全量继承
        deceased_standing = getattr(deceased, "faction_standing", {}) or {}
        heir_standing = getattr(heir, "faction_standing", {}) or {}
        for faction_id, standing in deceased_standing.items():
            # 取较大值（更友好的立场优先）
            heir_standing[faction_id] = max(heir_standing.get(faction_id, 0), standing)
        heir.faction_standing = heir_standing

        # 3. stats × 0.6（仅正面属性：strength/agility/intelligence/luck）
        stat_factors = {
            "strength": 0.6, "agility": 0.6,
            "intelligence": 0.6, "luck": 0.6,
        }
        inherited_stats = {}
        for stat_name, factor in stat_factors.items():
            old_val = getattr(deceased.stats, stat_name, 0)
            inherited = int(old_val * factor)
            if inherited > 0:
                current_val = getattr(heir.stats, stat_name, 0)
                # 继承人属性上限 20（避免无限滚雪球）
                new_val = min(20, current_val + inherited)
                setattr(heir.stats, stat_name, new_val)
                inherited_stats[stat_name] = inherited

        # 4. 家族关系继承：heir 继承 deceased 的 family_id（如果 heir 没有的话）
        if not getattr(heir, "family_id", None) and getattr(deceased, "family_id", None):
            heir.family_id = deceased.family_id
            self.family_registry.add_member(deceased.family_id, heir.agent_id)

        # 5. 家族声望调整（ deceased 的名誉贡献给家族）
        if deceased.family_id:
            self.family_registry.update_reputation(
                deceased.family_id, int(old_rep * 0.2),
            )

        # 6. 记录传承
        record = {
            "deceased_id": deceased.agent_id,
            "deceased_name": deceased.name,
            "heir_id": heir.agent_id,
            "heir_name": heir.name,
            "day": world_state.current_day,
            "inherited_reputation": inherited_rep,
            "inherited_stats": inherited_stats,
            "inherited_faction_standing": dict(deceased_standing),
        }
        logger.info(
            "Inheritance: %s → %s (rep=%d, stats=%s, day=%d)",
            deceased.name, heir.name, inherited_rep,
            inherited_stats, world_state.current_day,
        )
        return record

    def try_inherit_on_natural_death(self, deceased: "NPCState",
                                      all_npcs: dict[str, "NPCState"],
                                      world_state: "WorldState",
                                      dead_npcs: set = None) -> dict | None:
        """自然死亡时尝试传承

        Returns:
            传承记录 dict，或 None（无继承人）
        """
        heir = self.find_heir(deceased, all_npcs, dead_npcs)
        if not heir:
            logger.info(
                "No heir found for %s (age=%d), legacy motivation may trigger",
                deceased.name, deceased.age,
            )
            return None
        return self.inherit(deceased, heir, world_state)


# ===== 全局单例 =====

_global_family_registry: FamilyRegistry | None = None
_global_inheritance_service: InheritanceService | None = None


def get_family_registry() -> FamilyRegistry:
    """获取全局 FamilyRegistry 单例"""
    global _global_family_registry
    if _global_family_registry is None:
        _global_family_registry = FamilyRegistry()
    return _global_family_registry


def get_inheritance_service() -> InheritanceService:
    """获取全局 InheritanceService 单例"""
    global _global_inheritance_service
    if _global_inheritance_service is None:
        _global_inheritance_service = InheritanceService(get_family_registry())
    return _global_inheritance_service


def reset_family_registry() -> None:
    """重置全局单例（测试用）"""
    global _global_family_registry, _global_inheritance_service
    _global_family_registry = None
    _global_inheritance_service = None
