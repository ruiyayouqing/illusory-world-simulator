from __future__ import annotations
import random
from .schemas import PlayerState, WorldState, NPCState


class InfluenceEdge:
    def __init__(self, source: str, target: str, weight: float = 50.0,
                 relation_type: str = "acquaintance"):
        self.source = source
        self.target = target
        self.weight = weight
        self.relation_type = relation_type
        self.interaction_count: int = 0
        self.last_interaction_day: int = 0

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "weight": self.weight,
            "relation_type": self.relation_type,
            "interaction_count": self.interaction_count,
        }


class InfluenceEvent:
    def __init__(self, originator: str, event_type: str, description: str,
                 day: int, initial_impact: float = 5.0):
        self.originator = originator
        self.event_type = event_type
        self.description = description
        self.day = day
        self.initial_impact = initial_impact
        self.propagations: list[dict] = []
        self.total_affected: int = 0


class InfluenceNetwork:
    def __init__(self):
        self.edges: dict[tuple[str, str], InfluenceEdge] = {}
        self.events: list[InfluenceEvent] = []
        self.propagation_log: list[dict] = []

    def add_edge(self, source: str, target: str, weight: float = 50.0,
                 relation_type: str = "acquaintance"):
        key = (source, target)
        if key not in self.edges:
            self.edges[key] = InfluenceEdge(source, target, weight, relation_type)
        reverse_key = (target, source)
        if reverse_key not in self.edges:
            self.edges[reverse_key] = InfluenceEdge(target, source, weight, relation_type)

    def update_edge_weight(self, source: str, target: str, delta: float, day: int):
        key = (source, target)
        if key in self.edges:
            edge = self.edges[key]
            edge.weight = max(0, min(100, edge.weight + delta))
            edge.interaction_count += 1
            edge.last_interaction_day = day

        reverse_key = (target, source)
        if reverse_key in self.edges:
            edge = self.edges[reverse_key]
            edge.weight = max(0, min(100, edge.weight + delta))
            edge.interaction_count += 1
            edge.last_interaction_day = day

    def initialize_from_player(self, player: PlayerState, npc_states: dict[str, NPCState]):
        for npc_id, rel in player.relations.items():
            self.add_edge("player", npc_id, float(rel.favor), rel.relation_type)

        for npc_id, npc in npc_states.items():
            if ("player", npc_id) not in self.edges and (npc_id, "player") not in self.edges:
                favor = npc.relation_to_player.favor if npc.relation_to_player else 50
                rel_type = npc.relation_to_player.relation_type if npc.relation_to_player else "acquaintance"
                self.add_edge("player", npc_id, float(favor), rel_type)

        npc_ids = list(npc_states.keys())
        for i, a_id in enumerate(npc_ids):
            for b_id in npc_ids[i+1:]:
                if random.random() < 0.3:
                    weight = random.uniform(20, 80)
                    self.add_edge(a_id, b_id, weight, "acquaintance")

    def propagate_influence(self, originator: str, event_type: str,
                            description: str, day: int,
                            initial_impact: float = 5.0,
                            max_hops: int = 3,
                            decay_factor: float = 0.5) -> InfluenceEvent:
        event = InfluenceEvent(originator, event_type, description, day, initial_impact)

        visited = {originator}
        frontier = [(originator, initial_impact)]
        hop = 0

        while frontier and hop < max_hops:
            next_frontier = []
            for node, impact in frontier:
                for key, edge in self.edges.items():
                    if key[0] != node:
                        continue
                    target = key[1]
                    if target in visited:
                        continue

                    attenuation = (edge.weight / 100.0) * decay_factor
                    propagated_impact = impact * attenuation

                    if propagated_impact < 0.5:
                        continue

                    visited.add(target)
                    event.propagations.append({
                        "from": node,
                        "to": target,
                        "impact": round(propagated_impact, 2),
                        "hop": hop + 1,
                        "relation_weight": edge.weight,
                    })

                    self.propagation_log.append({
                        "day": day,
                        "originator": originator,
                        "event_type": event_type,
                        "from_node": node,
                        "to_node": target,
                        "impact": round(propagated_impact, 2),
                        "hop": hop + 1,
                    })

                    next_frontier.append((target, propagated_impact))

            frontier = next_frontier
            hop += 1

        event.total_affected = len(visited) - 1
        self.events.append(event)
        return event

    def get_node_influence(self, node_id: str) -> dict:
        outgoing = [e for k, e in self.edges.items() if k[0] == node_id]
        incoming = [e for k, e in self.edges.items() if k[1] == node_id]

        total_out_weight = sum(e.weight for e in outgoing)
        total_in_weight = sum(e.weight for e in incoming)
        connection_count = len(set(e.target for e in outgoing) | set(e.source for e in incoming))

        influence_score = (total_out_weight + total_in_weight * 0.5) / max(1, connection_count)

        return {
            "node_id": node_id,
            "connections": connection_count,
            "outgoing_avg_weight": round(total_out_weight / max(1, len(outgoing)), 1),
            "incoming_avg_weight": round(total_in_weight / max(1, len(incoming)), 1),
            "influence_score": round(influence_score, 1),
        }

    def get_graph_data(self, npc_names: dict[str, str] = None, player_name: str = "玩家") -> dict:
        nodes = set()
        for key in self.edges:
            nodes.add(key[0])
            nodes.add(key[1])

        # [Bug] 英文→中文关系类型映射
        _REL_MAP = {
            "acquaintance": "相识", "friend": "朋友", "enemy": "敌人",
            "lover": "恋人", "spouse": "配偶", "ally": "盟友",
            "rival": "对手", "family": "亲属", "master": "师徒",
            "subordinate": "下属", "stranger": "陌生人",
        }

        node_data = []
        for node_id in nodes:
            info = self.get_node_influence(node_id)
            # [Bug] 玩家节点使用玩家名字
            if node_id == "player":
                label = player_name
            elif npc_names and node_id in npc_names:
                label = npc_names[node_id]
            elif node_id.startswith("npc_"):
                label = node_id[4:]
            else:
                label = node_id
            node_data.append({
                "id": node_id,
                "label": label,
                "influence_score": info["influence_score"],
                "connections": info["connections"],
            })

        edge_data = []
        seen = set()
        for key, edge in self.edges.items():
            edge_key = tuple(sorted([key[0], key[1]]))
            if edge_key in seen:
                continue
            seen.add(edge_key)
            edge_dict = edge.to_dict()
            # [Bug] 关系类型翻译为中文
            edge_dict["relation_type"] = _REL_MAP.get(edge.relation_type, edge.relation_type)
            edge_data.append(edge_dict)

        return {"nodes": node_data, "edges": edge_data}

    def get_influence_cascade(self, originator: str, depth: int = 2) -> list[dict]:
        cascade = []
        visited = {originator}
        frontier = [originator]

        for hop in range(depth):
            next_frontier = []
            for node in frontier:
                for key, edge in self.edges.items():
                    if key[0] == node and key[1] not in visited:
                        visited.add(key[1])
                        cascade.append({
                            "hop": hop + 1,
                            "from": node,
                            "to": key[1],
                            "weight": edge.weight,
                            "relation": edge.relation_type,
                        })
                        next_frontier.append(key[1])
            frontier = next_frontier

        return cascade

    def get_recent_events(self, n: int = 10) -> list[dict]:
        return [{
            "originator": e.originator,
            "event_type": e.event_type,
            "description": e.description,
            "day": e.day,
            "total_affected": e.total_affected,
            "propagations_count": len(e.propagations),
        } for e in self.events[-n:]]

    def to_dict(self) -> dict:
        return {
            "edges": [e.to_dict() for e in self.edges.values()],
            "events": self.get_recent_events(20),
        }

    def from_dict(self, data: dict):
        self.edges.clear()
        for ed in data.get("edges", []):
            key = (ed["source"], ed["target"])
            edge = InfluenceEdge(ed["source"], ed["target"], ed.get("weight", 50),
                                 ed.get("relation_type", "acquaintance"))
            edge.interaction_count = ed.get("interaction_count", 0)
            self.edges[key] = edge
