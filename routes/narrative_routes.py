from __future__ import annotations
import json
import logging
import re
from fastapi import APIRouter, HTTPException

from .deps import BASE_DIR, get_engine, get_meta_db

logger = logging.getLogger("chronoverse")
router = APIRouter(prefix="/api")

# [v9] 安全文件名校验，防止路径遍历
SAFE_FILENAME = re.compile(r'^[a-zA-Z0-9_\-\.]+\.json$')


@router.get("/novel/preview")
async def novel_preview():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    recent = engine.narrative_history[engine.last_novel_checkpoint:]
    return {
        "entries_count": len(recent),
        "from_day": recent[0].get("day", 0) if recent else 0,
        "to_day": recent[-1].get("day", 0) if recent else 0,
        "has_content": len(recent) > 0,
    }


@router.post("/novel/generate")
async def generate_novel():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    result = engine.generate_novel_chapter()
    engine.save_game("auto")
    return result


@router.get("/novel/chapters")
async def list_novel_chapters():
    engine = get_engine()
    if not engine or not engine.current_world_id:
        return {"chapters": []}
    narrative_dir = BASE_DIR / "saves" / engine.current_world_id / "narrative"
    if not narrative_dir.exists():
        return {"chapters": []}
    chapters = []
    for f in sorted(narrative_dir.glob("chapter_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            chapters.append({
                "file": f.name,
                "from_day": data.get("from_day", 0),
                "to_day": data.get("to_day", 0),
                "entries_count": data.get("entries_count", 0),
                "preview": data.get("chapter", "")[:100],
            })
        except Exception as e:
            logger.warning("Failed to parse chapter %s: %s", f.name, e)
    return {"chapters": chapters}


@router.get("/novel/chapters/{filename}")
async def read_novel_chapter(filename: str):
    engine = get_engine()
    if not engine or not engine.current_world_id:
        return {"error": "游戏未初始化"}
    # [v9] 校验文件名，防止路径遍历
    if not SAFE_FILENAME.match(filename) or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    narrative_dir = (BASE_DIR / "saves" / engine.current_world_id / "narrative").resolve()
    chapter_file = (BASE_DIR / "saves" / engine.current_world_id / "narrative" / filename).resolve()
    try:
        chapter_file.relative_to(narrative_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not chapter_file.exists():
        return {"error": "章节不存在"}
    data = json.loads(chapter_file.read_text(encoding="utf-8"))
    return {"chapter": data}


@router.get("/narrative/search")
async def search_narrative(q: str = ""):
    engine = get_engine()
    if not engine or not engine.current_world_id:
        return {"error": "游戏未初始化"}
    if not q:
        return {"results": []}
    try:
        db = get_meta_db()
        results = db.search_narrative(engine.current_world_id, q)
        return {"results": results}
    except Exception as e:
        logger.error("Narrative search failed: %s", e)
        return {"results": [], "error": str(e)}


@router.get("/narrative/stats")
async def narrative_stats():
    engine = get_engine()
    if not engine or not engine.current_world_id:
        return {"error": "游戏未初始化"}
    try:
        db = get_meta_db()
        return {"stats": db.get_stats(engine.current_world_id)}
    except Exception as e:
        return {"stats": {}, "error": str(e)}


@router.get("/world-evolution")
async def get_world_evolution():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    evolution = engine.generate_world_evolution()
    return {"evolution": evolution}


@router.get("/return-narrative")
async def get_return_narrative():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    narrative = engine.generate_return_narrative()
    return {"narrative": narrative}


@router.get("/rag-context")
async def get_rag_context():
    engine = get_engine()
    if not engine or not engine.rag_historical:
        return {"context": "", "injected_count": 0}
    era = (engine.world_state.era_name or engine.world_state.world_name) if engine.world_state else ""
    context = engine.rag_historical.generate_historical_context(
        engine.player_state, engine.world_state, ""
    ) if engine.player_state and engine.world_state else ""
    return {"context": context, "injected_count": engine.rag_historical.get_injected_count()}
