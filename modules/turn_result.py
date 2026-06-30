"""
[v11] TurnResult — TurnProcessorV2.process() 的结构化输出契约
从 turn_processor_v2.py 提取为独立文件，减少文件依赖。
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class TurnResult:
    """[v10.5] TurnProcessorV2.process() 的结构化输出契约"""
    narrative: str = ""
    options: list = field(default_factory=list)
    dice_result: dict | None = None
    status_changes: dict = field(default_factory=dict)
    new_effects: list = field(default_factory=list)
    removed_effects: list = field(default_factory=list)
    world_event: dict | None = None
    auto_event: dict | None = None
    impact: dict | None = None
    death: dict | None = None
    suicide_confirm: bool = False
    identity_log: list = field(default_factory=list)
    audit_results: list = field(default_factory=list)
    auto_image: str = ""
    time_skip: int = 0
    year_evolution: dict | None = None
    intent_type: str = ""
    rules_triggered: list = field(default_factory=list)
    generation_strategy: str = ""
    butterfly_approval: dict | None = None
    narrative_review: dict | None = None
    task_board: dict | None = None
    curator: dict | None = None
    lessons_injected: str = ""
    foreshadow: dict | None = None
    continuity_audit: dict | None = None
    autonomous_memory: dict | None = None
    character_state_stats: dict | None = None
    scene_type: str = ""
    scene_stats: dict | None = None
    multi_agent_narrative: dict | None = None

    def to_dict(self) -> dict:
        """转换为 dict（向后兼容现有调用方）"""
        from dataclasses import asdict
        return asdict(self)
