from __future__ import annotations
import logging
import uuid
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel, Field

from .deps import get_engine

logger = logging.getLogger("chronoverse")
router = APIRouter(prefix="/api")


class AddNpcRequest(BaseModel):
    name: str
    age: int = 20
    role: str = ""
    personality: str = ""
    speaking_style: str = ""
    dialogue_examples: list[str] = Field(default_factory=list)
    location: str = ""
    relation_type: str = "陌生人"
    favor: int = 50
    tags: list[str] = Field(default_factory=list)


class UpdateNpcRequest(BaseModel):
    name: Optional[str] = None
    age: Optional[int] = None
    role: Optional[str] = None
    personality: Optional[str] = None
    speaking_style: Optional[str] = None
    dialogue_examples: Optional[list[str]] = None
    location: Optional[str] = None
    relation_type: Optional[str] = None
    favor: Optional[int] = None
    tags: Optional[list[str]] = None
    mbti_type: Optional[str] = None
    status_effects: Optional[list[str]] = None
    stats: Optional[dict] = None
    ai_behavior: Optional[dict] = None


@router.get("/npcs")
async def get_npcs():
    engine = get_engine()
    if not engine:
        return {"npcs": []}
    return {"npcs": engine.get_npc_list()}


@router.post("/add-npc")
async def add_npc(req: AddNpcRequest):
    engine = get_engine()
    if not engine or not engine.player_state:
        return {"error": "游戏未初始化"}

    npc_id = f"npc_{uuid.uuid4().hex[:8]}"
    from modules.schemas import NPCState, RelationEntry
    npc = NPCState(
        agent_id=npc_id,
        name=req.name,
        age=req.age,
        role=req.role,
        personality=req.personality,
        speaking_style=req.speaking_style,
        dialogue_examples=req.dialogue_examples,
        current_location=req.location or engine.player_state.location,
        relation_to_player=RelationEntry(
            favor=req.favor,
            relation_type=req.relation_type,
            description="玩家自定义添加的角色",
        ),
        tags=req.tags or ([req.role] if req.role else []),
    )

    engine.npc_states[npc_id] = npc
    engine.save_manager.save_state(
        engine.current_world_id,
        engine.meta,
        engine.world_state,
        engine.player_state,
        engine.npc_states,
    )

    logger.info("Player added NPC: %s (%s)", req.name, req.role)

    return {
        "status": "ok",
        "npc": {
            "id": npc_id,
            "name": req.name,
            "role": req.role,
            "personality": req.personality,
            "speaking_style": req.speaking_style,
        },
    }


@router.get("/npc-actions")
async def get_npc_actions():
    engine = get_engine()
    if not engine or not engine.npc_autonomous:
        return {"npc_actions": []}
    day = engine.world_state.current_day if engine.world_state else 0
    location = engine.player_state.location if engine.player_state else ""
    nearby = engine.npc_autonomous.get_npc_nearby_actions(location, day)
    today = engine.npc_autonomous.get_npc_logs_today(day)
    return {"nearby": nearby, "today": today}


# ── [v10+++] 异步 NPC 生成 ──────────────────────────────────
# [Bug] 必须定义在 /npc/{npc_id} 之前，否则 spawn-status 会被当作 npc_id 匹配

@router.post("/npc/async-create")
async def async_create_npcs():
    """[v10+++] 启动后台异步 NPC 生成。
    玩家进入游戏后调用，用 cheap_llm 在后台逐步补充重要 NPC。
    不阻塞响应，立即返回生成状态。"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    result = engine.npc_spawner.start_async_spawn()
    logger.info("[API] async-create-npcs: %s", result)
    return result


@router.get("/npc/spawn-status")
async def get_npc_spawn_status():
    """[v10+++] 查询后台 NPC 生成状态"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    spawner = engine.npc_spawner
    return {
        "spawning": spawner.is_spawning(),
        "current_count": len(engine.npc_states),
        "target": spawner.TARGET_NPC_COUNT,
    }


@router.get("/npc/{npc_id}")
async def get_npc_detail(npc_id: str):
    engine = get_engine()
    if not engine or not engine.npc_states:
        return {"error": "游戏未初始化"}
    npc = engine.npc_states.get(npc_id)
    if not npc:
        return {"error": "角色不存在"}
    data = npc.model_dump()
    return {"npc": data}


@router.put("/npc/{npc_id}")
async def update_npc(npc_id: str, req: UpdateNpcRequest):
    engine = get_engine()
    if not engine or not engine.npc_states:
        return {"error": "游戏未初始化"}
    npc = engine.npc_states.get(npc_id)
    if not npc:
        return {"error": "角色不存在"}

    update_data = req.model_dump(exclude_none=True)

    if "name" in update_data:
        npc.name = update_data["name"]
    if "age" in update_data:
        npc.age = update_data["age"]
    if "role" in update_data:
        old_role = npc.role
        new_role = update_data["role"]
        if old_role != new_role:
            npc.record_role_change(new_role, "玩家修改", engine.world_state.current_day if engine.world_state else 1)
    if "personality" in update_data:
        npc.personality = update_data["personality"]
    if "speaking_style" in update_data:
        npc.speaking_style = update_data["speaking_style"]
    if "dialogue_examples" in update_data:
        npc.dialogue_examples = update_data["dialogue_examples"]
    if "location" in update_data:
        npc.current_location = update_data["location"]
    if "relation_type" in update_data or "favor" in update_data:
        old_rel = npc.relation_to_player.relation_type
        new_rel = update_data.get("relation_type", old_rel)
        new_fav = update_data.get("favor", npc.relation_to_player.favor)
        if old_rel != new_rel:
            npc.record_relation_change(new_rel, "玩家修改", engine.world_state.current_day if engine.world_state else 1)
        npc.relation_to_player.relation_type = new_rel
        npc.relation_to_player.favor = new_fav
    if "tags" in update_data:
        npc.tags = update_data["tags"]
    if "mbti_type" in update_data:
        npc.mbti_type = update_data["mbti_type"]
    if "status_effects" in update_data:
        npc.status_effects = update_data["status_effects"]
    if "stats" in update_data:
        for k, v in update_data["stats"].items():
            if hasattr(npc.stats, k):
                setattr(npc.stats, k, v)
    if "ai_behavior" in update_data:
        for k, v in update_data["ai_behavior"].items():
            if k in npc.ai_behavior:
                npc.ai_behavior[k] = v

    engine.save_manager.save_state(
        engine.current_world_id,
        engine.meta,
        engine.world_state,
        engine.player_state,
        engine.npc_states,
    )

    logger.info("Player updated NPC: %s (%s)", npc.name, npc_id)
    return {"status": "ok", "npc": npc.model_dump()}


@router.get("/npc-zones")
async def get_npc_zones():
    engine = get_engine()
    if not engine or not engine.npc_perception:
        return {"zones": []}
    return {"zones": engine.npc_perception.get_zone_display()}


@router.get("/npc-evolution/{npc_id}")
async def get_npc_evolution(npc_id: str):
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return {"evolution": engine.get_npc_evolution_summary(npc_id)}


@router.get("/who-is-who")
async def get_who_is_who():
    engine = get_engine()
    if not engine or not engine.npc_registry:
        return {"error": "游戏未初始化", "factions": {}}
    directory = engine.npc_registry.get_world_npc_directory()
    recent_rumors = []
    if engine.world_state:
        day = engine.world_state.current_day
        pending = engine.npc_registry.get_pending_rumors(day=day, count=2)
        for r in pending:
            recent_rumors.append({
                "rumor_id": r.rumor_id,
                "npc_name": r.npc_name,
                "content": r.content,
                "day": r.day,
                "source": r.source,
                "is_major_event": r.is_major_event,
            })
    directory["recent_rumors"] = recent_rumors
    directory["info_visibility"] = engine.npc_registry.get_info_visibility()
    return directory


@router.get("/who-is-who/{npc_id}")
async def get_npc_who_is_who_detail(npc_id: str):
    engine = get_engine()
    if not engine or not engine.npc_registry:
        return {"error": "游戏未初始化"}
    info = engine.npc_registry.get_npc_visible_info(npc_id)
    return info


class SetVisibilityRequest(BaseModel):
    mode: str


@router.post("/npc-visibility")
async def set_npc_visibility(req: SetVisibilityRequest):
    engine = get_engine()
    if not engine or not engine.npc_registry:
        return {"error": "游戏未初始化"}
    mode = req.mode
    if mode not in ("immersive", "semi", "god"):
        return {"error": "invalid mode"}
    engine.npc_registry.set_info_visibility(mode)
    # [v9] Bug H2b: 改用 config_routes._write_config 统一写入路径，
    # 确保缓存失效一致且不破坏已加密的 api_key
    import json
    from pathlib import Path
    from .config_routes import _write_config
    config_path = Path(__file__).parent.parent / "config.json"
    try:
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        else:
            config = {}
        config["npc_info_visibility"] = mode
        _write_config(config)
    except Exception as e:
        logger.warning("Failed to save npc visibility to config: %s", e)
    return {"status": "ok", "mode": mode}


class HearRumorRequest(BaseModel):
    npc_id: str
    content: str
    is_major_event: bool = False


@router.post("/hear-rumor")
async def hear_rumor(req: HearRumorRequest):
    engine = get_engine()
    if not engine or not engine.npc_registry:
        return {"error": "游戏未初始化"}
    day = engine.world_state.current_day if engine.world_state else 1
    engine.npc_registry.add_rumor(req.npc_id, req.content, day=day, is_major_event=req.is_major_event)
    return {"status": "ok"}


class MeetNpcRequest(BaseModel):
    npc_id: str
    interaction: dict = None


@router.post("/meet-npc/{npc_id}")
async def meet_npc(npc_id: str, req: MeetNpcRequest = None):
    engine = get_engine()
    if not engine or not engine.npc_registry:
        return {"error": "游戏未初始化"}
    day = engine.world_state.current_day if engine.world_state else 1
    npc = engine.npc_registry.get_npc(npc_id)
    if not npc:
        return {"error": "NPC不存在"}
    if req and req.interaction:
        engine.npc_registry.add_interaction(npc_id, req.interaction, day=day)
    else:
        engine.npc_registry.mark_acquainted(npc_id, day=day)
    return {"status": "ok", "knowledge_level": engine.npc_registry.knowledge.get(npc_id, 0)}
