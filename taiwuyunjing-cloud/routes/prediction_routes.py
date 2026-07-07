"""
[v12] NPC行动智能推演 API路由
"""
from __future__ import annotations
import logging
from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional

from .deps import get_engine

logger = logging.getLogger("chronoverse.prediction")
router = APIRouter(prefix="/api")


class StartPredictionRequest(BaseModel):
    source_mode: str = "auto"
    max_npcs: int = 50


class ApplyPredictionRequest(BaseModel):
    npc_ids: list[str] = Field(default_factory=list)


@router.post("/npc-prediction/start")
async def start_prediction(req: StartPredictionRequest):
    engine = get_engine()
    if not engine or not engine.player_state:
        return {"error": "游戏未初始化"}
    if not engine.llm:
        return {"error": "LLM未配置"}

    try:
        from modules.npc_prediction import NpcPredictionEngine
        predictor = NpcPredictionEngine(
            llm=engine.llm,
            graph_rag=engine.graph_rag,
            npc_registry=engine.npc_registry,
            character_state_manager=engine.character_state_manager,
        )
        engine._lazy_npc_prediction = predictor

        report = predictor.predict_all_npcs(
            source_mode=req.source_mode,
            max_npcs=req.max_npcs,
            engine=engine,
        )
        return {"status": "ok", "report": report.to_dict()}
    except Exception as e:
        logger.error("[Prediction] 推演失败: %s", e, exc_info=True)
        return {"error": f"推演失败: {str(e)}"}


@router.get("/npc-prediction/report")
async def get_prediction_report():
    engine = get_engine()
    if not engine or not getattr(engine, '_lazy_npc_prediction', None):
        return {"error": "无推演报告"}
    predictor = engine._lazy_npc_prediction
    report = predictor.get_current_report()
    if not report:
        return {"error": "无推演报告"}
    return {"report": report.to_dict()}


@router.post("/npc-prediction/apply")
async def apply_prediction(req: ApplyPredictionRequest):
    engine = get_engine()
    if not engine or not getattr(engine, '_lazy_npc_prediction', None):
        return {"error": "无推演报告"}
    predictor = engine._lazy_npc_prediction
    npc_ids = req.npc_ids if req.npc_ids else None
    result = predictor.apply_predictions(npc_ids=npc_ids, engine=engine)
    return result
