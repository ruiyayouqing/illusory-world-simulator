"""
[v12] 时间轴引擎 — 小说大事件管理与状态快照。

核心功能：
1. 从小说分析结果构建时间轴（按章节/事件节点排列）
2. 为每个时间节点维护世界状态快照
3. 玩家选择时间节点进入游戏时，提供该点之前的完整记忆

状态快照不存原文，只存摘要。原文通过向量检索按需获取。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph_rag import GraphRAG, GraphEntity, GraphRelation

logger = logging.getLogger("chronoverse.timeline")


@dataclass
class CharacterSnapshot:
    """人物状态快照：某一时间点某角色的状态"""
    char_id: str
    name: str
    time_id: str = ""
    age: int = 0
    status: str = ""            # 身份/地位
    description: str = ""
    relationships: dict = field(default_factory=dict)  # {对方名: 关系描述}
    skills: list[str] = field(default_factory=list)
    inventory: list[str] = field(default_factory=list)
    location: str = ""
    goals: list[str] = field(default_factory=list)
    emotional_state: str = ""
    is_alive: bool = True

    def to_dict(self) -> dict:
        return {
            "char_id": self.char_id,
            "name": self.name,
            "time_id": self.time_id,
            "age": self.age,
            "status": self.status,
            "description": self.description,
            "relationships": self.relationships,
            "skills": self.skills,
            "inventory": self.inventory,
            "location": self.location,
            "goals": self.goals,
            "emotional_state": self.emotional_state,
            "is_alive": self.is_alive,
        }


@dataclass
class TimelineNode:
    """时间轴节点"""
    time_id: str                     # 时间节点ID
    chapter_index: int = 0           # 章节序号
    chapter_title: str = ""          # 章节标题
    story_time: str = ""              # 故事内时间描述
    event_description: str = ""       # 该时间点的事件描述
    importance: float = 0.0           # 事件重要性(0-10)
    world_state: dict = field(default_factory=dict)
    character_snapshots: dict = field(default_factory=dict)  # {char_name: CharacterSnapshot}
    accumulated_summaries: list[str] = field(default_factory=list)  # 该点之前的累积摘要
    original_future_events: list[str] = field(default_factory=list)  # 原著后续事件

    def to_dict(self) -> dict:
        return {
            "time_id": self.time_id,
            "chapter_index": self.chapter_index,
            "chapter_title": self.chapter_title,
            "story_time": self.story_time,
            "event_description": self.event_description,
            "importance": self.importance,
            "world_state": self.world_state,
            "character_snapshots": {
                k: v.to_dict() if isinstance(v, CharacterSnapshot) else v
                for k, v in self.character_snapshots.items()
            },
            "accumulated_summaries": self.accumulated_summaries,
            "original_future_events": self.original_future_events,
        }


class TimelineEngine:
    """
    [v12] 时间轴引擎。

    从小说分块+GraphRAG构建时间轴，管理状态快照。
    玩家选择时间节点进入游戏时，提供该点之前的完整记忆。
    """

    def __init__(self):
        self.nodes: dict[str, TimelineNode] = {}
        self._ordered_ids: list[str] = []  # 按时间排序的节点ID

    def build_from_analysis(self, chunks: list, graph_rag: "GraphRAG",
                             key_event_threshold: float = 6.0) -> int:
        """
        从分块结果和GraphRAG构建时间轴。

        chunks: SemanticChunker 输出的 TextChunk 列表
        graph_rag: 已导入小说的 GraphRAG 实例
        key_event_threshold: 重要事件阈值，高于此值才作为可选节点

        返回：创建的节点数
        """
        accumulated_summaries = []
        created = 0

        # 按块序号构建时间轴
        # 每个块代表一个时间点，但只将重要块作为可选节点
        for i, chunk in enumerate(chunks):
            chunk_text = chunk.text if hasattr(chunk, 'text') else str(chunk)
            chunk_title = chunk.chapter_title if hasattr(chunk, 'chapter_title') else ""

            # 累积摘要（取每块前200字作为摘要）
            summary = chunk_text[:200].replace('\n', ' ').strip()
            accumulated_summaries.append(f"[第{i+1}块/{chunk_title}] {summary}")

            # 为每个块创建时间节点
            time_id = f"t_{i:04d}"
            node = TimelineNode(
                time_id=time_id,
                chapter_index=i,
                chapter_title=chunk_title,
                story_time=f"章节{i+1}",
                event_description=summary,
                importance=self._estimate_importance(chunk_text, i, len(chunks)),
            )
            node.accumulated_summaries = list(accumulated_summaries)

            # 从GraphRAG提取该时间点的角色状态
            if graph_rag:
                node.character_snapshots = self._extract_character_states(
                    graph_rag, i
                )
                node.world_state = self._extract_world_state(graph_rag, i)

            self.nodes[time_id] = node
            self._ordered_ids.append(time_id)

            if node.importance >= key_event_threshold:
                created += 1

        # 为每个节点设置原著后续事件
        for i, tid in enumerate(self._ordered_ids):
            future = []
            for j in range(i + 1, len(self._ordered_ids)):
                future.append(self.nodes[self._ordered_ids[j]].event_description)
            self.nodes[tid].original_future_events = future

        logger.info("时间轴构建完成: %d 节点, %d 关键事件",
                    len(self.nodes), created)
        return created

    def _estimate_importance(self, text: str, index: int,
                               total: int) -> float:
        """
        估算块的重要性（0-10）。
        启发式：含战斗/死亡/转折关键词的块重要性高。
        """
        importance = 5.0  # 基础分

        # 关键事件关键词
        high_importance_keywords = [
            "死", "杀", "战", "败", "胜", "逃", "叛", "降",
            "婚", "娶", "离", "别", "逢", "遇", "救",
            "登基", "即位", "退位", "篡", "谋反",
            "突破", "觉醒", "顿悟", "进阶",
            "秘密", "真相", "发现", "揭晓",
        ]
        for kw in high_importance_keywords:
            if kw in text:
                importance += 0.5

        # 开头和结尾的块重要性略高
        if index == 0 or index == total - 1:
            importance += 1.0

        return min(10.0, importance)

    def _extract_character_states(self, graph_rag: "GraphRAG",
                                    chapter_index: int) -> dict:
        """从GraphRAG提取截至某章节的角色状态"""
        snapshots = {}
        for name, entity in graph_rag.entities.items():
            if entity.entity_type != "person":
                continue
            # 只取在该章节之前出现过的角色
            if entity.last_seen_turn > chapter_index:
                continue

            snapshot = CharacterSnapshot(
                char_id=name,
                name=name,
                time_id=f"t_{chapter_index:04d}",
                description=entity.description,
                status=entity.attributes.get("status", ""),
            )
            # 提取关系
            for rel in graph_rag.relations:
                if not rel.is_active:
                    continue
                if rel.source == name:
                    snapshot.relationships[rel.target] = (
                        f"{rel.relation_type}: {rel.description}"
                    )
                elif rel.target == name:
                    snapshot.relationships[rel.source] = (
                        f"{rel.relation_type}: {rel.description}"
                    )
            snapshots[name] = snapshot
        return snapshots

    def _extract_world_state(self, graph_rag: "GraphRAG",
                               chapter_index: int) -> dict:
        """提取截至某章节的世界状态"""
        world_state = {
            "active_entities": [],
            "active_relations": [],
            "locations": [],
        }
        for name, entity in graph_rag.entities.items():
            if entity.last_seen_turn > chapter_index:
                continue
            world_state["active_entities"].append({
                "name": name, "type": entity.entity_type,
            })
            if entity.entity_type == "place":
                world_state["locations"].append(name)

        for rel in graph_rag.get_active_relations():
            if rel.turn > chapter_index:
                continue
            world_state["active_relations"].append({
                "source": rel.source, "target": rel.target,
                "type": rel.relation_type,
            })
        return world_state

    def get_key_events(self, threshold: float = 6.0) -> list[dict]:
        """获取关键事件列表（供玩家选择）"""
        events = []
        for tid in self._ordered_ids:
            node = self.nodes[tid]
            if node.importance >= threshold:
                events.append({
                    "time_id": tid,
                    "chapter": node.chapter_title or f"第{node.chapter_index+1}章",
                    "description": node.event_description,
                    "importance": node.importance,
                })
        return events

    def get_snapshot(self, time_id: str) -> TimelineNode | None:
        """获取指定时间节点的完整快照"""
        return self.nodes.get(time_id)

    def get_memories_before(self, time_id: str) -> list[str]:
        """
        获取该时间点之前的所有累积记忆。
        进入游戏时注入 MemoryStore。
        """
        node = self.nodes.get(time_id)
        if not node:
            return []
        return node.accumulated_summaries

    def get_character_state_at(self, time_id: str,
                                 char_name: str) -> CharacterSnapshot | None:
        """获取某角色在某时间点的状态"""
        node = self.nodes.get(time_id)
        if not node:
            return None
        return node.character_snapshots.get(char_name)

    def get_all_character_states_at(self, time_id: str) -> dict:
        """获取某时间点所有角色状态"""
        node = self.nodes.get(time_id)
        if not node:
            return {}
        return node.character_snapshots

    def get_future_characters(self, time_id: str,
                               exclude_chars: list[str] = None) -> list[dict]:
        """[NovelRoleplay] 获取某时间点之后才出现的角色（未来角色）。
        用于小说扮演时全量注入潜在 NPC。

        逻辑：遍历所有时间点之后节点，收集其 character_snapshots 中
        未在当前时间点出现的角色。返回角色名 + 首次出现章节 + 快照信息。

        参数：
            time_id: 当前时间点
            exclude_chars: 要排除的角色名列表（如玩家角色名）

        返回：
            [{"name", "first_chapter", "first_chapter_title",
              "description", "status", "location", "goals", "is_alive"}, ...]
        """
        node = self.nodes.get(time_id)
        if not node:
            return []
        current_chars = set(node.character_snapshots.keys())
        if exclude_chars:
            current_chars.update(exclude_chars)

        # 收集当前时间点之后的所有节点
        idx = self._ordered_ids.index(time_id) if time_id in self._ordered_ids else -1
        if idx < 0:
            return []
        future_ids = self._ordered_ids[idx + 1:]

        # 遍历未来节点，找出新出现的角色
        seen = set(current_chars)
        future_chars: list[dict] = []
        for fid in future_ids:
            fnode = self.nodes[fid]
            for name, snap in fnode.character_snapshots.items():
                if name in seen:
                    continue
                seen.add(name)
                # 处理 dict 格式（序列化后）和 CharacterSnapshot 对象
                if isinstance(snap, dict):
                    future_chars.append({
                        "name": snap.get("name", name),
                        "first_chapter": fnode.chapter_index,
                        "first_chapter_title": fnode.chapter_title,
                        "description": snap.get("description", ""),
                        "status": snap.get("status", ""),
                        "location": snap.get("location", ""),
                        "goals": snap.get("goals", []),
                        "is_alive": snap.get("is_alive", True),
                        "emotional_state": snap.get("emotional_state", ""),
                    })
                else:
                    future_chars.append({
                        "name": snap.name,
                        "first_chapter": fnode.chapter_index,
                        "first_chapter_title": fnode.chapter_title,
                        "description": snap.description,
                        "status": snap.status,
                        "location": snap.location,
                        "goals": snap.goals,
                        "is_alive": snap.is_alive,
                        "emotional_state": snap.emotional_state,
                    })
        return future_chars

    def get_future_events(self, time_id: str,
                          min_importance: float = 5.0) -> list[dict]:
        """[NovelRoleplay] 获取某时间点之后的关键事件（作为伏笔/既定未来）。
        用于小说扮演时注入伏笔系统 + 偏离度追踪。

        返回：
            [{"time_id", "chapter", "chapter_title", "event", "importance",
              "characters_involved": [str]}, ...]
        """
        node = self.nodes.get(time_id)
        if not node:
            return []
        idx = self._ordered_ids.index(time_id) if time_id in self._ordered_ids else -1
        if idx < 0:
            return []
        future_ids = self._ordered_ids[idx + 1:]

        events: list[dict] = []
        for fid in future_ids:
            fnode = self.nodes[fid]
            if fnode.importance < min_importance:
                continue
            events.append({
                "time_id": fid,
                "chapter": fnode.chapter_index,
                "chapter_title": fnode.chapter_title,
                "event": fnode.event_description,
                "importance": fnode.importance,
                "characters_involved": list(fnode.character_snapshots.keys()),
            })
        return events

    def get_timeline_summary(self) -> list[dict]:
        """获取时间轴摘要（用于前端可视化）"""
        return [
            {
                "time_id": tid,
                "chapter": self.nodes[tid].chapter_title or f"第{self.nodes[tid].chapter_index+1}章",
                "event": self.nodes[tid].event_description[:100],
                "importance": self.nodes[tid].importance,
                "char_count": len(self.nodes[tid].character_snapshots),
            }
            for tid in self._ordered_ids
        ]

    def to_dict(self) -> dict:
        """序列化"""
        return {
            "nodes": {tid: node.to_dict() for tid, node in self.nodes.items()},
            "ordered_ids": self._ordered_ids,
        }

    def from_dict(self, data: dict):
        """反序列化"""
        self.nodes = {}
        self._ordered_ids = data.get("ordered_ids", [])
        for tid, ndata in data.get("nodes", {}).items():
            node = TimelineNode(
                time_id=ndata["time_id"],
                chapter_index=ndata.get("chapter_index", 0),
                chapter_title=ndata.get("chapter_title", ""),
                story_time=ndata.get("story_time", ""),
                event_description=ndata.get("event_description", ""),
                importance=ndata.get("importance", 0.0),
            )
            node.world_state = ndata.get("world_state", {})
            node.accumulated_summaries = ndata.get("accumulated_summaries", [])
            node.original_future_events = ndata.get("original_future_events", [])
            # 恢复角色快照
            for char_name, snap_data in ndata.get("character_snapshots", {}).items():
                snap = CharacterSnapshot(
                    char_id=snap_data.get("char_id", char_name),
                    name=snap_data.get("name", char_name),
                    time_id=tid,
                    description=snap_data.get("description", ""),
                    status=snap_data.get("status", ""),
                )
                snap.relationships = snap_data.get("relationships", {})
                snap.skills = snap_data.get("skills", [])
                snap.inventory = snap_data.get("inventory", [])
                snap.location = snap_data.get("location", "")
                snap.goals = snap_data.get("goals", [])
                snap.is_alive = snap_data.get("is_alive", True)
                node.character_snapshots[char_name] = snap
            self.nodes[tid] = node
