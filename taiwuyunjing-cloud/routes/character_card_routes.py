from __future__ import annotations
import json
import logging
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel, Field

from .deps import get_engine

logger = logging.getLogger("chronoverse")
router = APIRouter(prefix="/api")


class ImportCardResponse(BaseModel):
    status: str
    npc_id: str = ""
    name: str = ""


@router.get("/npc/{npc_id}/card")
async def get_character_card(npc_id: str):
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=400, detail="游戏未初始化")

    npc = engine.npc_states.get(npc_id)
    if not npc:
        raise HTTPException(status_code=404, detail="NPC不存在")

    from modules.character_card import CharacterCard
    card = CharacterCard.from_npc_state(npc, world_state=engine.world_state)
    return card


@router.post("/npc/{npc_id}/card/export")
async def export_character_card(npc_id: str):
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=400, detail="游戏未初始化")

    npc = engine.npc_states.get(npc_id)
    if not npc:
        raise HTTPException(status_code=404, detail="NPC不存在")

    from modules.character_card import CharacterCard
    card = CharacterCard.from_npc_state(npc, world_state=engine.world_state)
    return card


@router.post("/npc/card/import", response_model=ImportCardResponse)
async def import_character_card(file: UploadFile = File(...)):
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=400, detail="游戏未初始化")

    try:
        content = await file.read()
        card_data = json.loads(content.decode("utf-8"))

        from modules.character_card import CharacterCard
        npc = CharacterCard.to_npc_state(card_data)

        npc_id = f"npc_{uuid.uuid4().hex[:8]}"
        npc.agent_id = npc_id

        if not npc.current_location and engine.player_state:
            npc.current_location = engine.player_state.location

        engine.npc_states[npc_id] = npc

        if engine.lorebook:
            engine.lorebook.update_npc_entry(npc.name, npc.get_identity_summary())

        engine.save_manager.save_state(
            engine.current_world_id,
            engine.meta,
            engine.world_state,
            engine.player_state,
            engine.npc_states,
            engine.memory,
            engine.lorebook,
            engine.foreshadow,
        )

        return ImportCardResponse(
            status="ok",
            npc_id=npc_id,
            name=npc.name,
        )
    except Exception as e:
        logger.error("导入角色卡失败: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/npc/{npc_id}/card/export-st")
async def export_character_card_st(npc_id: str):
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=400, detail="游戏未初始化")

    npc = engine.npc_states.get(npc_id)
    if not npc:
        raise HTTPException(status_code=404, detail="NPC不存在")

    from modules.character_card import CharacterCard
    card = CharacterCard.to_sillytavern_card(npc, world_state=engine.world_state)
    return card
