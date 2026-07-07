"""
[v9] NPC社会网络 — 信息传播、关系网络、社会影响力

设计原则：
  - NPC之间有关系（上下级、朋友、敌人、亲人等）
  - 信息在NPC网络中自然传播
  - 社会影响力决定NPC的话语权和行动能力
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional
from collections import deque

logger = logging.getLogger("chronoverse.social_network")


@dataclass
class SocialLink:
    """NPC之间的社会关系"""
    source_id: str
    target_id: str
    relation_type: str  # 上级、下属、朋友、敌人、亲人、恋人、同门、邻居
    strength: int = 50  # 关系强度 0-100
    hidden: bool = False  # 是否是隐秘关系


@dataclass
class InformationPiece:
    """一条信息"""
    content: str
    source_id: str  # 信息来源NPC
    topic: str  # 信息主题（玩家身份、战斗、秘密等）
    day: int  # 信息产生的时间
    trust_level: int = 50  # 信息可信度
    spread_count: int = 0  # 已传播次数
    max_spread: int = 5  # 最大传播次数


class SocialNetwork:
    """NPC社会网络管理器"""

    def __init__(self):
        self.links: list[SocialLink] = []
        self.information_pool: list[InformationPiece] = []
        self.npc_influence: dict[str, float] = {}  # npc_id -> 影响力 0-100
        self._initialized = False

    def initialize(self, npc_states: dict, world_state=None):
        """从NPC状态初始化社会网络"""
        if self._initialized:
            return

        npc_ids = list(npc_states.keys())
        if len(npc_ids) < 2:
            self._initialized = True
            return

        # 根据NPC属性自动建立关系
        for i, nid1 in enumerate(npc_ids):
            npc1 = npc_states[nid1]
            for nid2 in npc_ids[i+1:]:
                npc2 = npc_states[nid2]

                # 同一位置 → 邻居关系
                if (npc1.current_location and npc1.current_location == npc2.current_location):
                    self.add_link(nid1, nid2, "邻居", strength=30)

                # 同一职业 → 同行关系
                if (npc1.role and npc2.role and npc1.role == npc2.role):
                    self.add_link(nid1, nid2, "同行", strength=20)

                # 性格相似 → 可能成为朋友
                if self._personality_compatible(npc1.personality, npc2.personality):
                    self.add_link(nid1, nid2, "潜在朋友", strength=15)

        # 计算影响力
        self._calculate_influence(npc_states)
        self._initialized = True
        logger.info("Social network initialized: %d links, %d NPCs",
                     len(self.links), len(npc_ids))

    def add_link(self, source_id: str, target_id: str,
                 relation_type: str, strength: int = 50, hidden: bool = False):
        """添加或更新一条社会关系"""
        for link in self.links:
            if link.source_id == source_id and link.target_id == target_id:
                link.relation_type = relation_type
                link.strength = strength
                return
        self.links.append(SocialLink(
            source_id=source_id, target_id=target_id,
            relation_type=relation_type, strength=strength, hidden=hidden,
        ))

    def remove_link(self, source_id: str, target_id: str):
        """删除一条社会关系"""
        self.links = [
            l for l in self.links
            if not (l.source_id == source_id and l.target_id == target_id)
        ]

    def get_npc_links(self, npc_id: str) -> list[SocialLink]:
        """获取某个NPC的所有社会关系"""
        return [
            l for l in self.links
            if l.source_id == npc_id or l.target_id == npc_id
        ]

    def get_npc_neighbors(self, npc_id: str) -> list[str]:
        """获取某个NPC的所有社会关系对象"""
        neighbors = set()
        for link in self.get_npc_links(npc_id):
            if link.source_id == npc_id:
                neighbors.add(link.target_id)
            else:
                neighbors.add(link.source_id)
        return list(neighbors)

    def get_relation_strength(self, npc1_id: str, npc2_id: str) -> int:
        """获取两个NPC之间的关系强度"""
        for link in self.links:
            if (link.source_id == npc1_id and link.target_id == npc2_id):
                return link.strength
            if (link.source_id == npc2_id and link.target_id == npc1_id):
                return link.strength
        return 0

    def spread_information(self, info: InformationPiece, npc_states: dict) -> list[str]:
        """
        信息在网络中传播
        返回：被通知的NPC ID列表
        """
        notified = []
        queue = deque([info.source_id])
        visited = {info.source_id}

        while queue:
            current_id = queue.popleft()
            neighbors = self.get_npc_neighbors(current_id)

            for neighbor_id in neighbors:
                if neighbor_id in visited:
                    continue
                if info.spread_count >= info.max_spread:
                    break

                # 传播概率：关系越强越容易传播
                strength = self.get_relation_strength(current_id, neighbor_id)
                if strength < 10:
                    continue  # 关系太弱，不会传播

                # 可信度随传播距离衰减
                info.spread_count += 1
                notified.append(neighbor_id)
                visited.add(neighbor_id)
                queue.append(neighbor_id)

                logger.info("Information spread: %s -> %s (topic=%s, strength=%d)",
                           current_id, neighbor_id, info.topic, strength)

        return notified

    def add_information(self, content: str, source_id: str, topic: str,
                       day: int, trust_level: int = 50):
        """添加一条新信息到信息池"""
        info = InformationPiece(
            content=content, source_id=source_id,
            topic=topic, day=day, trust_level=trust_level,
        )
        self.information_pool.append(info)

        # 信息传播（使用已初始化的 links 网络）
        notified = self.spread_information(info, {})
        if notified:
            logger.info("New information about '%s' reached %d NPCs", topic, len(notified))

    def get_npc_knowledge(self, npc_id: str, topic: str = None) -> list[InformationPiece]:
        """获取某个NPC已知的信息"""
        known = []
        for info in self.information_pool:
            # 信息来源是自己
            if info.source_id == npc_id:
                if topic is None or info.topic == topic:
                    known.append(info)
                    continue
            # 信息传播到了自己
            neighbors = self.get_npc_neighbors(info.source_id)
            if npc_id in neighbors:
                strength = self.get_relation_strength(info.source_id, npc_id)
                if strength >= 20:
                    if topic is None or info.topic == topic:
                        known.append(info)
        return known

    def get_network_summary(self, npc_states: dict) -> str:
        """生成社会网络摘要，注入到LLM prompt中"""
        parts = []

        # 按类型分组关系
        relation_groups: dict[str, list[str]] = {}
        for link in self.links:
            key = link.relation_type
            if key not in relation_groups:
                relation_groups[key] = []
            src_name = npc_states.get(link.source_id, None)
            tgt_name = npc_states.get(link.target_id, None)
            if src_name and tgt_name:
                relation_groups[key].append(
                    f"{src_name.name}-{tgt_name.name}(强度{link.strength})"
                )

        for rel_type, pairs in relation_groups.items():
            parts.append(f"【{rel_type}】" + "、".join(pairs[:5]))

        return "\n".join(parts) if parts else ""

    def _personality_compatible(self, p1: str, p2: str) -> bool:
        """简单判断两个性格是否兼容"""
        if not p1 or not p2:
            return False
        positive = ["温和", "善良", "开朗", "热情", "正直", "豪爽"]
        negative = ["阴险", "狡诈", "冷酷", "残忍", "孤僻", "暴躁"]
        p1_positive = any(w in p1 for w in positive)
        p2_positive = any(w in p2 for w in positive)
        p1_negative = any(w in p1 for w in negative)
        p2_negative = any(w in p2 for w in negative)
        return (p1_positive and p2_positive) or (p1_negative and p2_negative)

    def _calculate_influence(self, npc_states: dict):
        """计算每个NPC的社会影响力"""
        for npc_id, npc in npc_states.items():
            influence = 50.0  # 基础影响力

            # 职业影响力
            if npc.role:
                if any(w in npc.role for w in ["皇帝", "国王", "城主", "帮主", "掌门"]):
                    influence += 30
                elif any(w in npc.role for w in ["将军", "大臣", "长老", "护法"]):
                    influence += 20
                elif any(w in npc.role for w in ["官员", "管事", "执事"]):
                    influence += 10

            # 关系网络影响力
            neighbors = self.get_npc_links(npc_id)
            influence += len(neighbors) * 2

            # 好感度影响力
            if npc.relation_to_player:
                favor = npc.relation_to_player.favor
                if favor >= 80:
                    influence += 10
                elif favor <= 20:
                    influence -= 10

            self.npc_influence[npc_id] = max(0, min(100, influence))

    def get_most_influential_npcs(self, npc_states: dict, top_n: int = 5) -> list[str]:
        """获取最有影响力的NPC"""
        if not self.npc_influence:
            self._calculate_influence(npc_states)
        sorted_npcs = sorted(
            self.npc_influence.items(), key=lambda x: x[1], reverse=True
        )
        return [npc_id for npc_id, _ in sorted_npcs[:top_n]]

    def process_turn(self, npc_states: dict, world_state, day: int):
        """每回合处理：更新影响力、清理过期信息"""
        self._calculate_influence(npc_states)

        # 清理过期信息（超过30天）
        self.information_pool = [
            info for info in self.information_pool
            if day - info.day < 30
        ]

    def get_social_context_for_npc(self, npc_id: str, npc_states: dict) -> str:
        """获取某个NPC的社会关系上下文，注入到该NPC的prompt中"""
        npc = npc_states.get(npc_id)
        if not npc:
            return ""

        links = self.get_npc_links(npc_id)
        if not links:
            return ""

        parts = [f"【{npc.name}的社会关系】"]
        for link in links:
            other_id = link.target_id if link.source_id == npc_id else link.source_id
            other = npc_states.get(other_id)
            if other:
                parts.append(f"- {other.name}: {link.relation_type}(强度{link.strength})")

        return "\n".join(parts)
