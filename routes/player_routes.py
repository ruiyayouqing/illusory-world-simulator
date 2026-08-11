from __future__ import annotations
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .deps import get_engine

logger = logging.getLogger("chronoverse")
router = APIRouter(prefix="/api")


class UpdatePlayerProfileRequest(BaseModel):
    """修改主角基础信息（名字/年龄等显示字段，不影响 agent_id 索引）"""
    name: Optional[str] = None
    age: Optional[int] = None
    current_goal: Optional[str] = None


@router.get("/level")
async def get_level_info():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return {"level": engine.get_level_info()}


@router.get("/whispers")
async def get_whispers():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    try:
        return {"whispers": engine.get_whispers()}
    except Exception as e:
        return {"whispers": [], "error": str(e)}


@router.get("/memoir")
async def get_memoir():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return {"memoir": engine.get_full_memoir()}


@router.get("/memoir/reflection")
async def get_reflection():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return {"reflection": engine.get_current_reflection()}


@router.get("/inventory")
async def get_inventory():
    engine = get_engine()
    if not engine or not engine.player_state:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return {"summary": engine.item_system.get_inventory_summary(engine.player_state)}


@router.get("/skill-tree")
async def get_skill_tree():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    tree = engine.skill_tree
    return {
        "available": tree.get_available_skills() if tree else [],
        "unlocked": tree.unlocked_skills if tree else [],
        "points": tree.skill_points if tree else 0,
        "display": tree.get_tree_display() if tree else "",
    }


@router.post("/skill-tree/unlock")
async def unlock_skill(req: dict):
    engine = get_engine()
    if not engine or not engine.skill_tree:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    slot_id = req.get("slot_id", "")
    result = engine.skill_tree.unlock_skill(slot_id)
    return result


@router.get("/quests")
async def get_quests():
    engine = get_engine()
    if not engine or not engine.quest_system:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return {"active": engine.quest_system.get_active_quests()}


@router.get("/reputation")
async def get_reputation():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    rep = engine.reputation_system
    return {
        "display": rep.get_reputation_display() if rep else "",
        "wanted": rep.get_wanted_effects() if rep else {},
        "faction_reputation": rep.faction_reputation if rep else {},
    }


@router.get("/context-debug")
async def get_context_debug():
    """AI 上下文调试面板数据"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return engine.get_context_debug()


@router.get("/player/profile")
async def get_player_profile():
    """获取主角基础信息（供修改面板加载）"""
    engine = get_engine()
    if not engine or not engine.player_state:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    p = engine.player_state
    return {
        "player": {
            "name": p.name,
            "age": p.age,
            "current_goal": p.current_goal,
            "location": p.location,
            "tags": p.tags,
        }
    }


@router.put("/player/profile")
async def update_player_profile(req: UpdatePlayerProfileRequest):
    """修改主角基础信息。
    name/age/current_goal 均为显示字段，player 的 agent_id 固定为 'player_01'，
    改名不会产生孤儿引用。保存时 save_manager 会自动同步 manifest.player_name。
    """
    engine = get_engine()
    if not engine or not engine.player_state:
        raise HTTPException(status_code=503, detail="游戏未初始化")

    p = engine.player_state
    if req.name is not None:
        new_name = req.name.strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="名字不能为空")
        old_name = p.name
        p.name = new_name
        # [Bug] player.relations 按 NPC 名字作 key，与玩家名字无关，无需同步。
        # 但 meta/manifest 的 player_name 需由 save_manager 在保存时自动更新。
        logger.info("Player renamed: %r -> %r", old_name, new_name)
    if req.age is not None:
        p.age = req.age
    if req.current_goal is not None:
        p.current_goal = req.current_goal.strip()

    engine.save_manager.save_state(
        engine.current_world_id,
        engine.meta,
        engine.world_state,
        engine.player_state,
        engine.npc_states,
    )

    return {"status": "ok", "player": {"name": p.name, "age": p.age, "current_goal": p.current_goal}}
