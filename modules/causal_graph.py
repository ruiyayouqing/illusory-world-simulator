"""
[v1.3] 因果链可视化模块（全模式通用）

记录玩家决策点 → 后果链的有向图，用于 debug 可视化。

设计原则：
- 阈值过滤：只记录 importance >= min_importance_to_record 的回合，避免噪音
- 多维度重要性：综合蝴蝶效应分、规则效果数、涉及 NPC 数、性格转折等
- 依赖追溯：每个节点可指向 parent_turn（上游触发），形成因果链
- 模式无关：普通模式和小说模式都启用（小说模式额外记录 divergence 字段）
- 持久化：通过 to_dict/from_dict 存入存档（GameMeta.causal_graph）

节点结构：
    CausalNode:
        turn_id: 回合号
        day: 游戏内天数
        player_input_excerpt: 玩家输入摘要（前80字）
        narrative_excerpt: AI 叙事摘要（前120字）
        importance: 重要性分数（0-15）
        triggered_events: 触发的事件类型列表（如 butterfly_effect/personality_shift/foreshadow）
        effects_summary: 后果摘要（如"气血+10, 与李逍遥关系-5"）
        parent_turn_ids: 上游依赖回合（如本回合触发了上回合埋的伏笔）
        timestamp: 实际时间戳
        novel_divergence: 小说模式下的蝴蝶效应偏离度（普通模式为0）
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import NPCState

logger = logging.getLogger("chronoverse.causal_graph")


class CausalNode:
    """因果链上的一个决策节点"""

    def __init__(
        self,
        turn_id: int,
        day: int,
        player_input: str = "",
        narrative: str = "",
        importance: float = 0.0,
        triggered_events: list[str] | None = None,
        effects_summary: str = "",
        parent_turn_ids: list[int] | None = None,
        novel_divergence: float = 0.0,
        timestamp: float | None = None,
    ):
        self.turn_id = int(turn_id)
        self.day = int(day)
        self.player_input_excerpt = (player_input or "")[:80]
        self.narrative_excerpt = (narrative or "")[:120]
        self.importance = float(importance)
        self.triggered_events = list(triggered_events or [])
        self.effects_summary = (effects_summary or "")[:200]
        self.parent_turn_ids = list(parent_turn_ids or [])
        self.novel_divergence = float(novel_divergence or 0.0)
        self.timestamp = float(timestamp if timestamp is not None else time.time())

    def to_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "day": self.day,
            "player_input_excerpt": self.player_input_excerpt,
            "narrative_excerpt": self.narrative_excerpt,
            "importance": round(self.importance, 2),
            "triggered_events": self.triggered_events,
            "effects_summary": self.effects_summary,
            "parent_turn_ids": self.parent_turn_ids,
            "novel_divergence": round(self.novel_divergence, 2),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CausalNode":
        return cls(
            turn_id=d.get("turn_id", 0),
            day=d.get("day", 0),
            player_input=d.get("player_input_excerpt", ""),
            narrative=d.get("narrative_excerpt", ""),
            importance=d.get("importance", 0.0),
            triggered_events=d.get("triggered_events", []),
            effects_summary=d.get("effects_summary", ""),
            parent_turn_ids=d.get("parent_turn_ids", []),
            novel_divergence=d.get("novel_divergence", 0.0),
            timestamp=d.get("timestamp", time.time()),
        )


class CausalGraph:
    """因果链图：维护所有重要决策节点的集合，提供查询接口"""

    def __init__(self, min_importance: float = 6.0, max_nodes: int = 500):
        self.min_importance = float(min_importance)
        self.max_nodes = int(max_nodes)
        self.nodes: list[CausalNode] = []
        # turn_id → node 索引，便于反查
        self._index: dict[int, CausalNode] = {}

    def add_node(self, node: CausalNode) -> bool:
        """添加节点。低于阈值的会被丢弃。
        返回是否成功添加。"""
        if node.importance < self.min_importance:
            return False
        # 已存在同 turn_id 则覆盖
        existing = self._index.get(node.turn_id)
        if existing:
            existing.__dict__.update(node.__dict__)
            return True
        self.nodes.append(node)
        self._index[node.turn_id] = node
        # 容量上限：FIFO 淘汰最旧
        if len(self.nodes) > self.max_nodes:
            old = self.nodes.pop(0)
            self._index.pop(old.turn_id, None)
        return True

    def get_node(self, turn_id: int) -> CausalNode | None:
        return self._index.get(turn_id)

    def get_recent(self, n: int = 20) -> list[CausalNode]:
        """返回最近 N 个节点（按 turn_id 倒序）"""
        sorted_nodes = sorted(self.nodes, key=lambda x: x.turn_id, reverse=True)
        return sorted_nodes[:n]

    def get_all_nodes(self) -> list[CausalNode]:
        """返回所有节点（按 turn_id 升序）"""
        return sorted(self.nodes, key=lambda x: x.turn_id)

    def get_edges(self) -> list[dict]:
        """返回所有因果边（基于 parent_turn_ids）"""
        edges = []
        for node in self.nodes:
            for pid in node.parent_turn_ids:
                parent = self._index.get(pid)
                if parent:
                    edges.append({
                        "source": pid,
                        "target": node.turn_id,
                        "type": "causal",
                    })
        return edges

    def clear(self):
        self.nodes.clear()
        self._index.clear()

    def to_dict(self) -> dict:
        return {
            "min_importance": self.min_importance,
            "max_nodes": self.max_nodes,
            "nodes": [n.to_dict() for n in self.nodes],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CausalGraph":
        graph = cls(
            min_importance=d.get("min_importance", 6.0),
            max_nodes=d.get("max_nodes", 500),
        )
        for nd in d.get("nodes", []):
            try:
                node = CausalNode.from_dict(nd)
                graph.nodes.append(node)
                graph._index[node.turn_id] = node
            except Exception as e:
                logger.warning("[CausalGraph] 反序列化节点失败: %s", e)
        return graph

    def to_vis_format(self) -> dict:
        """转成可视化格式（Cytoscape.js elements）"""
        elements = []
        # 节点
        for node in self.get_all_nodes():
            # 重要性决定颜色
            imp = node.importance
            if imp >= 10:
                color = "#d44"  # 红色 - 重大
            elif imp >= 8:
                color = "#fa4"  # 橙色 - 重要
            else:
                color = "#4af"  # 蓝色 - 普通
            elements.append({
                "data": {
                    "id": str(node.turn_id),
                    "label": f"T{node.turn_id}\nD{node.day}",
                    "turn_id": node.turn_id,
                    "day": node.day,
                    "importance": round(node.importance, 2),
                    "player_input": node.player_input_excerpt,
                    "narrative": node.narrative_excerpt,
                    "effects": node.effects_summary,
                    "triggered_events": node.triggered_events,
                    "novel_divergence": round(node.novel_divergence, 2),
                    "color": color,
                    "size": 20 + min(node.importance * 2, 30),
                }
            })
        # 边
        for edge in self.get_edges():
            elements.append({
                "data": {
                    "id": f"e{edge['source']}-{edge['target']}",
                    "source": str(edge["source"]),
                    "target": str(edge["target"]),
                    "type": edge["type"],
                }
            })
        return {"elements": elements, "count": len(self.nodes)}


def compute_importance(
    turn_id: int,
    player_input: str,
    narrative: str,
    rule_effects_count: int = 0,
    involved_npc_count: int = 0,
    butterfly_score: float = 0.0,
    novel_divergence: float = 0.0,
    triggered_events: list[str] | None = None,
    has_personality_shift: bool = False,
    has_foreshadow: bool = False,
) -> tuple[float, list[str]]:
    """[v1.3] 多维度计算回合重要性分数。

    维度（加权累加，上限 15）：
    - 蝴蝶效应分：score * 0.5（0-5分，score 范围 0-10）
    - 小说偏离度：divergence * 0.05（0-5分，divergence 范围 0-100）
    - 规则效果数：每条 +0.5（0-3分）
    - 涉及 NPC 数：每个 +0.4（0-3分）
    - 触发性格转折：+3
    - 触发伏笔：+2
    - 玩家输入长度：>50字 +1，>100字 +2
    - 叙事长度：>500字 +1，>1000字 +2

    返回 (importance, triggered_events_normalized)
    """
    events = list(triggered_events or [])
    score = 0.0

    # 蝴蝶效应分（impact_score，范围 0-10）
    if butterfly_score > 0:
        score += min(butterfly_score * 0.5, 5.0)
        if "butterfly_effect" not in events:
            events.append("butterfly_effect")

    # 小说偏离度（novel_divergence_score，范围 0-100）
    if novel_divergence > 0:
        score += min(novel_divergence * 0.05, 5.0)
        if "novel_divergence" not in events:
            events.append("novel_divergence")

    # 规则效果
    if rule_effects_count > 0:
        score += min(rule_effects_count * 0.5, 3.0)

    # 涉及 NPC
    if involved_npc_count > 0:
        score += min(involved_npc_count * 0.4, 3.0)

    # 性格转折
    if has_personality_shift:
        score += 3.0
        if "personality_shift" not in events:
            events.append("personality_shift")

    # 伏笔
    if has_foreshadow:
        score += 2.0
        if "foreshadow" not in events:
            events.append("foreshadow")

    # 输入长度
    pi_len = len(player_input or "")
    if pi_len > 100:
        score += 2.0
    elif pi_len > 50:
        score += 1.0

    # 叙事长度
    n_len = len(narrative or "")
    if n_len > 1000:
        score += 2.0
    elif n_len > 500:
        score += 1.0

    return min(score, 15.0), events


def build_effects_summary(
    player_input: str,
    narrative: str,
    rule_effects_count: int = 0,
    npc_names: list[str] | None = None,
    butterfly_narrative: str = "",
) -> str:
    """构造后果摘要（200字内）"""
    parts = []
    if rule_effects_count > 0:
        parts.append(f"规则效果×{rule_effects_count}")
    if npc_names:
        parts.append(f"涉及NPC: {','.join(npc_names[:3])}")
    if butterfly_narrative:
        parts.append(f"蝴蝶效应: {butterfly_narrative[:60]}")
    if not parts:
        # 兜底：取叙事末尾
        n = (narrative or "").strip()
        if n:
            parts.append(n[-80:])
    return "; ".join(parts)[:200]


def is_feature_enabled(config: dict) -> bool:
    """检查因果链可视化是否启用"""
    if not config:
        return True  # 默认启用
    return bool(config.get("features", {}).get("causal_graph", {}).get("enabled", True))


def get_min_importance(config: dict) -> float:
    """获取最低记录阈值"""
    if not config:
        return 6.0
    try:
        return float(config.get("features", {}).get("causal_graph", {}).get("min_importance_to_record", 6.0))
    except Exception:
        return 6.0
