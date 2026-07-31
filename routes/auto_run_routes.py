"""
[v1.2] 自主运行路由 — 让世界自我演化 N 天，围绕主角汇总成小说章节。

端点：
  POST /api/auto-run/start   启动自主运行（同步阻塞，返回完整章节）
  GET  /api/auto-run/chapters 列出自主运行生成的章节
  GET  /api/auto-run/chapters/{filename}  读取指定章节
"""
from __future__ import annotations
import asyncio
import logging
import re
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .deps import BASE_DIR, get_engine
from modules.prompt_utils import resolve_location_name  # [Bug] location code → display name

logger = logging.getLogger("chronoverse")
router = APIRouter(prefix="/api")

# [v9] 安全文件名校验，防止路径遍历
_SAFE_FILENAME = re.compile(r'^[a-zA-Z0-9_\-\.]+\.json$')


class AutoRunRequest(BaseModel):
    """自主运行请求"""
    days: int = Field(..., ge=1, le=365, description="要推进的天数（1-365）")
    options: dict = Field(default_factory=dict, description="预留扩展项")


@router.post("/auto-run/start")
async def start_auto_run(req: AutoRunRequest):
    """启动自主运行 N 天。

    同步阻塞执行（用户选择"仅最终汇总"模式），前端显示 loading 即可。
    运行前会自动存档（auto_before_autorun），异常可回滚。
    """
    engine = get_engine()
    if not engine or not engine.world_state or not engine.player_state:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    try:
        # 使用并发锁防止运行期间玩家同时操作
        async with engine._game_lock:
            result = await asyncio.to_thread(
                engine.auto_run_days, req.days, req.options
            )
        if result.get("error"):
            return {"error": result["error"]}
        # 推送一条系统消息到叙事历史（供下次正常章节生成时引用）
        try:
            engine.narrative_history.append({
                "type": "autorun",
                "day": result.get("to_day", 0),
                "time": "",
                "text": f"【自主运行 · 第{result.get('from_day')}天~第{result.get('to_day')}天】\n"
                        + result.get("chapter", ""),
                "days_advanced": result.get("days_advanced", 0),
                "events_count": result.get("events_count", 0),
                "interactions_count": result.get("interactions_count", 0),
            })
            engine._narrative_compressed = True
            engine._persisted_narrative_count = len(engine.narrative_history)
        except Exception as e:
            logger.warning("[AutoRun] 写入叙事历史失败: %s", e)

        # [Bug] 自主运行推进了 N 天，世界/主角状态已大幅变化，
        # 必须基于最新状态重新生成行动选项，否则前端仍显示运行前的旧选项，
        # 玩家点选后剧情会从旧分支接续，导致剧情断裂。
        # 处理方式与 /slot/load、/hundred-book/rewind 保持一致。
        initial_options = []
        if engine.option_engine and engine.player_state and engine.world_state:
            try:
                scene = (
                    f"第{engine.world_state.current_day}天 {engine.world_state.current_time}，"
                    f"你在{resolve_location_name(engine.player_state.location, engine.world_state)}"
                )
                initial_options = await asyncio.to_thread(
                    engine.option_engine.generate_options,
                    scene, engine.player_state, engine.world_state
                )
            except Exception as e:
                logger.warning("[AutoRun] 生成新选项失败: %s", e)
                initial_options = engine.option_engine._fallback_options(engine.player_state)
        result["initial_options"] = initial_options
        return result
    except Exception as e:
        logger.error("Auto run failed: %s", e, exc_info=True)
        return {"error": f"自主运行失败: {e}"}


@router.get("/auto-run/chapters")
async def list_autorun_chapters():
    """列出自主运行生成的章节"""
    engine = get_engine()
    if not engine or not engine.current_world_id:
        return {"chapters": []}
    narrative_dir = BASE_DIR / "saves" / engine.current_world_id / "narrative"
    if not narrative_dir.exists():
        return {"chapters": []}
    chapters = []
    for f in sorted(narrative_dir.glob("chapter_autorun_*.json")):
        try:
            import json
            data = json.loads(f.read_text(encoding="utf-8"))
            chapters.append({
                "file": f.name,
                "from_day": data.get("from_day", 0),
                "to_day": data.get("to_day", 0),
                "days": data.get("days", 0),
                "preview": (data.get("chapter") or "")[:100],
                "created_at": data.get("created_at", ""),
            })
        except Exception as e:
            logger.warning("Failed to parse autorun chapter %s: %s", f.name, e)
    # 按时间倒序（最新在前）
    chapters.sort(key=lambda c: c.get("created_at", ""), reverse=True)
    return {"chapters": chapters}


@router.get("/auto-run/chapters/{filename}")
async def read_autorun_chapter(filename: str):
    """读取指定自主运行章节"""
    engine = get_engine()
    if not engine or not engine.current_world_id:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    if not _SAFE_FILENAME.match(filename) or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    narrative_dir = (BASE_DIR / "saves" / engine.current_world_id / "narrative").resolve()
    chapter_file = (BASE_DIR / "saves" / engine.current_world_id / "narrative" / filename).resolve()
    try:
        chapter_file.relative_to(narrative_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not chapter_file.exists():
        raise HTTPException(status_code=404, detail="章节不存在")
    import json
    data = json.loads(chapter_file.read_text(encoding="utf-8"))
    return {"chapter": data}
