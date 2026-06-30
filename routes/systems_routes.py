from __future__ import annotations
import json
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from .deps import BASE_DIR, get_engine
from modules.security import decrypt_config_keys

logger = logging.getLogger("chronoverse")
router = APIRouter(prefix="/api")

# [v9] 安全校验：NPC ID 仅允许字母、数字、下划线、连字符和中文
SAFE_NPC_ID = re.compile(r'^[a-zA-Z0-9_\u4e00-\u9fff]+$')


class DeathChoiceRequest(BaseModel):
    choice: str


class ImageRequest(BaseModel):
    prompt_override: str = ""


@router.get("/market")
async def get_market_report():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    report = engine.get_market_report()
    return {"report": report}


@router.get("/butterfly")
async def get_butterfly_summary():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return {"summary": engine.get_butterfly_summary()}


@router.get("/favor-events")
async def check_favor_events():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return {"events": engine.check_favor_events()}


@router.get("/destiny-regret")
async def check_destiny_regret():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    regret = engine.check_destiny_regret()
    missed = engine.get_missed_summary() if engine.destiny_regret else ""
    irreversible = engine.get_irreversible_summary() if engine.destiny_regret else ""
    return {"regret": regret, "missed": missed, "irreversible": irreversible}


@router.get("/faction-wars")
async def get_faction_wars():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    status = engine.get_faction_wars() if engine.faction_wars else ""
    history = engine.get_war_history() if engine.faction_wars else ""
    return {"status": status, "history": history}


@router.get("/death-stats")
async def get_death_stats():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return {"stats": engine.get_death_stats()}


@router.post("/death-choice")
async def handle_death_choice(req: DeathChoiceRequest):
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    result = engine.handle_death_choice(req.choice)
    return result


@router.post("/suicide-confirm")
async def suicide_confirm():
    engine = get_engine()
    if not engine or not engine.death_system or not engine.player_state:
        return {"error": "游戏未初始化"}
    if not engine.world_state:
        return {"error": "世界状态未初始化"}
    # [Bug] 加 _game_lock 防止与 /input 并发竞态
    async with engine._game_lock:
        death = engine.death_system.trigger_suicide(engine.player_state, engine.world_state)
        if engine.memoir:
            engine.memoir.record_death(engine.player_state, death["cause"],
                                       engine.world_state.current_day, engine.world_state)
        # [Bug] 触发 on_death 钩子，与自然死亡路径保持一致
        engine.trigger_hook("on_death",
                            player_state=engine.player_state,
                            world_state=engine.world_state,
                            cause=death["cause"],
                            is_suicide=True)
        engine.save_game("auto")
    return {"death": death}


@router.get("/hundred-life-book")
async def get_hundred_life_book():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    book = engine.hundred_life_book
    return {
        "total_lives": book.get_total_lives(),
        "sealed_lives": book.get_sealed_lives(),
        "current_life": book.current_life,
        "inherited_tags": book.get_inherited_tags(),
        "previews": book.get_life_previews(),
    }


@router.get("/hundred-life-book/narrative")
async def get_book_narrative():
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return {"narrative": engine.hundred_life_book.generate_book_narrative()}


@router.post("/generate-image")
async def generate_image(req: ImageRequest):
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    if not engine.visual_engine:
        return {"image": {"generated": False, "error": "图像引擎未初始化"}}
    config_path = BASE_DIR / "config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config = decrypt_config_keys(config)
        img_cfg = config.get("image", {})
        img_key = img_cfg.get("api_key", "")
        img_url = img_cfg.get("base_url", "")
        img_model = img_cfg.get("model_name", "")
        if img_key:
            engine.visual_engine.set_api_key(img_key)
        if img_url:
            engine.visual_engine.set_api_url(img_url)
        if img_model:
            engine.visual_engine.set_model(img_model)

    if req.prompt_override:
        world_type = engine.world_state.world_type if engine.world_state else "custom"
        from modules.visual_engine import SCENE_STYLES
        style = SCENE_STYLES.get(world_type, SCENE_STYLES["custom"])
        # [Bug] 使用 location_name（如"汴京城"）而非 location code（如"bianjing"）
        loc_code = engine.player_state.location if engine.player_state else ""
        location = loc_code
        if engine.world_state and hasattr(engine.world_state, 'locations') and loc_code in engine.world_state.locations:
            loc_obj = engine.world_state.locations[loc_code]
            if isinstance(loc_obj, dict):
                location = loc_obj.get('location_name') or loc_obj.get('name') or loc_code
            elif hasattr(loc_obj, 'location_name'):
                location = loc_obj.location_name or loc_code
            elif hasattr(loc_obj, 'name'):
                location = loc_obj.name or loc_code
        weather = engine.world_state.weather if engine.world_state else ""
        narrative = req.prompt_override[:400]
        if engine.narrative_history:
            last_n = engine.narrative_history[-1]
            if last_n.get("text"):
                narrative = last_n["text"][:400]
        prompt = f"""{narrative}.
Location: {location}. Weather: {weather}.
Style: {style}, masterpiece quality, detailed, 8k resolution."""
        result = engine.visual_engine.generate_image(prompt)
    else:
        result = engine.generate_scene_image("")

    return {"image": result}


@router.get("/influence-graph")
async def get_influence_graph():
    engine = get_engine()
    if not engine or not engine.influence_network:
        return {"nodes": [], "edges": []}
    npc_names = {}
    if engine.npc_states:
        for nid, npc in engine.npc_states.items():
            npc_names[nid] = npc.name
    # [Bug] 传入玩家名字，避免显示英文 "player"
    player_name = engine.player_state.name if engine.player_state else "玩家"
    return engine.influence_network.get_graph_data(npc_names=npc_names, player_name=player_name)


@router.get("/influence-events")
async def get_influence_events():
    engine = get_engine()
    if not engine or not engine.influence_network:
        return {"events": []}
    return {"events": engine.influence_network.get_recent_events(20)}


@router.get("/map-data")
async def get_map_data():
    """世界地图数据"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return engine.get_map_data()


# ── v7 新增端点 ──────────────────────────────────────────

class GraphQueryRequest(BaseModel):
    question: str = ""

class CharCardImportRequest(BaseModel):
    path: str = ""

@router.get("/graph-rag")
async def get_graph_rag():
    """获取知识图谱可视化数据"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return engine.get_graph_visualization()

@router.post("/graph-rag/query")
async def query_graph_rag(req: GraphQueryRequest):
    """查询知识图谱"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return engine.query_graph_rag(req.question)

@router.get("/ammm-facts")
async def get_amm_facts():
    """获取 AMM 经济系统的统计特征（厚尾分布等）"""
    engine = get_engine()
    if not engine or not engine.economy_system:
        return {"error": "经济系统未初始化"}
    return engine.economy_system.get_stylized_facts()

@router.post("/character-card/export/{npc_id}")
async def export_character_card(npc_id: str):
    """导出NPC角色卡"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    # [v9] 校验 npc_id 格式，防止路径注入
    if not SAFE_NPC_ID.match(npc_id):
        raise HTTPException(status_code=400, detail="Invalid NPC ID")
    # [v9] 使用 uuid 生成文件名，避免文件名注入
    safe_name = f"{uuid.uuid4().hex[:8]}_card.json"
    path = os.path.join(tempfile.gettempdir(), safe_name)
    ok = engine.export_character_card(npc_id, path)
    if ok:
        return {"success": True, "path": path}
    return {"error": "导出失败"}

@router.post("/character-card/import")
async def import_character_card(req: CharCardImportRequest):
    """导入角色卡"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    # [v9] 限制导入路径必须在 saves 目录内，防止任意文件读取
    try:
        req_path = Path(req.path).resolve()
        saves_dir = (BASE_DIR / "saves").resolve()
        req_path.relative_to(saves_dir)
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="Invalid import path: must be within saves directory")
    if not req_path.exists():
        raise HTTPException(status_code=404, detail="Import file not found")
    return engine.import_character_card(str(req_path))


# ── [v10] 新增端点 ──────────────────────────────────────────

class ButterflyApprovalRequest(BaseModel):
    approval_id: str
    decision: str = "approve"  # approve / reject / modify


@router.get("/v10/dashboard")
async def get_v10_dashboard():
    """[v10] 获取所有 v10 新系统的概览面板"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return engine.get_v10_dashboard()


@router.get("/v10/narrative-review")
async def get_narrative_review():
    """[v10] 获取叙事回顾结果和质量趋势"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return engine.get_narrative_review()


@router.get("/v10/task-board")
async def get_task_board():
    """[v10] 获取世界任务板状态"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return engine.get_task_board()


@router.get("/v10/butterfly-approvals")
async def get_butterfly_approvals():
    """[v10] 获取待审批的蝴蝶效应"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return {"approvals": engine.get_butterfly_approvals()}


@router.post("/v10/butterfly-approve")
async def approve_butterfly_effect(req: ButterflyApprovalRequest):
    """[v10] 审批蝴蝶效应后果"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return engine.approve_butterfly_effect(req.approval_id, req.decision)


@router.get("/v10/curator-stats")
async def get_curator_stats():
    """[v10] 获取记忆 Curator 统计"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return engine.get_curator_stats()


@router.get("/v10/procedural-memory")
async def get_procedural_memory_stats():
    """[v10] 获取 NPC 程序性记忆统计"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return engine.get_npc_procedural_stats()


class ApprovalGateConfigRequest(BaseModel):
    enabled: bool = False
    threshold: float = 7.0


@router.post("/v10/approval-gate/config")
async def configure_approval_gate(req: ApprovalGateConfigRequest):
    """[v10] 配置蝴蝶效应审批门"""
    engine = get_engine()
    if not engine or not engine.butterfly:
        return {"error": "游戏未初始化"}
    engine.butterfly.approval_gate_enabled = req.enabled
    engine.butterfly.approval_threshold = req.threshold
    return {
        "success": True,
        "enabled": req.enabled,
        "threshold": req.threshold,
    }


# ── [v10+] 新增端点 ──────────────────────────────────────────

@router.get("/v10/foreshadow")
async def get_foreshadow_health():
    """[v10+] 获取伏笔健康报告"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return engine.get_foreshadow_health()


class ForeshadowReminderConfig(BaseModel):
    mode: str = "normal"  # "normal" 或 "silent"


@router.post("/v10/foreshadow/reminder")
async def set_foreshadow_reminder(req: ForeshadowReminderConfig):
    """[v10+] 设置伏笔提醒模式：normal=正常提醒, silent=静默运行"""
    engine = get_engine()
    if not engine or not engine.foreshadow_lifecycle:
        return {"error": "伏笔系统未初始化"}
    if req.mode not in ("normal", "silent"):
        return {"error": "mode 必须是 normal 或 silent"}
    engine.foreshadow_lifecycle.reminder_mode = req.mode
    return {"success": True, "mode": req.mode}


@router.get("/v10/continuity-audit")
async def get_continuity_audit():
    """[v10+] 获取多维度连续性审计结果"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    return engine.get_continuity_audit()


# ── [v10+] SillyTavern 世界书导入 ──────────────────────────

class WorldInfoJsonRequest(BaseModel):
    """世界书 JSON 导入请求（直接传入 SillyTavern 世界书 JSON）。"""
    data: dict  # SillyTavern World Info 完整 JSON


@router.post("/import-world-info")
async def import_world_info(file: UploadFile = File(...)):
    """[v10+] 导入 SillyTavern 世界书（文件上传方式）。

    接受 SillyTavern World Info 格式的 JSON 文件，
    解析后导入到当前游戏的 Lorebook 中。
    """
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    if not engine.lorebook:
        raise HTTPException(status_code=503, detail="世界书系统未初始化")

    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"JSON 解析失败: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件读取失败: {e}")

    try:
        from modules.world_info_importer import WorldInfoImporter
        importer = WorldInfoImporter()
        book = importer.import_from_dict(data)
        count = engine.lorebook.import_from_world_info(book)
        logger.info("World info imported via file: '%s', %d entries", book.name, count)
        return {
            "status": "success",
            "name": book.name,
            "entries": count,
        }
    except Exception as e:
        logger.error("World info import failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"导入失败: {e}")


@router.post("/import-world-info-json")
async def import_world_info_json(req: WorldInfoJsonRequest):
    """[v10+] 导入 SillyTavern 世界书（JSON 字符串方式）。

    接受 SillyTavern World Info 格式的 JSON 对象，
    解析后导入到当前游戏的 Lorebook 中。
    """
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    if not engine.lorebook:
        raise HTTPException(status_code=503, detail="世界书系统未初始化")

    try:
        from modules.world_info_importer import WorldInfoImporter
        importer = WorldInfoImporter()
        book = importer.import_from_dict(req.data)
        count = engine.lorebook.import_from_world_info(book)
        logger.info("World info imported via JSON: '%s', %d entries", book.name, count)
        return {
            "status": "success",
            "name": book.name,
            "entries": count,
        }
    except Exception as e:
        logger.error("World info import failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"导入失败: {e}")


@router.get("/lorebook")
async def get_lorebook():
    """[v10+] 获取当前 Lorebook 的所有条目（含世界书导入的条目）。"""
    engine = get_engine()
    if not engine:
        return {"error": "游戏未初始化"}
    if not engine.lorebook:
        return {"error": "世界书系统未初始化"}
    return engine.lorebook.to_dict()


# ── [v10++] MCP 工具协议层 API ──────────────────────────────

@router.get("/mcp/tools")
async def list_mcp_tools():
    """[v10++] 列出所有可用 MCP 工具。"""
    engine = get_engine()
    if not engine or not engine.mcp_registry:
        raise HTTPException(status_code=503, detail="MCP 不可用")
    return {"tools": engine.mcp_registry.list_tools()}


@router.post("/mcp/call")
async def call_mcp_tool(req: dict):
    """[v10++] 调用 MCP 工具。

    请求体格式：{"name": "工具名称", "arguments": {...}}
    """
    engine = get_engine()
    if not engine or not engine.mcp_registry:
        raise HTTPException(status_code=503, detail="MCP 不可用")
    name = req.get("name", "")
    arguments = req.get("arguments", {})
    if not name:
        raise HTTPException(status_code=400, detail="缺少工具名称 'name'")
    result = engine.mcp_registry.call(name, arguments)
    return result.to_dict()


@router.get("/mcp/stats")
async def mcp_stats():
    """[v10++] 获取 MCP 工具调用统计信息。"""
    engine = get_engine()
    if not engine or not engine.mcp_registry:
        raise HTTPException(status_code=503, detail="MCP 不可用")
    return engine.mcp_registry.get_stats()
