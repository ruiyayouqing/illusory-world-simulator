from __future__ import annotations
import json
import logging
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel, Field

from .deps import get_engine

logger = logging.getLogger("chronoverse")
router = APIRouter(prefix="/api")


class LorebookEntryModel(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    content: str = ""
    entry_type: str = "keyword"
    priority: int = 0
    enabled: bool = True
    position: str = "before_main"
    constant: bool = False
    comment: str = ""


@router.get("/lorebook")
async def get_lorebook():
    engine = get_engine()
    if not engine or not engine.lorebook:
        return {"entries": [], "global_entries": []}
    data = engine.lorebook.to_dict()
    return data


@router.post("/lorebook/entry")
async def add_lorebook_entry(entry: LorebookEntryModel):
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=400, detail="游戏未初始化")
    if not engine.lorebook:
        from modules.lorebook import Lorebook
        engine.lorebook = Lorebook()

    uid = engine.lorebook.add_entry(
        keywords=entry.keywords,
        content=entry.content,
        entry_type=entry.entry_type,
        priority=entry.priority,
        enabled=entry.enabled,
        position=entry.position,
        constant=entry.constant,
        comment=entry.comment,
    )
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
    return {"uid": uid, "status": "ok"}


@router.post("/lorebook/entry/{uid}")
async def update_lorebook_entry(uid: str, entry: LorebookEntryModel):
    engine = get_engine()
    if not engine or not engine.lorebook:
        raise HTTPException(status_code=400, detail="游戏未初始化")
    engine.lorebook.update_entry(uid, **entry.model_dump())
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
    return {"status": "ok"}


@router.delete("/lorebook/entry/{uid}")
async def delete_lorebook_entry(uid: str):
    engine = get_engine()
    if not engine or not engine.lorebook:
        raise HTTPException(status_code=400, detail="游戏未初始化")
    engine.lorebook.remove_entry(uid)
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
    return {"status": "ok"}


@router.post("/lorebook/import")
async def import_lorebook(file: UploadFile = File(...)):
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=400, detail="游戏未初始化")
    if not engine.lorebook:
        from modules.lorebook import Lorebook
        engine.lorebook = Lorebook()

    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))

        if "entries" in data and isinstance(data["entries"], dict):
            engine.lorebook.from_dict(data)
            count = len(data.get("entries", {}))
        elif isinstance(data, list):
            count = 0
            for item in data:
                keywords = item.get("keys", item.get("keywords", []))
                content_text = item.get("content", item.get("text", ""))
                if keywords and content_text:
                    engine.lorebook.add_entry(
                        keywords=keywords if isinstance(keywords, list) else [str(keywords)],
                        content=content_text,
                        entry_type=item.get("type", "keyword"),
                        priority=item.get("priority", item.get("order", 100)),
                        enabled=item.get("enabled", True),
                        constant=item.get("constant", item.get("is_constant", False)),
                        position=item.get("position", "before_main"),
                        comment=item.get("comment", item.get("name", "")),
                    )
                    count += 1
        else:
            raise ValueError("不支持的世界书格式")

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
        return {"status": "ok", "imported": count}
    except Exception as e:
        logger.error("导入世界书失败: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/lorebook/export")
async def export_lorebook():
    engine = get_engine()
    if not engine or not engine.lorebook:
        return {"entries": {}, "global_entries": []}
    return engine.lorebook.to_dict()


@router.post("/lorebook/import-world-info")
async def import_world_info(file: UploadFile = File(...)):
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=400, detail="游戏未初始化")
    if not engine.lorebook:
        from modules.lorebook import Lorebook
        engine.lorebook = Lorebook()

    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))

        from modules.world_info_importer import WorldInfoBook
        book = WorldInfoBook.from_dict(data)
        count = engine.lorebook.import_from_world_info(book)

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
        return {"status": "ok", "imported": count}
    except Exception as e:
        logger.error("导入World Info失败: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
