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

logger = logging.getLogger("chronoverse.routes")
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
        raise HTTPException(status_code=503, detail="游戏未初始化")
    report = engine.get_market_report()
    return {"report": report}


@router.get("/butterfly")
async def get_butterfly_summary():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return {"summary": engine.get_butterfly_summary()}


@router.get("/favor-events")
async def check_favor_events():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return {"events": engine.check_favor_events()}


@router.get("/destiny-regret")
async def check_destiny_regret():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    regret = engine.check_destiny_regret()
    missed = engine.get_missed_summary() if engine.destiny_regret else ""
    irreversible = engine.get_irreversible_summary() if engine.destiny_regret else ""
    return {"regret": regret, "missed": missed, "irreversible": irreversible}


@router.get("/faction-wars")
async def get_faction_wars():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    status = engine.get_faction_wars() if engine.faction_wars else ""
    history = engine.get_war_history() if engine.faction_wars else ""
    return {"status": status, "history": history}


@router.get("/death-stats")
async def get_death_stats():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return {"stats": engine.get_death_stats()}


@router.post("/death-choice")
async def handle_death_choice(req: DeathChoiceRequest):
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    result = engine.handle_death_choice(req.choice)
    return result


@router.post("/suicide-confirm")
async def suicide_confirm():
    engine = get_engine()
    if not engine or not engine.death_system or not engine.player_state:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    if not engine.world_state:
        raise HTTPException(status_code=503, detail="世界状态未初始化")
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
        raise HTTPException(status_code=503, detail="游戏未初始化")
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
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return {"narrative": engine.hundred_life_book.generate_book_narrative()}


@router.post("/generate-image")
async def generate_image(req: ImageRequest):
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
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
        if result.get("generated"):
            day = engine.world_state.current_day if engine.world_state else 0
            time_str = engine.world_state.current_time if engine.world_state else ""
            result["location"] = location
            result["weather"] = weather
            result["day"] = day
            result["time"] = time_str
            engine.visual_engine.image_history.append(result)
            if len(engine.visual_engine.image_history) > 50:
                engine.visual_engine.image_history = engine.visual_engine.image_history[-50:]
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
        raise HTTPException(status_code=503, detail="游戏未初始化")
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
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return engine.get_graph_visualization()

@router.post("/graph-rag/query")
async def query_graph_rag(req: GraphQueryRequest):
    """查询知识图谱"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return engine.query_graph_rag(req.question)


# ── [v1.6 P1-4] 势力图：GraphRAG 社区检测 ─────────────────────────

@router.get("/faction-graph")
async def get_faction_graph(method: str = "louvain", active_only: bool = True):
    """[v1.6 P1-4] 获取势力图可视化数据（Cytoscape.js 格式 + 社区信息）。

    查询参数：
        method: 社区检测算法，可选 louvain / label_propagation / greedy
        active_only: 是否仅基于有效关系构建图（默认 true）
    """
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    # 校验算法名
    if method not in ("louvain", "label_propagation", "greedy"):
        method = "louvain"
    active_only_flag = str(active_only).lower() in ("1", "true", "yes")
    return engine.get_faction_graph(method=method, active_only=active_only_flag)


@router.get("/factions")
async def get_factions(method: str = "louvain", active_only: bool = True):
    """[v1.6 P1-4] 仅获取势力检测结果（精简版，不含图谱元素）。

    用于侧边栏"势力概览"快速展示。
    """
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    if method not in ("louvain", "label_propagation", "greedy"):
        method = "louvain"
    active_only_flag = str(active_only).lower() in ("1", "true", "yes")
    return engine.detect_factions(method=method, active_only=active_only_flag)


# ── [v1.6 P1-5] CRAG + HyDE 检索调试 ─────────────────────────

@router.get("/retrieval/audit")
async def get_retrieval_audit(limit: int = 20):
    """[v1.6 P1-5] 获取最近 N 次检索审计日志。"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    audit = getattr(engine, "retrieval_audit", None)
    if not audit:
        return {"records": [], "stats": {"total_records": 0, "enabled": False}}
    return {
        "records": audit.recent(limit=limit),
        "stats": audit.stats(),
    }


@router.post("/retrieval/debug")
async def set_retrieval_debug(enabled: bool = False):
    """[v1.6 P1-5] 开关检索调试模式（详细日志打印）。"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    audit = getattr(engine, "retrieval_audit", None)
    if not audit:
        raise HTTPException(status_code=503, detail="检索审计未初始化")
    audit.debug = bool(enabled)
    audit.enabled = True  # 调试模式自动启用审计
    return {"debug": audit.debug, "enabled": audit.enabled}


@router.get("/retrieval/test")
async def test_retrieval(query: str = "测试", top_k: int = 5):
    """[v1.6 P1-5] 测试检索：执行一次 CRAG+HyDE 管道并返回详细结果。

    查询参数：
        query: 测试查询文本
        top_k: 返回数量
    """
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    pipeline = getattr(engine, "crag_hyde_pipeline", None)
    if not pipeline:
        raise HTTPException(status_code=503, detail="CRAG+HyDE 管道未初始化")
    import time as _time
    start = _time.time()
    results = pipeline.retrieve(query, top_k=top_k, world_context="")
    elapsed_ms = int((_time.time() - start) * 1000)
    # 返回前 top_k 结果（不含内部字段）
    clean = []
    for r in results:
        clean.append({
            "id": r.get("id", ""),
            "text": (r.get("text", "") or "")[:300],
            "score": round(r.get("score", 0.0), 4),
            "source": r.get("source", "") or (r.get("sources", [""])[0] if isinstance(r.get("sources"), list) else ""),
            "crag_score": r.get("crag_score", 0.0),
            "crag_label": r.get("crag_label", ""),
        })
    return {
        "query": query,
        "top_k": top_k,
        "elapsed_ms": elapsed_ms,
        "results": clean,
        "audit": engine.retrieval_audit.recent(limit=1)[0] if engine.retrieval_audit else None,
    }


@router.delete("/retrieval/audit")
async def clear_retrieval_audit():
    """[v1.6 P1-5] 清空检索审计日志。"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    audit = getattr(engine, "retrieval_audit", None)
    if not audit:
        raise HTTPException(status_code=503, detail="检索审计未初始化")
    audit.clear()
    return {"status": "cleared"}

@router.get("/ammm-facts")
async def get_amm_facts():
    """获取 AMM 经济系统的统计特征（厚尾分布等）"""
    engine = get_engine()
    if not engine or not engine.economy_system:
        raise HTTPException(status_code=503, detail="经济系统未初始化")
    return engine.economy_system.get_stylized_facts()


# ── [v1.6] 江湖见闻：NPC-NPC 对话传闻流 ─────────────────────────────

@router.get("/npc-dialogues/recent")
async def get_recent_npc_dialogues(limit: int = 10):
    """[v1.6] 获取最近的 NPC-NPC 对话会话列表。

    用于「江湖见闻」面板展示。返回完整对话内容。
    """
    engine = get_engine()
    if not engine or not getattr(engine, "npc_dialogue_manager", None):
        return {"sessions": [], "total": 0}
    limit = max(1, min(50, int(limit)))
    sessions = engine.npc_dialogue_manager.get_recent_sessions(limit=limit)
    return {"sessions": sessions, "total": len(sessions)}


@router.get("/npc-dialogues/rumors")
async def get_npc_dialogue_rumors(limit: int = 10):
    """[v1.6] 获取 NPC-NPC 对话的传闻流。

    - 玩家目击的对话返回完整内容
    - 远处对话降级为模糊传闻（隐去部分姓名、仅暴露摘要）
    """
    engine = get_engine()
    if not engine or not getattr(engine, "npc_dialogue_manager", None):
        return {"rumors": [], "total": 0}
    limit = max(1, min(50, int(limit)))
    player_loc = ""
    if engine.player_state:
        player_loc = engine.player_state.location or ""
    rumors = engine.npc_dialogue_manager.get_rumor_feed(
        limit=limit, player_location=player_loc,
    )
    return {"rumors": rumors, "total": len(rumors)}


@router.get("/npc-dialogues/today")
async def get_today_npc_dialogues():
    """[v1.6] 获取今日产生的 NPC-NPC 对话会话（供 UI 实时刷新）。"""
    engine = get_engine()
    if not engine:
        return {"sessions": [], "count": 0}
    today = getattr(engine, "_today_npc_dialogues", []) or []
    return {"sessions": list(today), "count": len(today)}


# ── [v1.6] BranchPlanner 思维树可视化 + 异步预规划 ─────────────────

@router.get("/planner/recent")
async def get_recent_plans(limit: int = 10):
    """获取最近的规划历史记录。"""
    engine = get_engine()
    if not engine or not engine.branch_planner:
        return {"plans": [], "total": 0}
    plans = engine.branch_planner.get_recent_plans(limit=limit)
    return {"plans": plans, "total": len(plans)}


@router.get("/planner/{npc_id}")
async def get_npc_plan(npc_id: str):
    """获取指定 NPC 的缓存规划。"""
    engine = get_engine()
    if not engine or not engine.branch_planner:
        return {"plan": None}
    plan = engine.branch_planner.get_npc_plan(npc_id)
    return {"plan": plan}


@router.get("/planner/thought-tree/{npc_id}")
async def get_thought_tree(npc_id: str):
    """获取指定 NPC 的思维树数据（cytoscape elements 格式）。"""
    engine = get_engine()
    if not engine or not engine.branch_planner:
        return {"npc_id": npc_id, "elements": {"nodes": [], "edges": []}, "plan": None}
    # [v9] 校验 npc_id 格式
    if not SAFE_NPC_ID.match(npc_id):
        raise HTTPException(status_code=400, detail="Invalid NPC ID")
    return engine.branch_planner.get_thought_tree(npc_id)


@router.post("/planner/preplan")
async def trigger_preplan():
    """触发异步预规划（对活跃 NPC 批量预规划）。

    通过 task_queue 后台执行，不阻塞请求。
    """
    engine = get_engine()
    if not engine or not engine.branch_planner:
        raise HTTPException(status_code=503, detail="规划器未初始化")
    if not engine.npc_states:
        return {"status": "no_npcs"}
    # 选取活跃 NPC（非 sleeping，最多 5 个）
    npcs = []
    for npc in engine.npc_states.values():
        if len(npcs) >= 5:
            break
        status = getattr(npc, "status", "") or ""
        if status == "sleeping":
            continue
        npcs.append(npc)
    if not npcs:
        return {"status": "no_active_npcs"}
    # 后台异步执行，不阻塞请求
    world_state = engine.world_state
    import asyncio
    async def _do_preplan():
        await engine.branch_planner.batch_preplan_async(npcs, world_state)
    asyncio.create_task(_do_preplan())
    return {"status": "started", "npc_count": len(npcs)}


@router.post("/character-card/export/{npc_id}")
async def export_character_card(npc_id: str):
    """导出NPC角色卡"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    # [v9] 校验 npc_id 格式，防止路径注入
    if not SAFE_NPC_ID.match(npc_id):
        raise HTTPException(status_code=400, detail="Invalid NPC ID")
    # [v9] 使用 uuid 生成文件名，避免文件名注入
    safe_name = f"{uuid.uuid4().hex[:8]}_card.json"
    path = os.path.join(tempfile.gettempdir(), safe_name)
    ok = engine.export_character_card(npc_id, path)
    if ok:
        return {"success": True, "path": path}
    raise HTTPException(status_code=500, detail="导出失败")

@router.post("/character-card/import")
async def import_character_card(req: CharCardImportRequest):
    """导入角色卡"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
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
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return engine.get_v10_dashboard()


@router.get("/v10/narrative-review")
async def get_narrative_review():
    """[v10] 获取叙事回顾结果和质量趋势"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return engine.get_narrative_review()


@router.get("/v10/task-board")
async def get_task_board():
    """[v10] 获取世界任务板状态"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return engine.get_task_board()


@router.get("/v10/butterfly-approvals")
async def get_butterfly_approvals():
    """[v10] 获取待审批的蝴蝶效应"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return {"approvals": engine.get_butterfly_approvals()}


@router.post("/v10/butterfly-approve")
async def approve_butterfly_effect(req: ButterflyApprovalRequest):
    """[v10] 审批蝴蝶效应后果"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return engine.approve_butterfly_effect(req.approval_id, req.decision)


@router.get("/v10/curator-stats")
async def get_curator_stats():
    """[v10] 获取记忆 Curator 统计"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return engine.get_curator_stats()


@router.get("/v10/procedural-memory")
async def get_procedural_memory_stats():
    """[v10] 获取 NPC 程序性记忆统计"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return engine.get_npc_procedural_stats()


class ApprovalGateConfigRequest(BaseModel):
    enabled: bool = False
    threshold: float = 7.0


@router.post("/v10/approval-gate/config")
async def configure_approval_gate(req: ApprovalGateConfigRequest):
    """[v10] 配置蝴蝶效应审批门"""
    engine = get_engine()
    if not engine or not engine.butterfly:
        raise HTTPException(status_code=503, detail="游戏未初始化")
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
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return engine.get_foreshadow_health()


class ForeshadowReminderConfig(BaseModel):
    mode: str = "normal"  # "normal" 或 "silent"


@router.post("/v10/foreshadow/reminder")
async def set_foreshadow_reminder(req: ForeshadowReminderConfig):
    """[v10+] 设置伏笔提醒模式：normal=正常提醒, silent=静默运行"""
    engine = get_engine()
    if not engine or not engine.foreshadow_lifecycle:
        raise HTTPException(status_code=503, detail="伏笔系统未初始化")
    if req.mode not in ("normal", "silent"):
        raise HTTPException(status_code=400, detail="mode 必须是 normal 或 silent")
    engine.foreshadow_lifecycle.reminder_mode = req.mode
    return {"success": True, "mode": req.mode}


@router.get("/v10/continuity-audit")
async def get_continuity_audit():
    """[v10+] 获取多维度连续性审计结果"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
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
        raise HTTPException(status_code=503, detail="游戏未初始化")
    if not engine.lorebook:
        raise HTTPException(status_code=503, detail="世界书系统未初始化")
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


# ── [BudgetGuard] LLM 预算控制 API（v1.3+） ──────────────────

@router.get("/llm-budget/status")
async def get_llm_budget_status():
    """获取 LLM 预算控制状态（成本、熔断、回合调用）"""
    engine = get_engine()
    if not engine or not engine.llm:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    from modules.llm.router import LLMRouter
    if not isinstance(engine.llm, LLMRouter):
        # TaskBoundLLM 代理：透传到内部 router
        inner = getattr(engine.llm, '_router', None)
        if not isinstance(inner, LLMRouter):
            raise HTTPException(status_code=501, detail="LLM 不支持预算控制")
        return inner.get_budget_status()
    return engine.llm.get_budget_status()


@router.get("/llm-budget/aggregate")
async def get_llm_aggregate_stats():
    """获取 LLM 聚合统计（按模型/任务分类的调用次数与成本）"""
    engine = get_engine()
    if not engine or not engine.llm:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    from modules.llm.router import LLMRouter
    router = engine.llm
    if not isinstance(router, LLMRouter):
        router = getattr(engine.llm, '_router', None)
        if not isinstance(router, LLMRouter):
            raise HTTPException(status_code=501, detail="LLM 不支持聚合统计")
    return router.get_aggregate_stats()


@router.post("/llm-budget/reset-daily")
async def reset_llm_daily_budget():
    """手动重置今日 LLM 成本累计"""
    engine = get_engine()
    if not engine or not engine.llm:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    from modules.llm.router import LLMRouter
    router = engine.llm
    if not isinstance(router, LLMRouter):
        router = getattr(engine.llm, '_router', None)
        if not isinstance(router, LLMRouter):
            raise HTTPException(status_code=501, detail="LLM 不支持预算控制")
    router.reset_daily_budget()
    return {"success": True, "status": router.get_budget_status()}


@router.post("/llm-budget/reset-circuit")
async def reset_llm_circuit():
    """手动关闭熔断（紧急恢复用）"""
    engine = get_engine()
    if not engine or not engine.llm:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    from modules.llm.router import LLMRouter
    router = engine.llm
    if not isinstance(router, LLMRouter):
        router = getattr(engine.llm, '_router', None)
        if not isinstance(router, LLMRouter):
            raise HTTPException(status_code=501, detail="LLM 不支持预算控制")
    router.reset_circuit()
    return {"success": True, "status": router.get_budget_status()}


# ========== [v1.3] 因果链可视化 API ==========

@router.get("/causal-graph")
async def get_causal_graph(limit: int = 0):
    """获取因果链图（Cytoscape.js elements 格式）

    参数：
        limit: 限制返回节点数（0=全部）
    """
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    cg = getattr(engine, 'causal_graph', None)
    if not cg:
        return {"elements": [], "count": 0, "min_importance": 6.0}
    if limit and limit > 0:
        # 仅返回最近 N 个节点
        recent_nodes = cg.get_recent(limit)
        from modules.causal_graph import CausalGraph, CausalNode
        tmp = CausalGraph(min_importance=cg.min_importance, max_nodes=cg.max_nodes)
        for n in recent_nodes:
            tmp.nodes.append(n)
            tmp._index[n.turn_id] = n
        return tmp.to_vis_format()
    return cg.to_vis_format()


@router.get("/causal-graph/recent")
async def get_causal_graph_recent(n: int = 20):
    """获取最近的因果节点（倒序）"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    cg = getattr(engine, 'causal_graph', None)
    if not cg:
        return {"nodes": [], "count": 0}
    nodes = cg.get_recent(n)
    return {
        "nodes": [nd.to_dict() for nd in nodes],
        "count": len(nodes),
        "total": len(cg.nodes),
    }


@router.get("/causal-graph/turn/{turn_id}")
async def get_causal_node_by_turn(turn_id: int):
    """获取指定回合的因果节点详情"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    cg = getattr(engine, 'causal_graph', None)
    if not cg:
        raise HTTPException(status_code=503, detail="因果链未初始化")
    node = cg.get_node(turn_id)
    if not node:
        return {"error": f"回合 {turn_id} 未记录因果节点"}
    return node.to_dict()


@router.post("/causal-graph/clear")
async def clear_causal_graph():
    """清空因果链图（debug 用）"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    cg = getattr(engine, 'causal_graph', None)
    if not cg:
        raise HTTPException(status_code=503, detail="因果链未初始化")
    count = len(cg.nodes)
    cg.clear()
    return {"success": True, "cleared": count}


@router.get("/causal-graph/stats")
async def get_causal_graph_stats():
    """获取因果链统计信息"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    cg = getattr(engine, 'causal_graph', None)
    if not cg:
        raise HTTPException(status_code=503, detail="因果链未初始化")
    nodes = cg.get_all_nodes()
    if not nodes:
        return {
            "total": 0,
            "min_importance": cg.min_importance,
            "max_importance": 0,
            "avg_importance": 0,
            "by_event": {},
            "by_day": {},
        }
    # 按事件类型统计
    by_event: dict[str, int] = {}
    by_day: dict[int, int] = {}
    total_imp = 0.0
    max_imp = 0.0
    for n in nodes:
        total_imp += n.importance
        if n.importance > max_imp:
            max_imp = n.importance
        by_day[n.day] = by_day.get(n.day, 0) + 1
        for ev in n.triggered_events:
            by_event[ev] = by_event.get(ev, 0) + 1
    return {
        "total": len(nodes),
        "min_importance": cg.min_importance,
        "max_importance": round(max_imp, 2),
        "avg_importance": round(total_imp / len(nodes), 2),
        "by_event": by_event,
        "by_day": by_day,
        "latest_turn": nodes[-1].turn_id if nodes else 0,
        "earliest_turn": nodes[0].turn_id if nodes else 0,
    }


# ── [v1.6 P1-6] 长期记忆摘要 + 审计日志 API ──────────────────


@router.get("/memory/summaries")
async def get_long_term_summaries(level: str = "", limit: int = 20):
    """获取长期记忆摘要列表（L1/L2/L3 多层）。

    参数：
        level: 过滤级别（L1/L2/L3），空字符串表示全部
        limit: 返回数量上限（最多 50）
    """
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    summarizer = getattr(engine, "long_term_memory_summarizer", None)
    if not summarizer:
        raise HTTPException(status_code=503, detail="长期记忆摘要器未启用")
    limit = max(1, min(50, int(limit)))
    summaries = summarizer.get_long_term_summaries(level=level, limit=limit)
    return {
        "summaries": summaries,
        "count": len(summaries),
        "filter_level": level or "all",
    }


@router.get("/memory/audit")
async def get_memory_audit(limit: int = 50, operation: str = ""):
    """获取记忆操作审计日志（最新在前）。"""
    from modules.memory import memory_audit_log
    limit = max(1, min(200, int(limit)))
    records = memory_audit_log.recent(limit=limit, operation=operation)
    return {
        "records": records,
        "count": len(records),
        "stats": memory_audit_log.stats(),
    }


@router.get("/memory/audit/stats")
async def get_memory_audit_stats():
    """获取审计日志统计信息。"""
    from modules.memory import memory_audit_log
    return memory_audit_log.stats()


@router.get("/memory/audit/target/{target_id}")
async def get_memory_audit_by_target(target_id: str):
    """查询某条记忆的所有操作历史。"""
    from modules.memory import memory_audit_log
    records = memory_audit_log.get_by_target(target_id)
    return {
        "target_id": target_id,
        "records": records,
        "count": len(records),
    }


class MemoryAuditConfigRequest(BaseModel):
    enabled: bool = True


@router.post("/memory/audit/toggle")
async def toggle_memory_audit(req: MemoryAuditConfigRequest):
    """启用/禁用记忆审计日志。"""
    from modules.memory import memory_audit_log
    memory_audit_log.enabled = bool(req.enabled)
    return {
        "enabled": memory_audit_log.enabled,
        "stats": memory_audit_log.stats(),
    }


@router.post("/memory/audit/clear")
async def clear_memory_audit():
    """清空审计日志（仅清缓冲，不影响已写入向量库的记忆）。"""
    from modules.memory import memory_audit_log
    memory_audit_log.clear()
    return {
        "status": "cleared",
        "stats": memory_audit_log.stats(),
    }


class MilestoneGenerateRequest(BaseModel):
    event_type: str
    description: str
    day: int = 0
    context: str = ""


@router.post("/memory/milestone")
async def generate_milestone(req: MilestoneGenerateRequest):
    """手动触发里程碑摘要生成（L3）。"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    summarizer = getattr(engine, "long_term_memory_summarizer", None)
    if not summarizer:
        raise HTTPException(status_code=503, detail="长期记忆摘要器未启用")
    if not summarizer.memory_store:
        raise HTTPException(status_code=503, detail="MemoryStore 未注入")
    result = summarizer.generate_milestone_summary(
        event_type=req.event_type,
        description=req.description,
        day=req.day,
        context=req.context,
        turn=getattr(engine.meta, "current_turn", 0) if engine.meta else 0,
    )
    return result


@router.get("/memory/overview")
async def get_memory_overview():
    """获取记忆系统总览：摘要统计 + 审计统计 + MemoryStore 计数。"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    from modules.memory import memory_audit_log
    summarizer = getattr(engine, "long_term_memory_summarizer", None)
    summaries = summarizer.get_long_term_summaries(limit=50) if summarizer else []
    # 按级别统计
    by_level: dict[str, int] = {}
    for s in summaries:
        lv = s.get("level", "?")
        by_level[lv] = by_level.get(lv, 0) + 1
    memory_count = 0
    if engine.memory:
        try:
            memory_count = engine.memory.collection.count()
        except Exception:
            memory_count = 0
    # MemoryCurator 历史
    curator_summaries = []
    if engine.memory_curator:
        curator_summaries = engine.memory_curator.get_all_summaries()[-10:]
    return {
        "long_term_summaries": {
            "total": len(summaries),
            "by_level": by_level,
            "recent": summaries[:10],
        },
        "audit": memory_audit_log.stats(),
        "memory_store_count": memory_count,
        "curator_summary_count": len(curator_summaries),
        "curator_recent": curator_summaries,
    }


@router.get("/memory/detect-milestone")
async def detect_milestone_endpoint(text: str = ""):
    """检测给定文本中是否包含里程碑事件（不生成摘要，仅查询）。"""
    from modules.memory import detect_milestone
    event_type = detect_milestone(text)
    return {
        "text_preview": text[:200] if text else "",
        "detected": event_type is not None,
        "event_type": event_type,
    }


# ── [v1.6 P1-7] 记忆检索增强闭环 API ──────────────────


@router.get("/memory/retrieval-test")
async def memory_retrieval_test(query: str = "", top_k: int = 8):
    """测试带长期摘要加权的检索结果。

    参数：
        query: 测试查询文本
        top_k: 返回数量
    返回：检索结果列表 + 长期摘要引用标记
    """
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    if not query:
        raise HTTPException(status_code=400, detail="query 参数必填")
    top_k = max(1, min(20, int(top_k)))
    pipeline = getattr(engine, "crag_hyde_pipeline", None)
    if not pipeline:
        # 回退到 hybrid_retriever
        hybrid = getattr(engine, "hybrid_retriever", None)
        if not hybrid:
            raise HTTPException(status_code=503, detail="检索器未启用")
        try:
            results = hybrid.retrieve(query, top_k=top_k)
        except Exception as e:
            return {"error": f"检索失败: {e}"}
    else:
        try:
            results = pipeline.retrieve(query, top_k=top_k)
        except Exception as e:
            return {"error": f"检索失败: {e}"}
    # 标注长期摘要引用
    long_term_refs = [
        {
            "level": r.get("summary_level", ""),
            "milestone_type": r.get("milestone_type", ""),
            "day": r.get("day", 0),
            "text": (r.get("text", "") or "")[:100],
            "score": r.get("score", 0),
            "crag_label": r.get("crag_label", ""),
            "forced_recall": r.get("forced_recall", False),
        }
        for r in results
        if r.get("is_long_term_summary") or r.get("summary_level")
    ]
    return {
        "query": query,
        "top_k": top_k,
        "results": [
            {
                "id": r.get("id", ""),
                "text": (r.get("text", "") or "")[:300],
                "score": round(r.get("score", 0), 4),
                "source": r.get("source", ""),
                "crag_score": r.get("crag_score"),
                "crag_label": r.get("crag_label", ""),
                "is_long_term_summary": r.get("is_long_term_summary", False),
                "summary_level": r.get("summary_level", ""),
                "milestone_type": r.get("milestone_type", ""),
                "forced_recall": r.get("forced_recall", False),
            }
            for r in results
        ],
        "long_term_refs": long_term_refs,
        "long_term_count": len(long_term_refs),
    }


@router.get("/memory/last-refs")
async def get_last_long_term_refs():
    """获取上次叙事生成时引用的长期记忆摘要（供前端展示引用徽章）。"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    player_agent = getattr(engine, "player_agent", None)
    if not player_agent:
        raise HTTPException(status_code=503, detail="PlayerAgent 未启用")
    refs = getattr(player_agent, "_last_long_term_refs", [])
    return {
        "refs": refs,
        "count": len(refs),
    }


@router.get("/memory/milestone-recall")
async def milestone_recall_test(query: str = ""):
    """测试里程碑强制召回：给定查询文本，返回匹配到的 L3 摘要。"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    if not query:
        raise HTTPException(status_code=400, detail="query 参数必填")
    summarizer = getattr(engine, "long_term_memory_summarizer", None)
    if not summarizer:
        raise HTTPException(status_code=503, detail="长期记忆摘要器未启用")
    results = summarizer.fetch_milestones_for_retrieval(query, max_results=5)
    return {
        "query": query,
        "recall_count": len(results),
        "results": [
            {
                "id": r.get("id", ""),
                "text": (r.get("text", "") or "")[:200],
                "milestone_type": r.get("milestone_type", ""),
                "day": r.get("day", 0),
                "score": r.get("score", 0),
            }
            for r in results
        ],
    }


# ── [v1.6 P1-8] 情感记忆系统 API ──────────────────


@router.get("/emotional/summary")
async def get_emotional_summary(entity: str = ""):
    """获取情感记忆统计：8 类情感的强度分布、效价均值。

    参数：
        entity: 限定某实体（NPC 名），空字符串表示全局
    """
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    mgr = getattr(engine, "emotional_memory_manager", None)
    if not mgr:
        raise HTTPException(status_code=503, detail="情感记忆管理器未启用")
    summary = mgr.get_emotional_summary(related_entity=entity or None)
    return {
        "entity": entity or "global",
        "emotions": summary.get("emotions", {}),
        "total": summary.get("total", 0),
        "avg_valence": summary.get("avg_valence", 0.0),
    }


@router.get("/emotional/by-emotion")
async def search_emotional_memory(emotion: str = "",
                                    limit: int = 10,
                                    min_weight: float = 0.0,
                                    entity: str = ""):
    """按情感类型检索情感记忆。

    参数：
        emotion: 8 类情感之一（joy/sadness/anger/fear/surprise/disgust/trust/anticipation）
        limit: 返回数量上限
        min_weight: 最小情感强度过滤
        entity: 关联实体过滤
    """
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    mgr = getattr(engine, "emotional_memory_manager", None)
    if not mgr or not mgr.memory_store:
        raise HTTPException(status_code=503, detail="情感记忆管理器未启用或 MemoryStore 未注入")
    if not emotion:
        raise HTTPException(status_code=400, detail="emotion 参数必填")
    limit = max(1, min(50, int(limit)))
    items = mgr.memory_store.search_by_emotion(
        emotion_type=emotion,
        n_results=limit,
        min_weight=float(min_weight),
        related_entity=entity or None,
    )
    return {
        "emotion": emotion,
        "items": [
            {
                "id": it.get("id", ""),
                "text": (it.get("text", "") or "")[:300],
                "emotional_weight": it.get("emotional_weight", 0.0),
                "valence": it.get("valence", 0.0),
                "arousal": it.get("arousal", 0.0),
            }
            for it in items
        ],
        "count": len(items),
    }


@router.get("/emotional/player")
async def get_player_emotion():
    """获取主角当前情感状态向量。"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    mgr = getattr(engine, "emotional_memory_manager", None)
    if not mgr:
        raise HTTPException(status_code=503, detail="情感记忆管理器未启用")
    state = mgr.get_player_emotion()
    return state


@router.get("/emotional/npcs")
async def get_all_npc_emotions():
    """获取所有 NPC 的情感状态（用于前端情感面板可视化）。"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    mgr = getattr(engine, "emotional_memory_manager", None)
    if not mgr:
        raise HTTPException(status_code=503, detail="情感记忆管理器未启用")
    states = mgr.get_all_npc_emotions()
    return {
        "npcs": states,
        "count": len(states),
    }


@router.get("/emotional/npc/{npc_id}")
async def get_npc_emotion(npc_id: str):
    """获取指定 NPC 的情感状态。"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    mgr = getattr(engine, "emotional_memory_manager", None)
    if not mgr:
        raise HTTPException(status_code=503, detail="情感记忆管理器未启用")
    state = mgr.get_npc_emotion(npc_id)
    if state is None:
        return {"error": f"NPC {npc_id} 无情感状态记录"}
    return state


class EmotionEvaluateRequest(BaseModel):
    text: str
    npc_ids: list[str] = []
    npc_names: list[str] = []
    source: str = "manual"
    detail: str = ""


@router.post("/emotional/evaluate")
async def evaluate_emotion(req: EmotionEvaluateRequest):
    """手动触发情感评估：评估文本情感并写入记忆库 + 更新 NPC 状态。

    用于调试或前端手动测试。
    """
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    mgr = getattr(engine, "emotional_memory_manager", None)
    if not mgr:
        raise HTTPException(status_code=503, detail="情感记忆管理器未启用")
    turn = getattr(engine.meta, "current_turn", 0) if engine.meta else 0
    result = mgr.record_event(
        text=req.text,
        npc_ids=req.npc_ids,
        npc_names=req.npc_names,
        turn=turn,
        source=req.source,
        detail=req.detail,
    )
    return {
        "eval_result": result,
        "npc_count_updated": len(req.npc_ids),
    }


@router.get("/emotional/overview")
async def get_emotional_overview():
    """情感系统总览：玩家情感 + 全部 NPC 情感 + 情感记忆统计。"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    mgr = getattr(engine, "emotional_memory_manager", None)
    if not mgr:
        raise HTTPException(status_code=503, detail="情感记忆管理器未启用")
    return {
        "player": mgr.get_player_emotion(),
        "npcs": mgr.get_all_npc_emotions(),
        "summary": mgr.get_emotional_summary(),
    }


# ── [v1.7 P2-5] 可观测性：耗时打点端点 ─────────────────────


@router.get("/timing")
async def timing_stats(limit: int = 50, category: str = ""):
    """[v1.7 P2-5] 暴露 TimingCollector 耗时统计。

    参数：
        limit: 最近 N 条记录（默认 50，上限 200）
        category: 按分类过滤（如 llm/retrieval/db/memory）
    """
    from modules.core.timing import TimingCollectorInstance
    limit = max(1, min(200, limit))
    stats = TimingCollectorInstance.stats()
    recent = TimingCollectorInstance.recent(limit=limit, category=category)
    return {
        "stats": stats,
        "recent": recent,
    }


@router.post("/timing/clear")
async def timing_clear():
    """[v1.7 P2-5] 清空耗时打点（debug 用）。"""
    from modules.core.timing import TimingCollectorInstance
    TimingCollectorInstance.clear()
    return {"status": "cleared"}


# ── [v1.7 P3-A] 性能基线：聚合端点 ─────────────────────────


@router.get("/performance")
async def performance_overview():
    """[v1.7 P3-A] 一次性聚合性能数据：timing + LLM 调用 + 缓存 + 预算。

    返回结构：
        - timing: TimingCollector.stats()（含 by_category / slow_calls）
        - llm: LLMRouter.get_aggregate_stats()（含 per_model / per_task / 成本估算）
        - cache: LLMCache.get_stats()（含 hit_rate_float / expired）
        - budget: LLMRouter.get_budget_status()（含 daily_cost / 熔断状态）
        - locks: 关键锁的竞争提示（_game_lock / _save_lock 持有状态）

    敏感端点：与 /api/timing 一致，仅诊断用。
    """
    from modules.core.timing import TimingCollectorInstance
    engine = get_engine()

    overview = {
        "timing": TimingCollectorInstance.stats(),
        "llm": None,
        "cache": None,
        "budget": None,
        "locks": None,
    }

    if engine and engine.llm is not None:
        llm = engine.llm
        # [v1.7 P3-A] LLM 聚合统计（含 per_model / per_task / 成本）
        if hasattr(llm, "get_aggregate_stats"):
            overview["llm"] = llm.get_aggregate_stats()
        # [v1.7 P3-A] BudgetGuard 预算状态
        if hasattr(llm, "get_budget_status"):
            overview["budget"] = llm.get_budget_status()

    # [v1.7 P3-A] LLM 缓存统计
    if engine and engine.llm_cache is not None:
        overview["cache"] = engine.llm_cache.get_stats()

    # [v1.7 P3-A] 锁竞争提示（仅报告持有状态，不暴露内部数据）
    if engine:
        overview["locks"] = {
            "game_lock_locked": engine._game_lock.locked() if hasattr(engine, "_game_lock") else None,
            "save_lock_locked": engine._save_lock.locked() if hasattr(engine, "_save_lock") else None,
            "init_lock_locked": engine._init_lock.locked() if hasattr(engine, "_init_lock") else None,
        }

    return overview

