from __future__ import annotations
import logging
from fastapi import APIRouter

from .deps import get_engine

logger = logging.getLogger("chronoverse")
router = APIRouter(prefix="/api")


@router.get("/level")
async def get_level_info():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return {"level": engine.get_level_info()}


@router.get("/whispers")
async def get_whispers():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    try:
        return {"whispers": engine.get_whispers()}
    except Exception as e:
        return {"whispers": [], "error": str(e)}


@router.get("/memoir")
async def get_memoir():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return {"memoir": engine.get_full_memoir()}


@router.get("/memoir/reflection")
async def get_reflection():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return {"reflection": engine.get_current_reflection()}


@router.get("/inventory")
async def get_inventory():
    engine = get_engine()
    if not engine or not engine.player_state:
        return {"error": "游戏未初始化"}
    return {"summary": engine.item_system.get_inventory_summary(engine.player_state)}


@router.get("/skill-tree")
async def get_skill_tree():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
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
        return {"error": "游戏未初始化"}
    slot_id = req.get("slot_id", "")
    result = engine.skill_tree.unlock_skill(slot_id)
    return result


@router.get("/quests")
async def get_quests():
    engine = get_engine()
    if not engine or not engine.quest_system:
        return {"error": "游戏未初始化"}
    return {"active": engine.quest_system.get_active_quests()}


@router.get("/reputation")
async def get_reputation():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
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
        return {"error": "游戏未初始化"}
    return engine.get_context_debug()
