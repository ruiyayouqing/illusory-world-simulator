"""
[v12] 小说人物扮演路由组。

端点：
  POST /api/novel-roleplay/import      上传小说文本并处理
  GET  /api/novel-roleplay/status      查询处理进度
  GET  /api/novel-roleplay/characters   获取主要角色列表
  GET  /api/novel-roleplay/timeline     获取时间轴/关键事件
  POST /api/novel-roleplay/enter        选择角色+时间点进入游戏
  GET  /api/novel-roleplay/stats        获取整体状态
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .deps import get_engine, get_meta_db, load_config, _engine_switch_lock, set_engine, BASE_DIR

logger = logging.getLogger("chronoverse")
router = APIRouter(prefix="/api")

# 全局 NovelRoleplayService 实例（单例）
_roleplay_service = None


def _get_roleplay_service():
    """获取或创建 NovelRoleplayService 单例"""
    global _roleplay_service
    if _roleplay_service is None:
        from modules.novel_roleplay_service import NovelRoleplayService
        config = load_config()

        # 注意：deps.load_config() 返回原始 config（未解密 enc: 前缀的 api_key），
        # 这里统一解密一次供后续 LLM/embedding 创建使用
        try:
            from modules.security import decrypt_config_keys
            config_decrypted = decrypt_config_keys(config)
        except Exception as e:
            logger.warning("解密 config 失败，使用原始配置: %s", e)
            config_decrypted = config

        # 优先复用已初始化的 GameEngine
        engine = get_engine()
        llm = None
        embedding_func = None

        if engine and engine.main_llm:
            llm = engine.main_llm
            if engine.memory_store and hasattr(engine.memory_store, 'embedding_func'):
                embedding_func = engine.memory_store.embedding_func

        # 如果 engine 不可用，从 config.json 创建独立 LLM
        if llm is None:
            # 优先使用 active_llm_profile 指定的 profile（如果存在）
            llm_cfg = config_decrypted.get("llm", {})
            active_profile_id = config_decrypted.get("active_llm_profile", "")
            if active_profile_id and active_profile_id in config_decrypted.get("llm_profiles", {}):
                profile = config_decrypted["llm_profiles"][active_profile_id]
                llm_cfg = {**llm_cfg, **profile}  # profile 覆盖默认 llm 字段
                logger.info("NovelRoleplayService 使用 active_llm_profile=%s", active_profile_id)

            api_key = llm_cfg.get("api_key", "")
            base_url = llm_cfg.get("base_url", "")
            model_name = llm_cfg.get("model_name", "")
            if api_key and base_url and model_name:
                from modules.llm.mimo_llm import MimoLLM
                try:
                    llm = MimoLLM(
                        api_key=api_key,
                        base_url=base_url,
                        model_name=model_name,
                        default_max_tokens=llm_cfg.get("max_tokens", 0),
                        preflight_check=False,  # 跳过启动时连接测试
                    )
                    # api_key 脱敏日志：只显示前4和后4字符
                    masked = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "***"
                    logger.info("NovelRoleplayService 从 config 创建独立 LLM: %s @ %s (key=%s)",
                                model_name, base_url, masked)
                except Exception as e:
                    logger.warning("创建独立 LLM 失败: %s", e)

        if embedding_func is None:
            emb_cfg = config_decrypted.get("embedding", {})
            if emb_cfg.get("api_key") and emb_cfg.get("base_url"):
                try:
                    from modules.db.embedding_function import create_embedding_function
                    embedding_func = create_embedding_function(config_decrypted)
                    logger.info("NovelRoleplayService 从 config 创建 embedding 函数")
                except Exception as e:
                    logger.warning("创建 embedding 函数失败: %s", e)

        _roleplay_service = NovelRoleplayService(
            llm=llm,
            embedding_func=embedding_func,
        )
    return _roleplay_service


def _reset_roleplay_service():
    """重置服务实例（新小说导入时调用）"""
    global _roleplay_service
    _roleplay_service = None


# ── 请求模型 ──────────────────────────────────────────────

class NovelImportRequest(BaseModel):
    text: str = Field(..., description="小说全文")
    novel_name: str = Field(default="", description="小说名称（可选）")


class EnterRoleplayRequest(BaseModel):
    character_name: str = Field(..., description="选择的角色名")
    timeline_id: str = Field(..., description="选择的时间节点ID")
    api_key: str = Field(default="", description="LLM API Key")
    base_url: str = Field(default="")
    model_name: str = Field(default="")


class DeepProcessRequest(BaseModel):
    chapter_index: int = Field(default=-1, description="选定的章节序号（0-based）。与 char_position 二选一")
    character_name: str = Field(default="", description="玩家选定的角色名（聚焦提取）")
    char_position: int = Field(default=-1, description="直接指定字符位置（方案B/C用）。与 chapter_index 二选一")


class LocateTextRequest(BaseModel):
    snippet: str = Field(..., description="玩家粘贴的小说文字片段（至少10字）")


# ── 路由 ──────────────────────────────────────────────────

@router.post("/novel-roleplay/import")
async def import_novel(req: NovelImportRequest):
    """上传小说文本，开始异步处理（分块+建图谱+时间轴）"""
    if not req.text or len(req.text) < 100:
        raise HTTPException(status_code=400, detail="文本太短，至少需要100字")

    service = _get_roleplay_service()

    # 如果上次导入未完成，先重置
    if service.get_import_status()["state"] == "processing":
        raise HTTPException(status_code=409, detail="正在处理上一篇小说，请等待完成")

    # 重置服务
    _reset_roleplay_service()
    service = _get_roleplay_service()

    # 异步启动导入（不阻塞响应）
    asyncio.create_task(service.import_novel(req.text, req.novel_name))

    return {
        "success": True,
        "message": "小说导入已开始，请轮询 /api/novel-roleplay/status 查看进度",
        "total_chars": len(req.text),
    }


@router.get("/novel-roleplay/status")
async def get_import_status():
    """查询小说导入进度"""
    service = _get_roleplay_service()
    return service.get_import_status()


@router.get("/novel-roleplay/characters")
async def get_characters(top_n: int = 12):
    """获取主要角色列表（需先完成导入）"""
    service = _get_roleplay_service()
    if not service.is_ready():
        status = service.get_import_status()
        raise HTTPException(
            status_code=400,
            detail=f"小说尚未导入完成，当前状态: {status['state']}"
        )
    characters = service.get_characters(top_n=top_n)
    return {"characters": characters, "count": len(characters)}


@router.get("/novel-roleplay/timeline")
async def get_timeline():
    """获取时间轴和关键事件（需先完成导入）"""
    service = _get_roleplay_service()
    if not service.is_ready():
        raise HTTPException(status_code=400, detail="小说尚未导入完成")

    key_events = service.get_key_events()
    full_timeline = service.get_timeline_summary()
    return {
        "key_events": key_events,
        "full_timeline": full_timeline,
        "total_nodes": len(full_timeline),
    }


@router.get("/novel-roleplay/chapters")
async def get_chapters():
    """[v12+] 获取章节列表（快速导入后即可调用）"""
    service = _get_roleplay_service()
    chapters = service.get_chapters()
    return {"chapters": chapters, "count": len(chapters)}


@router.post("/novel-roleplay/deep-process")
async def deep_process(req: DeepProcessRequest):
    """[v12+] 玩家选定章节后，异步深度处理该章节前的内容

    支持两种入参（二选一）：
    - chapter_index: 通过章节序号定位（默认方式）
    - char_position: 直接指定字符位置（方案B按进度、方案C粘贴文字定位）
    """
    service = _get_roleplay_service()
    if not service._novel_text:
        raise HTTPException(status_code=400, detail="尚未导入小说")

    # 校验：至少提供一种定位方式
    if req.chapter_index < 0 and req.char_position < 0:
        raise HTTPException(
            status_code=400,
            detail="必须提供 chapter_index 或 char_position"
        )

    # 如果正在处理，拒绝
    if service.get_import_status()["state"] == "processing":
        raise HTTPException(status_code=409, detail="正在处理中，请等待完成")

    # 异步启动深度处理
    asyncio.create_task(
        service.deep_process_before_chapter(
            chapter_index=req.chapter_index,
            character_name=req.character_name,
            char_position=req.char_position,
        )
    )

    # 描述处理范围
    if req.char_position >= 0:
        range_desc = f"前 {req.char_position} 字"
    else:
        range_desc = f"前 {req.chapter_index + 1} 章"

    return {
        "success": True,
        "message": f"开始深度处理{range_desc}内容，请轮询 /status",
        "chapter_index": req.chapter_index,
        "char_position": req.char_position,
    }


@router.post("/novel-roleplay/locate-text")
async def locate_text(req: LocateTextRequest):
    """[v12+] 玩家粘贴一段小说文字，定位其在全文中的位置

    用于"粘贴文字定位"功能：玩家粘贴记得的剧情文字，
    系统找到位置后，前端可调用 /deep-process 传入 char_position。
    """
    service = _get_roleplay_service()
    if not service._novel_text:
        raise HTTPException(status_code=400, detail="尚未导入小说")

    result = service.locate_text(req.snippet)
    return result


@router.post("/novel-roleplay/enter")
async def enter_roleplay(req: EnterRoleplayRequest):
    """选择角色和时间点，进入游戏"""
    service = _get_roleplay_service()
    if not service.is_ready():
        raise HTTPException(status_code=400, detail="小说尚未导入完成")

    async with _engine_switch_lock:
        engine = get_engine()
        # [Bug] 小说扮演流程独立于 /create 和 /load，进入时 engine 可能尚未初始化。
        #       原先直接返回 503，导致用户必须先去"加载存档"或"创建世界"才能进入小说扮演。
        #       修复：engine 为 None 时自动创建新实例并 set_engine，与 /load 流程一致。
        if not engine:
            from modules.game_engine import GameEngine
            engine = GameEngine(str(BASE_DIR / "saves"))
            logger.info("Novel roleplay: auto-creating new GameEngine (was None)")

        # 初始化LLM（必须在 create_new_game 之前调用）
        # 如果请求中提供了API配置，使用请求值；否则从配置文件读取
        config = load_config()
        api_key = req.api_key or config.get("llm", {}).get("api_key", "")
        base_url = req.base_url or config.get("llm", {}).get("base_url", "")
        model_name = req.model_name or config.get("llm", {}).get("model_name", "")
        
        if not engine.llm:
            engine.init_llm(api_key, base_url, model_name)

        # [Bug] 自动创建的 engine 必须 set_engine 后，service.enter_roleplay 内部
        #       才能通过 get_engine() / 全局引用访问到它（部分子系统使用全局 engine）
        if get_engine() is not engine:
            set_engine(engine)

        result = await service.enter_roleplay(
            character_name=req.character_name,
            timeline_id=req.timeline_id,
            engine=engine,
        )

        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])

        # 获取游戏状态
        game_state = engine.get_game_state()
        result["game_state"] = game_state
        return result


@router.get("/novel-roleplay/stats")
async def get_stats():
    """获取小说人物扮演整体状态"""
    service = _get_roleplay_service()
    return service.get_stats()
