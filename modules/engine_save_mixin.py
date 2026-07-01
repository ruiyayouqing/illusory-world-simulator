"""
[v9] 存档/读档 Mixin — 从 GameEngine 抽取的存档槽和持久化逻辑
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .game_engine import GameEngine

from .registry import trigger_hook

logger = logging.getLogger("chronoverse")


class SaveMixin:
    """存档/读档相关方法"""

    def save_game(self: "GameEngine", save_type: str = "auto") -> bool:
        if not self.current_world_id or not self.meta or not self.world_state:
            return False
        # [v10+++] 线程锁保护存档写入，防止 NpcSpawner 后台线程与主线程并发写入冲突
        with self._save_lock:
            try:
                if save_type == "manual":
                    slot_name = f"第{self.world_state.current_day}天 {self.world_state.current_time}"
                    self.save_to_slot(slot_name, f"{self.player_state.name} {self.player_state.age}岁")
                else:
                    self.save_manager.save_state(
                        world_id=self.current_world_id,
                        meta=self.meta,
                        world_state=self.world_state,
                        player_state=self.player_state,
                        npc_states=self.npc_states,
                        event_log=self.event_log_today,
                        save_type=save_type,
                    )
                self._save_game_state()
                # [v10.5] 使用实例级 trigger_hook 而非全局
                self.trigger_hook("on_save",
                             world_id=self.current_world_id,
                             save_type=save_type,
                             world_state=self.world_state,
                             player_state=self.player_state)
                return True
            except Exception as e:
                logger.error("Save failed: %s", e)
                return False

    def save_to_slot(self: "GameEngine", slot_name: str, description: str = "") -> str:
        if not self.current_world_id or not self.meta:
            return ""
        timeline = self.save_manager.get_timeline(self.current_world_id)
        # [Bug] 把当前 narrative_history 一起存到 slot，加载 slot 后才能恢复历史记录
        return timeline.create_slot(
            slot_name, self.meta, self.world_state,
            self.player_state, self.npc_states, description,
            narrative_history=self.narrative_history,
        )

    def load_from_slot(self: "GameEngine", slot_id: str) -> bool:
        if not self.current_world_id:
            return False
        timeline = self.save_manager.get_timeline(self.current_world_id)
        state = timeline.load_slot(slot_id)
        if not state:
            return False
        from .schemas import SaveMeta, WorldState, PlayerState, NPCState
        self.meta = SaveMeta(**state["meta"])
        self.world_state = WorldState(**state["world_state"])
        self.player_state = PlayerState(**state["player_state"])
        self.npc_states = {k: NPCState(**v) for k, v in state.get("npc_states", {}).items()}
        # [Bug] 从 slot 恢复 narrative_history，并同步持久化计数器，否则 JSONL 增量写入会把旧历史重复追加
        self.narrative_history = list(state.get("narrative_history", []))
        self._persisted_narrative_count = len(self.narrative_history)
        self._narrative_compressed = False
        # [Bug] 恢复 visual_engine.image_history，否则加载 slot 后已生成的图片无法显示
        if self.visual_engine:
            try:
                import json as _json
                from pathlib import Path
                gs_file = self.save_manager.base_dir / self.current_world_id / "state" / "game_state.json"
                if gs_file.exists():
                    gs = _json.loads(gs_file.read_text(encoding="utf-8"))
                    self.visual_engine.image_history = gs.get("visual_engine", {}).get("image_history", [])
            except Exception:
                pass
        return True

    def list_slots(self: "GameEngine") -> list[dict]:
        if self.current_world_id:
            return self.save_manager.get_timeline(self.current_world_id).list_slots()
        return []

    def delete_slot(self: "GameEngine", slot_id: str) -> bool:
        if self.current_world_id:
            return self.save_manager.get_timeline(self.current_world_id).delete_slot(slot_id)
        return False
