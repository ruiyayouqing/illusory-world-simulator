from __future__ import annotations
import json
import asyncio
import logging
import re
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .deps import BASE_DIR, get_engine, set_engine, get_meta_db, _engine_switch_lock
from modules.game_engine import GameEngine
from modules.security import decrypt_config_keys
from modules.prompt_utils import resolve_location_name  # [Bug] location code → display name

logger = logging.getLogger("chronoverse")
router = APIRouter(prefix="/api")


def _validate_world_id(world_id: str) -> str:
    """[v9] 验证 world_id，防止路径遍历攻击"""
    if not world_id or not re.match(r'^[a-zA-Z0-9_\-]+$', world_id):
        raise HTTPException(status_code=400, detail="Invalid world_id format")
    return world_id


class CreateGameRequest(BaseModel):
    api_key: str
    base_url: str = "https://token-plan-cn.xiaomimimo.com/v1"
    model_name: str = "mimo-V2.5-Pro"
    world_name: str = "大明风华"


class LoadGameRequest(BaseModel):
    api_key: str
    base_url: str = "https://token-plan-cn.xiaomimimo.com/v1"
    model_name: str = "mimo-V2.5-Pro"
    world_id: str


class PlayerInputRequest(BaseModel):
    input: str


class GenerateWorldRequest(BaseModel):
    description: str
    world_type: str = "custom"
    golden_finger: bool = False  # [v9] 金手指开关
    api_key: str = ""
    base_url: str = "https://token-plan-cn.xiaomimimo.com/v1"
    model_name: str = "mimo-V2.5-Pro"


class SetGoalRequest(BaseModel):
    goal_type: str


class SlotRequest(BaseModel):
    slot_id: str


class AddExpRequest(BaseModel):
    amount: int


class EventRespondRequest(BaseModel):
    action: str = "accept"  # accept | reject | ignore | view


def _apply_image_config(eng):
    config_path = BASE_DIR / "config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config = decrypt_config_keys(config)
            img_cfg = config.get("image", {})
            img_key = img_cfg.get("api_key", "")
            img_url = img_cfg.get("base_url", "")
            img_model = img_cfg.get("model_name", "")
            img_size = img_cfg.get("image_size", "")
            if img_key and eng.visual_engine:
                eng.visual_engine.set_api_key(img_key)
            if img_url and eng.visual_engine:
                eng.visual_engine.set_api_url(img_url)
            if img_model and eng.visual_engine:
                eng.visual_engine.set_model(img_model)
            if img_size and eng.visual_engine:
                eng.visual_engine.default_image_size = img_size
        except Exception as e:
            logger.warning("Failed to load image config: %s", e)


@router.get("/saves")
async def list_saves():
    save_dir = BASE_DIR / "saves"
    if not save_dir.exists():
        return {"saves": []}
    saves = []
    index_file = save_dir / "index.json"
    if index_file.exists():
        index = json.loads(index_file.read_text(encoding="utf-8"))
        saves = list(index.get("saves", {}).values())
    return {"saves": saves}


@router.get("/worlds")
async def list_worlds():
    # [v1.3] 合并 MetaDB + index.json，防止两数据源不同步导致"加载存档"页看不到世界
    save_dir = BASE_DIR / "saves"
    index_file = save_dir / "index.json"
    # 1. 从 index.json 读取（文件系统真实存档）
    index_worlds = {}
    if index_file.exists():
        try:
            index = json.loads(index_file.read_text(encoding="utf-8"))
            for wid, info in index.get("saves", {}).items():
                index_worlds[wid] = info
        except Exception as e:
            logger.warning("Failed to read index.json: %s", e)
    # 2. 从 MetaDB 读取（补充元信息）
    db_worlds = {}
    try:
        db = get_meta_db()
        for w in db.list_worlds():
            db_worlds[w.get("world_id", "")] = w
    except Exception as e:
        logger.warning("MetaDB list_worlds failed: %s", e)
    # 3. 合并：以 index.json 为主（文件系统真实存在），MetaDB 补充缺失字段
    merged = {}
    for wid, info in index_worlds.items():
        merged[wid] = {**info, **{k: v for k, v in db_worlds.get(wid, {}).items() if k not in info or not info.get(k)}}
    # 4. 补充 MetaDB 中有但 index.json 没有的（理论上不应出现，防止数据丢失）
    for wid, w in db_worlds.items():
        if wid not in merged:
            merged[wid] = w
    # 5. 按最后保存时间排序
    worlds = list(merged.values())
    worlds.sort(key=lambda x: x.get("last_saved_at", x.get("created_at", "")), reverse=True)
    return {"worlds": worlds}


@router.get("/worlds/{world_id}/saves")
async def list_world_saves(world_id: str):
    world_id = _validate_world_id(world_id)
    save_dir = BASE_DIR / "saves"
    world_dir = save_dir / world_id
    if not world_dir.exists():
        return {"saves": []}
    index_file = save_dir / "index.json"
    world_info = {}
    if index_file.exists():
        index = json.loads(index_file.read_text(encoding="utf-8"))
        world_info = index.get("saves", {}).get(world_id, {})
    saves = [{
        "slot_id": "auto",
        "name": "自动存档",
        "day": world_info.get("current_day", 1),
        "player_name": world_info.get("player_name", "?"),
        "player_age": world_info.get("player_age", "?"),
        "saved_at": world_info.get("last_saved_at", ""),
        "total_turns": world_info.get("total_turns", 0),
    }]
    slot_file = world_dir / "save_slots" / "slots.json"
    if slot_file.exists():
        slots = json.loads(slot_file.read_text(encoding="utf-8"))
        saves.extend(slots)
    saves.sort(key=lambda s: s.get("saved_at", ""), reverse=True)
    return {"saves": saves, "world_name": world_info.get("world_name", "未知")}


@router.get("/world-types")
async def get_world_types():
    engine = get_engine()
    if not engine:
        return {"world_types": []}
    return {"world_types": engine.get_world_types()}


@router.get("/state")
async def get_state():
    engine = get_engine()
    if not engine or not engine.player_state:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    history = engine.narrative_history
    images = engine.visual_engine.image_history if engine.visual_engine else []
    # [v1.5 第一期] 返回事件清单（玩家事件 + 世界事件），前端按优先级渲染
    player_events = []
    world_events = []
    if getattr(engine, 'player_event_bus', None):
        player_events = [e.to_dict() for e in engine.player_event_bus.list_pending()]
    if getattr(engine, 'world_event_bus', None):
        world_events = [e.to_dict() for e in engine.world_event_bus.list_pending()]
    return {
        "state": engine.get_game_state(),
        "history": history,
        "images": images,
        "player_events": player_events,
        "world_events": world_events,
    }


@router.post("/create")
async def create_game(req: CreateGameRequest):
    # [v10.5] 加 _engine_switch_lock，防止并发创建/加载导致全局 engine 竞态
    async with _engine_switch_lock:
        engine = GameEngine(str(BASE_DIR / "saves"))
        engine.init_llm(req.api_key, req.base_url, req.model_name)

        _apply_image_config(engine)

        world_path = BASE_DIR / "data" / "worlds" / "demo_ming" / "world.json"
        world_data = json.loads(world_path.read_text(encoding="utf-8"))
        player_data = world_data["roles"]["player"]
        npc_data_list = [world_data["roles"]["npc_苏梅"], world_data["roles"]["npc_张大叔"]]

        world_id = engine.create_new_game(world_data, player_data, npc_data_list, req.world_name)
        state = engine.get_game_state()
        engine.save_game("auto")
        # [Bug] 必须在游戏状态完全初始化后才 set_engine，否则其他请求可能读到半初始化的引擎
        set_engine(engine)

    return {
        "world_id": world_id,
        "state": state,
    }


@router.post("/load")
async def load_game(req: LoadGameRequest):
    # [v9] Bug H5b: 校验 world_id，防止路径遍历
    world_id = _validate_world_id(req.world_id)
    # [v10.5] 加 _engine_switch_lock，防止并发加载导致全局 engine 竞态
    async with _engine_switch_lock:
        try:
            engine = GameEngine(str(BASE_DIR / "saves"))
            engine.init_llm(req.api_key, req.base_url, req.model_name)
            info = await asyncio.to_thread(engine.load_game, world_id)
            # [Bug] 必须在 load_game 完成后才 set_engine，否则其他请求可能读到 player_state=None
            set_engine(engine)
            state = engine.get_game_state()
            history = engine.narrative_history
            logger.info("Load game %s: history count=%d", world_id, len(history))
            images = engine.visual_engine.image_history if engine.visual_engine else []
            initial_options = []
            if engine.option_engine and engine.player_state:
                try:
                    scene = f"第{engine.world_state.current_day}天 {engine.world_state.current_time}，你在{resolve_location_name(engine.player_state.location, engine.world_state)}"  # [Bug] location code → display name
                    initial_options = await asyncio.to_thread(engine.option_engine.generate_options, scene, engine.player_state, engine.world_state)
                except Exception as e:
                    logger.warning("Failed to generate initial options on load: %s", e)
                    initial_options = engine.option_engine._fallback_options(engine.player_state)
            # [Bug] 加载存档时恢复世界观简介，否则前端只恢复历史叙事但丢失初始世界观
            world_intro = ""
            if engine.world_def:
                world_intro = engine.world_def.get("world_intro", "")
            if not world_intro and engine.world_def:
                desc = engine.world_def.get("description", "")
                name = engine.world_def.get("world_name", "未知世界")
                factions = ", ".join(engine.world_def.get("factions", {}).keys()) or "未知"
                locations = ", ".join(engine.world_def.get("locations", {}).keys()) or "未知"
                power = engine.world_def.get("power_system", {})
                power_name = power.get("name", "未知")
                power_levels = " → ".join([lv.get("name", "") for lv in power.get("levels", [])]) if power.get("levels") else ""
                world_intro = f"【{name}】\n\n{desc}\n\n势力分布：{factions}\n已知地点：{locations}\n力量体系：{power_name}"
                if power_levels:
                    world_intro += f"（{power_levels}）"
            return {"info": info, "state": state, "history": history, "images": images, "initial_options": initial_options, "world_intro": world_intro}
        except Exception as e:
            logger.error("Load game failed: %s", e, exc_info=True)
            return {"error": f"加载游戏失败: {e}"}


@router.post("/input")
async def player_input(req: PlayerInputRequest):
    engine = get_engine()
    if not engine or not engine.player_state:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    try:
        # [v9] 使用并发锁防止多请求同时修改游戏状态
        async with engine._game_lock:
            result = await asyncio.to_thread(engine.process_player_input, req.input)
            if hasattr(engine, 'npc_registry') and engine.npc_registry and result.get("narrative"):
                day = engine.world_state.current_day if engine.world_state else 0
                npc_knowledge = await asyncio.to_thread(
                    engine.npc_registry.process_narrative,
                    result["narrative"], req.input, day
                )
                if npc_knowledge.get("new_rumors"):
                    rumor_text = "\n\n".join(["📜 【江湖传闻】" + r for r in npc_knowledge["new_rumors"]])
                    result["narrative"] = result["narrative"] + "\n\n" + rumor_text
            state = engine.get_game_state()
            # [Bug] process_player_input 内部已调用 save_game("auto")，此处不再重复保存
        # [v1.4 P1-7] 统一 narrative 持久化：
        # narrative 现已由 TurnProcessorV2._save_snapshot 写入每世界 history.db，
        # 不再冗余写入 MetaDB.narrative 表（搜索/统计接口已改读 history.db）。
        # 仅更新 worlds 表的 last_saved_at 时间戳。
        try:
            db = get_meta_db()
            db.update_world_saved(engine.current_world_id)
        except Exception as e:
            logger.warning("Failed to update world_saved in MetaDB: %s", e)
        return {"result": result, "state": state}
    except Exception as e:
        logger.error("Player input failed: %s", e, exc_info=True)
        return {"error": f"处理输入失败: {e}"}


# ── [v1.5 第一期] 事件响应接口 ──────────────────────────────────────────

def _generate_bridge_narrative(engine, evt, npc) -> str:
    """[v1.5 第一期] 生成玩家接受事件后的"桥接叙事"（LLM 生成 1 段，失败则用模板）

    桥接叙事用于在玩家点"应门"后、正式进入 NPC 聊天之前，
    用一段叙事文本自然过渡（避免界面突兀切换）。
    """
    npc_name = getattr(npc, "name", "来客") if npc else "来客"
    npc_role = getattr(npc, "role", "") if npc else ""
    npc_personality = getattr(npc, "personality", "") if npc else ""
    npc_speaking = getattr(npc, "speaking_style", "") if npc else ""
    evt_title = evt.title or ""
    evt_type = evt.event_type or ""
    priority = evt.priority or "normal"
    day = engine.world_state.current_day if engine.world_state else 1
    time_str = engine.world_state.current_time if engine.world_state else ""
    player_name = engine.player_state.name if engine.player_state else "玩家"
    location = engine.player_state.location if engine.player_state else ""

    # 优先用 LLM 生成更长的桥接叙事（约 4-6 句）
    if engine.llm:
        try:
            prompt = (
                f"你是叙事小说家，正在为一个虚拟世界游戏写一段【事件桥接叙事】。\n"
                f"玩家：{player_name}，位于 {location}\n"
                f"当前：第{day}天 {time_str}\n"
                f"事件：{evt_title}（类型：{evt_type}，紧迫度：{priority}）\n"
                f"上门的 NPC：{npc_name}"
                + (f"（身份：{npc_role}）" if npc_role else "")
                + (f"，性格：{npc_personality}" if npc_personality else "")
                + (f"，说话风格：{npc_speaking}" if npc_speaking else "")
                + "\n\n"
                f"请写 4-6 句话的第三人称叙事，描述玩家前去应门/接待这位访客的过程，"
                f"包括：玩家起身/出门的动作、见到的场景、对方的状态与神情、"
                f"对方开口说出的第一句话（用引号包裹）。\n"
                f"要求：\n"
                f"- 第二人称视角称呼玩家为\"你\"\n"
                f"- 不要替玩家做决定或回应\n"
                f"- 文风简洁、有画面感\n"
                f"- 直接输出叙事文本，不要任何解释或前后缀"
            )
            bridge = engine.llm.chat(prompt, temperature=0.8, max_tokens=400)
            bridge = (bridge or "").strip()
            if bridge and len(bridge) > 20:
                return bridge
        except Exception as e:
            logger.warning("Bridge narrative LLM generation failed: %s", e)

    # 回退：模板
    mood = "急切" if priority == "urgent" else ("平和" if priority == "important" else "随意")
    return (
        f"你听到动静，起身前去开门。只见{name_var(npc_name)}站在门口，"
        f"神情{mood}。{name_var(npc_name)}见到你，开口道："
        f"\"{evt_title}。\""
    )


def name_var(name: str) -> str:
    """简单辅助：避免重复书写变量名"""
    return name


@router.post("/events/{event_id}/respond")
async def respond_event(event_id: str, req: EventRespondRequest):
    """[v1.5 第一期] 玩家响应事件（接受/拒绝/忽略/查看）

    - accept：接受事件。玩家事件 → 生成桥接叙事 + 返回 npc_id 供前端进入聊天；
                       世界事件 → 标记为已查看，注入到主叙事。
    - reject：拒绝事件。事件标记为 rejected，可能有后果（第二期实现）。
    - ignore：忽略事件。事件保持 pending，玩家可稍后处理。
    - view：仅查看事件详情，不改状态。
    """
    engine = get_engine()
    if not engine or not engine.player_state:
        raise HTTPException(status_code=503, detail="游戏未初始化")

    action = (req.action or "view").lower()
    if action not in ("accept", "reject", "ignore", "view"):
        raise HTTPException(status_code=400, detail="action 必须是 accept/reject/ignore/view")

    # 在两个 bus 中查找事件
    evt = None
    bus = None
    if getattr(engine, 'player_event_bus', None):
        evt = engine.player_event_bus.find(event_id)
        if evt:
            bus = engine.player_event_bus
    if not evt and getattr(engine, 'world_event_bus', None):
        evt = engine.world_event_bus.find(event_id)
        if evt:
            bus = engine.world_event_bus
    if not evt:
        raise HTTPException(status_code=404, detail="事件不存在或已过期")

    # 仅查看：返回详情
    if action == "view":
        return {"event": evt.to_dict()}

    # 标记事件状态
    status_map = {"accept": "accepted", "reject": "rejected", "ignore": "pending"}
    new_status = status_map[action]
    if new_status != "pending":
        bus.mark(event_id, new_status)

    response: dict = {"event_id": event_id, "action": action, "status": new_status}

    # 接受事件的处理
    if action == "accept":
        if evt.category == "player" and evt.source_npc:
            # 玩家事件 + 接受 → 生成桥接叙事 + 进入 NPC 聊天
            npc = engine.npc_states.get(evt.source_npc) if engine.npc_states else None
            bridge = _generate_bridge_narrative(engine, evt, npc)
            # 把桥接叙事写入主叙事历史，让前端 addNarrative 注入
            engine.narrative_history.append({
                "type": "narrative",
                "day": engine.world_state.current_day if engine.world_state else 0,
                "time": engine.world_state.current_time if engine.world_state else "",
                "text": bridge,
                "event_id": event_id,
            })
            # 记录 NPC 主动访问的 recent_action，避免 7 天内重复
            if npc is not None:
                npc.recent_actions = (npc.recent_actions or []) + [{
                    "action_type": "player_visit",
                    "day": engine.world_state.current_day if engine.world_state else 0,
                    "event_id": event_id,
                }]
                # 只保留最近 20 条
                if len(npc.recent_actions) > 20:
                    npc.recent_actions = npc.recent_actions[-20:]
            response["bridge_narrative"] = bridge
            response["npc_id"] = evt.source_npc
            response["npc_name"] = evt.payload.get("npc_name", "")
        elif evt.category == "world":
            # 世界事件 + 接受 → 标记为已查看，注入主叙事作为"世界公告"
            announcement = f"【世界公告】{evt.title}\n{evt.summary}"
            engine.narrative_history.append({
                "type": "event",
                "day": engine.world_state.current_day if engine.world_state else 0,
                "time": engine.world_state.current_time if engine.world_state else "",
                "text": announcement,
                "event_id": event_id,
                "event_type": evt.event_type,
            })
            response["announcement"] = announcement

    # 持久化（mark 内部已 _save，但桥接叙事改了 narrative_history 也需要存档）
    try:
        engine.save_game("auto")
    except Exception as e:
        logger.warning("Auto-save after event respond failed: %s", e)

    return response


@router.get("/events")
async def list_events():
    """[v1.5 第一期] 获取当前所有待处理事件（玩家事件 + 世界事件）"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    player_events = []
    world_events = []
    if getattr(engine, 'player_event_bus', None):
        player_events = [e.to_dict() for e in engine.player_event_bus.list_pending()]
    if getattr(engine, 'world_event_bus', None):
        world_events = [e.to_dict() for e in engine.world_event_bus.list_pending()]
    return {"player_events": player_events, "world_events": world_events}


@router.post("/events/clear-expired")
async def clear_expired_events():
    """[v1.5 第一期] 清理过期事件（手动触发，跨日时已自动清理）"""
    engine = get_engine()
    if not engine or not engine.world_state:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    day = engine.world_state.current_day
    player_expired = engine.player_event_bus.expire_old(day) if engine.player_event_bus else 0
    world_expired = engine.world_event_bus.expire_old(day) if engine.world_event_bus else 0
    return {"player_expired": player_expired, "world_expired": world_expired}


@router.get("/events/history")
async def get_events_history(category: str = None, event_type: str = None,
                              day_start: int = None, day_end: int = None,
                              limit: int = 100):
    """[v1.5 第二期] 查询事件历史归档（仅内存，重启清空）

    Args:
        category: 事件类别（player/world），不传=全部
        event_type: 事件类型（visit/war/...），不传=全部
        day_start: 起始日（含），不传=不限
        day_end: 结束日（含），不传=不限
        limit: 最多返回条数（默认 100，最大 500）
    """
    engine = get_engine()
    if not engine or not engine.world_state:
        raise HTTPException(status_code=503, detail="游戏未初始化")

    # 限制 limit 上限
    if limit is None or limit < 1:
        limit = 100
    limit = min(int(limit), 500)

    player_history = []
    world_history = []
    if engine.player_event_bus:
        player_history = [e.to_dict() for e in engine.player_event_bus.list_archive(
            category=category, event_type=event_type,
            day_start=day_start, day_end=day_end, limit=limit,
        )]
    if engine.world_event_bus:
        world_history = [e.to_dict() for e in engine.world_event_bus.list_archive(
            category=category, event_type=event_type,
            day_start=day_start, day_end=day_end, limit=limit,
        )]

    return {
        "player_events": player_history,
        "world_events": world_history,
        "player_stats": engine.player_event_bus.archive_stats() if engine.player_event_bus else {},
        "world_stats": engine.world_event_bus.archive_stats() if engine.world_event_bus else {},
        "filters": {
            "category": category, "event_type": event_type,
            "day_start": day_start, "day_end": day_end, "limit": limit,
        },
    }


@router.post("/undo")
async def undo_last_turn():
    """[v11] 撤销最后一次玩家行动及AI回复"""
    engine = get_engine()
    if not engine or not engine.player_state:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    try:
        async with engine._game_lock:
            undo_result = await asyncio.to_thread(engine.undo_last_turn)
            state = engine.get_game_state() if undo_result.get("success") else None
        if undo_result.get("success"):
            logger.info("Undo successful: removed %d entries", undo_result.get("removed", 0))
        return {"success": undo_result.get("success", False),
                "removed": undo_result.get("removed", 0),
                "remaining": undo_result.get("remaining", 0),
                "error": undo_result.get("error"),
                "state": state}
    except Exception as e:
        logger.error("Undo failed: %s", e, exc_info=True)
        return {"success": False, "error": f"撤销失败: {e}"}


@router.post("/event")
async def trigger_event():
    engine = get_engine()
    if not engine or not engine.world_state:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    try:
        # [v10.5] 加 _game_lock，防止与 /input 并发修改游戏状态导致存档损坏
        async with engine._game_lock:
            result = await asyncio.to_thread(engine.trigger_world_event)
            state = engine.get_game_state()
            await asyncio.to_thread(engine.save_game, "auto")
        return {"result": result, "state": state}
    except Exception as e:
        logger.error("Trigger event failed: %s", e, exc_info=True)
        return {"error": f"触发事件失败: {e}"}


@router.post("/advance")
async def advance_time():
    engine = get_engine()
    if not engine or not engine.world_state:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    try:
        # [v9] 使用并发锁防止多请求同时修改游戏状态
        async with engine._game_lock:
            result = await asyncio.to_thread(engine.advance_time)
            intro = engine.generate_morning_intro() if engine.world_state.current_time == "清晨" else ""
            state = engine.get_game_state()
            await asyncio.to_thread(engine.save_game, "auto")
        return {"state": state, "intro": intro, "sleeping_events": result.get("sleeping_npc_events", []), "npc_events": result.get("npc_events", [])}
    except Exception as e:
        logger.error("Advance time failed: %s", e, exc_info=True)
        return {"error": f"推进时间失败: {e}"}


@router.post("/save")
async def manual_save():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    ok = engine.save_game("manual")
    return {"status": "ok" if ok else "failed"}


@router.delete("/save/{world_id}")
async def delete_save(world_id: str):
    world_id = _validate_world_id(world_id)
    # [v1.3] 不再创建临时 GameEngine（会导致 _load_index 读取失败时用空字典覆盖 index.json）
    #        直接操作 MetaDB + SaveManager
    try:
        db = get_meta_db()
        db.delete_world(world_id)
    except Exception as e:
        logger.warning("Failed to delete from MetaDB: %s", e)
    from modules.save_manager import SaveManager
    sm = SaveManager(str(BASE_DIR / "saves"))
    ok = sm.delete_save(world_id)
    return {"status": "ok" if ok else "failed"}


@router.post("/generate-world")
async def generate_world(req: GenerateWorldRequest):
    # [Bug] 加 _engine_switch_lock，防止并发 generate-world 导致 check-then-act 竞态
    #       和对已有引擎的无锁重新初始化
    async with _engine_switch_lock:
        engine = get_engine()
        try:
            if not engine:
                engine = GameEngine(str(BASE_DIR / "saves"))
            # 总是使用请求中的配置重新初始化LLM，确保使用最新的API Key
            engine.init_llm(req.api_key, req.base_url, req.model_name)
            if not get_engine():
                set_engine(engine)
                _apply_image_config(engine)
            # [v9] 存储金手指设置到引擎，供后续prompt使用
            engine._golden_finger = req.golden_finger
            engine._world_type = req.world_type
            world_id = await asyncio.to_thread(engine.generate_world_from_description, req.description, req.world_type)
            # [v9] 将金手指设置写入world_def
            if engine.world_def:
                engine.world_def["golden_finger"] = req.golden_finger
            if engine.world_state:
                engine.world_state.golden_finger = req.golden_finger
            state = engine.get_game_state()
            await asyncio.to_thread(engine.save_game, "auto")
            try:
                db = get_meta_db()
                ps = state.get("player", {})
                ws = state.get("world", {})
                db.upsert_world(
                    world_id=world_id,
                    world_name=ws.get("name", "新世界"),
                    world_type=engine.world_def.get("world_type", "custom") if engine.world_def else "custom",
                    description=engine.world_def.get("description", "") if engine.world_def else "",
                    player_name=ps.get("name", "无名"),
                    player_age=ps.get("age", 18),
                    created_at=engine.meta.save_timestamp if engine.meta else "",
                    world_def=engine.world_def or {},
                )
            except Exception as e:
                logger.warning("Failed to sync world to MetaDB: %s", e)
            initial_event = ""
            if engine.world_state and engine.world_state.event_history_summary:
                initial_event = engine.world_state.event_history_summary
            world_intro = ""
            if engine.world_def:
                world_intro = engine.world_def.get("world_intro", "")
            if not world_intro and engine.world_def:
                desc = engine.world_def.get("description", "")
                name = engine.world_def.get("world_name", "未知世界")
                factions = ", ".join(engine.world_def.get("factions", {}).keys()) or "未知"
                locations = ", ".join(engine.world_def.get("locations", {}).keys()) or "未知"
                power = engine.world_def.get("power_system", {})
                power_name = power.get("name", "未知")
                power_levels = " → ".join([lv.get("name", "") for lv in power.get("levels", [])]) if power.get("levels") else ""
                world_intro = f"【{name}】\n\n{desc}\n\n势力分布：{factions}\n已知地点：{locations}\n力量体系：{power_name}"
                if power_levels:
                    world_intro += f"（{power_levels}）"
            if not initial_event and engine.world_def:
                initial_event = engine.world_def.get("initial_event", "")
            initial_options = []
            if engine.option_engine and engine.player_state:
                try:
                    scene = f"第{engine.world_state.current_day}天 {engine.world_state.current_time}，你在{resolve_location_name(engine.player_state.location, engine.world_state)}，刚刚来到这个世界"  # [Bug] location code → display name
                    initial_options = await asyncio.to_thread(engine.option_engine.generate_options, scene, engine.player_state, engine.world_state)
                except Exception as e:
                    logger.warning("Failed to generate initial options: %s", e)
                    initial_options = engine.option_engine._fallback_options(engine.player_state)
            # [Bug] 将世界简介和初始事件写入 narrative_history，确保加载存档后能看到初始内容
            try:
                from modules.state_history import StateHistoryManager
                db_path = BASE_DIR / "saves" / world_id / "history.db"
                history_mgr = StateHistoryManager(db_path)
                day = engine.world_state.current_day if engine.world_state else 1
                time_str = engine.world_state.current_time if engine.world_state else "清晨"
                if world_intro:
                    history_mgr.save_narrative_entry(
                        world_id=world_id, turn=0, day=day, time=time_str,
                        entry_type="event", narrative=world_intro,
                    )
                if initial_event:
                    history_mgr.save_narrative_entry(
                        world_id=world_id, turn=0, day=day, time=time_str,
                        entry_type="event", narrative=initial_event,
                    )
            except Exception as e:
                logger.warning("Failed to save initial narrative to history: %s", e)
            return {"world_id": world_id, "state": state, "initial_event": initial_event, "world_intro": world_intro, "initial_options": initial_options}
        except Exception as e:
            logger.error("Generate world failed: %s", e, exc_info=True)
            return {"error": str(e)}


@router.get("/better-options")
async def get_better_options():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return {"options": engine.generate_better_options()}


@router.get("/life-goals")
async def get_life_goals():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return {"goals": engine.get_life_goals(), "current": engine.check_life_goal()}


@router.post("/life-goal")
async def set_life_goal(req: SetGoalRequest):
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    # [Bug#36] 加 _game_lock
    async with engine._game_lock:
        ok = engine.set_life_goal(req.goal_type)
    return {"status": "ok" if ok else "failed"}


@router.get("/slots")
async def list_slots():
    engine = get_engine()
    if not engine:
        return {"slots": []}
    return {"slots": engine.list_slots()}


@router.post("/slot/save")
async def save_to_slot(req: SlotRequest):
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    # [Bug#36] 加 _game_lock，防止与 /input 并发修改游戏状态
    async with engine._game_lock:
        slot_id = engine.save_to_slot(req.slot_id, "手动存档")
    return {"slot_id": slot_id}


@router.post("/slot/load")
async def load_from_slot(req: SlotRequest):
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    # [Bug#36] 加 _game_lock，防止与 /input 并发修改游戏状态
    async with engine._game_lock:
        ok = engine.load_from_slot(req.slot_id)
    state = engine.get_game_state() if ok else {}
    # [Bug] slot 加载后状态变了，必须重新生成 initial_options，否则选项还是旧状态的
    initial_options = []
    if ok and engine.option_engine and engine.player_state:
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
            logger.warning("Failed to generate initial options after slot load: %s", e)
            initial_options = engine.option_engine._fallback_options(engine.player_state)
    # [Bug] slot 加载后也恢复世界观简介
    world_intro = ""
    if ok and engine.world_def:
        world_intro = engine.world_def.get("world_intro", "")
        if not world_intro:
            desc = engine.world_def.get("description", "")
            name = engine.world_def.get("world_name", "未知世界")
            factions = ", ".join(engine.world_def.get("factions", {}).keys()) or "未知"
            locations = ", ".join(engine.world_def.get("locations", {}).keys()) or "未知"
            power = engine.world_def.get("power_system", {})
            power_name = power.get("name", "未知")
            power_levels = " → ".join([lv.get("name", "") for lv in power.get("levels", [])]) if power.get("levels") else ""
            world_intro = f"【{name}】\n\n{desc}\n\n势力分布：{factions}\n已知地点：{locations}\n力量体系：{power_name}"
            if power_levels:
                world_intro += f"（{power_levels}）"
    return {"status": "ok" if ok else "failed", "state": state, "initial_options": initial_options, "world_intro": world_intro}


@router.delete("/slot/{slot_id}")
async def delete_slot(slot_id: str):
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    ok = engine.delete_slot(slot_id)
    return {"status": "ok" if ok else "failed"}


class RewindRequest(BaseModel):
    slot_id: str


@router.post("/hundred-book/rewind")
async def hundred_book_rewind(req: RewindRequest):
    """[v11] 百世书回滚：封印当前生命 → 加载目标存档 → 删除后续存档"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    # [Bug#36] 加 _game_lock，防止与 /input 并发修改游戏状态
    async with engine._game_lock:
        result = engine.hundred_book_rewind(req.slot_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    # 加载存档后重新生成选项
    initial_options = []
    if engine.option_engine and engine.player_state:
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
            logger.warning("Failed to generate options after rewind: %s", e)
            initial_options = engine.option_engine._fallback_options(engine.player_state)
    result["initial_options"] = initial_options
    return result


@router.get("/experience")
async def get_experience():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    return {"result": engine.add_experience(0)}


@router.post("/experience")
async def add_experience(req: AddExpRequest):
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    # [Bug#36] 加 _game_lock
    async with engine._game_lock:
        result = engine.add_experience(req.amount)
        state = engine.get_game_state()
    return {"result": result, "state": state}


# ── v7 新增端点 ──────────────────────────────────────────

class GroupChatRequest(BaseModel):
    player_input: str = ""
    npc_ids: list[str] = Field(default_factory=list)

class NovelImportRequest(BaseModel):
    text: str = ""
    world_type: str = "auto"

@router.post("/group-chat")
async def group_chat(req: GroupChatRequest):
    """群聊/多NPC对话"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    # [Bug#36] 加 _game_lock，防止与 /input 并发
    async with engine._game_lock:
        result = await asyncio.to_thread(engine.process_group_input, req.player_input, req.npc_ids or None)
    return result

@router.post("/import-novel")
async def import_novel(req: NovelImportRequest):
    """从小说文本导入世界"""
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="游戏未初始化")
    # [Bug] 使用 asyncio.to_thread 包装同步阻塞方法，避免阻塞事件循环
    result = await asyncio.to_thread(engine.import_novel, req.text, req.world_type)
    return result


# ── v9 新增：查看已有内容 ──────────────────────────────────────────

@router.get("/narrative-history/{world_id}")
async def get_narrative_history(world_id: str):
    """获取指定世界的叙事历史"""
    world_id = _validate_world_id(world_id)
    from pathlib import Path
    from modules.state_history import StateHistoryManager

    db_path = BASE_DIR / "saves" / world_id / "history.db"
    if not db_path.exists():
        # [Bug] 回退到从 narrative_history 文件读取，同时兼容 JSONL 和 JSON 格式
        hist_path = BASE_DIR / "saves" / world_id / "narrative_history.json"
        jsonl_path = BASE_DIR / "saves" / world_id / "state" / "narrative_history.jsonl"
        if jsonl_path.exists():
            try:
                entries = []
                with open(jsonl_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            entries.append(json.loads(line))
                return {"entries": entries}
            except Exception as e:
                logger.warning("Failed to read narrative_history.jsonl: %s", e)
        if hist_path.exists():
            try:
                entries = json.loads(hist_path.read_text(encoding="utf-8"))
                return {"entries": entries}
            except Exception as e:
                logger.warning("Failed to read narrative_history.json: %s", e)
        return {"entries": []}

    try:
        history_mgr = StateHistoryManager(db_path)
        entries = history_mgr.get_narrative_history(world_id)
        return {"entries": entries}
    except Exception as e:
        logger.warning("Failed to load narrative history: %s", e)
        return {"entries": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# 功能1：AI世界观生成
# ═══════════════════════════════════════════════════════════════

class GenerateWorldviewRequest(BaseModel):
    world_type: str = "custom"
    existing_description: str = ""


@router.post("/generate-worldview")
async def generate_worldview(req: GenerateWorldviewRequest):
    """根据世界类型和已有描述，AI生成世界观和主角身份。
    如果 world_type 为 custom，则从8种世界类型中随机选择一种。
    如果已有描述不为空，则基于已有描述扩展。
    返回300-500字的世界观+主角身份设定。"""
    import random
    from pathlib import Path
    from modules.llm.mimo_llm import MimoLLM

    engine = get_engine()
    llm = None

    if engine and engine.llm:
        llm = engine.llm
    else:
        config_path = Path(__file__).parent.parent / "config.json"
        if config_path.exists():
            try:
                raw = json.loads(config_path.read_text(encoding="utf-8"))
                cfg = decrypt_config_keys(raw)
                llm_cfg = cfg.get("llm", {})
                api_key = llm_cfg.get("api_key", "")
                base_url = llm_cfg.get("base_url", "")
                model_name = llm_cfg.get("model_name", "")
                max_tokens = llm_cfg.get("max_tokens", 0)
                if api_key and base_url and model_name:
                    llm = MimoLLM(
                        api_key=api_key,
                        base_url=base_url,
                        model_name=model_name,
                        default_max_tokens=max_tokens,
                    )
            except Exception as e:
                logger.warning("Failed to create temp LLM for worldview: %s", e)

    if not llm:
        return {"ok": False, "msg": "请检查模型配置", "worldview": ""}

    world_type = req.world_type
    existing_description = (req.existing_description or "").strip()

    WORLD_TYPE_MAP = {
        "custom": "随机世界",
        "historical": "历史穿越",
        "fantasy": "奇幻冒险",
        "scifi": "科幻未来",
        "postapocalyptic": "末日生存",
        "wuxia": "武侠江湖",
        "xianxia": "修仙问道",
        "modern": "现代生活",
        "urban_fantasy": "都市异能",
    }

    if world_type == "custom":
        import random
        types_list = [k for k in WORLD_TYPE_MAP.keys() if k != "custom"]
        world_type = random.choice(types_list)

    world_type_name = WORLD_TYPE_MAP.get(world_type, world_type)

    prompt = f"""
你是一个专业的世界设定师。请根据以下要求，生成一个完整的世界观和主角身份设定。

【世界类型】{world_type_name}

【已有描述】
{existing_description if existing_description else "（无）"}

【任务要求】
1. 如果已有描述不为空，基于已有描述扩展生成；如果为空，则完全由你创作
2. 生成内容包括两部分：
   - 世界观设定（世界背景、势力分布、力量体系等）
   - 主角身份设定（姓名、年龄、出身、目标、性格特点）
3. 字数控制在300-500字之间
4. 风格要符合该世界类型的特点
5. 主角身份要有故事性和可塑性，适合作为游戏的起点

【输出格式】
直接输出设定文本，不需要任何前缀或后缀。

示例（武侠江湖）：
世界观：大炎王朝末年，朝廷腐败，江湖纷乱。武林分为正道八大门派与魔道三宗，还有神秘的杀手组织"暗影阁"在暗处搅动风云。西域魔教觊觎中原已久，江湖暗流涌动。

主角：李清风，二十一岁，出身江南书香门第。其父是被冤枉的忠臣，全家被灭门时侥幸逃脱，后被隐世高人收养传授武艺。性格外柔内刚，心怀家国大义，誓要查明真相、重振家门。目前以游历江湖的书生身份行走天下，暗中调查当年灭门案的线索。
"""

    try:
        result = await asyncio.to_thread(llm.chat, prompt, temperature=0.7, max_tokens=800)
        worldview = (result or "").strip()
        if worldview:
            return {"ok": True, "msg": "生成成功", "worldview": worldview, "world_type": world_type}
        else:
            return {"ok": False, "msg": "生成内容为空，请重试", "worldview": ""}
    except Exception as e:
        logger.error("Failed to generate worldview: %s", e)
        err_str = str(e)
        if "401" in err_str or "api key" in err_str.lower() or "authentication" in err_str.lower():
            return {"ok": False, "msg": "请检查模型配置", "worldview": ""}
        return {"ok": False, "msg": f"生成失败: {err_str[:100]}", "worldview": ""}
    finally:
        if not engine and llm and hasattr(llm, 'close'):
            try:
                llm.close()
            except Exception:
                pass
