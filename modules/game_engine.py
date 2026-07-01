"""
[v10] GameEngine — 薄协调层
核心逻辑已拆分到 TurnProcessorV2（回合处理）和 WorldManager（世界演化）。
子系统查询、存档、世界生成、角色卡方法已抽取到 Mixin。
EventBus 提供子系统间的发布/订阅通信。

v10 新增：
  - 闭环学习系统（NarrativeReviewer）
  - NPC 程序性记忆（NPCProceduralMemory）
  - 世界任务板（WorldTaskBoard）
  - 记忆 Curator（MemoryCurator）
  - 蝴蝶效应审批门
  - 分层记忆 + 重要性衰减
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from .schemas import (
    PlayerState, WorldState, NPCState, SaveMeta, Stats, Social,
    Inventory, RelationEntry, PlayerMemory, MacroEvent,
)
from .prompt_utils import resolve_location_name  # [Bug] location code → display name
from .save_manager import SaveManager
from .llm.mimo_llm import MimoLLM
from .llm.router import LLMRouter, BaseLLM, TASK_DIALOGUE, TASK_SIMPLE
from .db.chroma_db import MemoryStore
from .core.task_queue import BackgroundTaskQueue
from .data.safe_io import atomic_write_json, load_json_safe
from .registry import ServiceRegistry, create_services, trigger_hook, _load_plugins
from .lorebook import Lorebook
from .npc_autonomous import NpcAutonomous
from .narrative_timekeeper import NarrativeTimekeeper
from .event_bus import EventBus
from .player_agent import PlayerAgent
from .world_agent import WorldAgent
from .level_system import LevelSystem, GodsCodex
from .group_chat import GroupChatManager
from .novel_importer import NovelImporter
from .graph_rag import GraphRAG
from .npc_registry import NpcRegistry
# [v9] Mixin 导入
from .engine_save_mixin import SaveMixin
from .engine_world_mixin import WorldGenMixin
from .engine_card_mixin import CharacterCardMixin
from .engine_query_mixin import SubsystemQueryMixin

logger = logging.getLogger("chronoverse")


class GameEngine(SaveMixin, WorldGenMixin, CharacterCardMixin, SubsystemQueryMixin):
    # [v10] 叙事历史上限 — 仅触发摘要生成，不再替换/截断原始记录
    MAX_NARRATIVE_HISTORY = 500
    NARRATIVE_HISTORY_KEEP = 500

    def __init__(self, save_dir: str = "./saves"):
        self.save_manager = SaveManager(save_dir)
        # [v10.5] 文本嵌入函数（init_llm 时从配置创建）
        self._embedding_function = None
        self.services: ServiceRegistry | None = None
        self.llm: BaseLLM | None = None
        self.main_llm: MimoLLM | None = None
        self.cheap_llm: MimoLLM | None = None
        # [v10.5+] 对话模型：用于游戏内叙事/NPC对话；未配置时为 None（Router 内回退到主力）
        self.dialogue_llm: MimoLLM | None = None

        # [v10.1] 后台任务队列
        self.task_queue = BackgroundTaskQueue()

        # [v9] 事件总线 — 子系统间解耦通信
        self.event_bus = EventBus()

        # [v9] 并发锁 — 防止多请求同时修改游戏状态
        self._game_lock = asyncio.Lock()

        # [v10+++] 存档线程锁 — 防止 NpcSpawner 后台线程与主线程同时 save_game 导致 Windows 文件占用冲突
        self._save_lock = threading.Lock()

        # [v10.5] 初始化锁 — 保护 _init_services 的 check-then-act，防止并发竞态
        self._init_lock = threading.Lock()

        # [v10.5] 实例级插件钩子表 — 替代全局 _plugin_hooks，实现多实例隔离
        self._plugin_hooks: dict[str, list] = {}
        self._plugin_hooks_lock = threading.Lock()

        # [v10.5] 懒加载服务私有存储 — 首次访问时通过 @property 创建
        self._lazy_world_generator = None
        self._lazy_world_template = None
        self._lazy_novel_importer = None
        self._lazy_character_card = None
        self._lazy_npc_prediction = None

        # 以下属性由 _init_services() 从 registry 填充
        self.player_agent: PlayerAgent | None = None
        self.world_agent: WorldAgent | None = None
        self.narrative = None
        # [v10++] 上下文引擎（注意力预算 + 提示压缩 + Prompt Caching）
        self.context_engine = None
        self.memory: MemoryStore | None = None
        self.age_system = None
        self.economy_system = None
        self.butterfly = None
        self.option_engine = None
        self.streamer = None
        self.world_generator = None
        self.level_system: LevelSystem | None = None
        self.gods_codex: GodsCodex | None = None
        self.destiny_regret = None
        self.brain_whispers = None
        self.memoir = None
        self.favor_events = None
        self.faction_wars = None
        self.visual_engine = None
        self.npc_perception = None
        self.influence_network = None
        self.rag_historical = None
        # [Bug] 初始化所有子系统属性为 None，避免 _init_services 前访问报错
        self.death_system = None
        self.hundred_life_book = None
        self.item_system = None
        self.skill_tree = None
        self.quest_system = None
        self.lorebook = None
        self.npc_agent = None
        self.reputation_system = None
        self.weather_effects = None
        self.npc_life_evolution = None
        self.llm_cache = None
        self.npc_autonomous = None
        # v7 新增
        self.branch_planner = None
        self.group_chat: GroupChatManager | None = None
        self.novel_importer: NovelImporter | None = None
        self.graph_rag: GraphRAG | None = None
        self.character_card = None
        self.current_level_system_type: str = "none"
        # [v10] 新增服务引用
        self.narrative_reviewer = None
        self.npc_procedural_memory = None
        self.world_task_board = None
        self.memory_curator = None
        # [v10++] NPC 技能自学库（Voyager/Hermes 式）
        self.npc_skill_library = None
        # [v10+] 新增服务引用
        self.foreshadow_lifecycle = None
        self.continuity_auditor = None
        # [v10++] 角色动态状态管理器（CHIRON 式）
        self.character_state_manager = None
        # [v10++] NPC 反思机制（Generative Agents 式）
        self.npc_reflection = None
        # [v10+] 混合检索（BM25 + 向量 + GraphRAG）
        self.bm25_retriever = None
        self.hybrid_retriever = None
        # [v10+] 叙事场景检测器（GraphRAG 动态启停）
        self.scene_detector = None
        # [v11] 蝴蝶效应异步结果缓存（下回合应用）
        self._pending_butterfly_result: dict | None = None
        # [v10++] Agent 自主记忆管理（MemGPT/Letta 式）
        self.autonomous_memory = None
        # [v10+++] 多智能体分工叙事（Agents' Room 式）：仅用于关键剧情
        self.multi_agent_narrative = None
        # [v10++] MCP 工具协议层（Model Context Protocol 兼容）
        self.mcp_registry = None

        # [v9] 回合处理器和世界管理器（延迟初始化）
        self._turn_processor = None
        self._world_manager = None

        self.current_world_id: str = ""
        self.world_state: WorldState | None = None
        self.player_state: PlayerState | None = None
        self.npc_states: dict[str, NPCState] = {}
        self.meta: SaveMeta | None = None
        self.world_def: dict | None = None

        self.event_log_today: list[dict] = []
        self.action_log_today: list[str] = []
        self.player_impacts_today: list[str] = []
        self.world_changes_today: list[str] = []
        self.narrative_history: list[dict] = []
        self._persisted_narrative_count: int = 0  # [v10.1] 已持久化到 JSONL 的条目数，避免每次全量读取
        self._narrative_compressed: bool = False  # [v10.1] 标记 narrative_history 是否被 Curator 压缩过
        self.last_novel_checkpoint: int = 0
        self.dialogue_count_since_image: int = 0
        self.next_auto_image_at: int = 3
        self.turns_since_audit: int = 0
        self.audit_interval: int = 5  # 每5轮对话执行一次身份审计
        self._last_year_evolved: int = 0  # 上次年度演化的天数
        self.timekeeper: NarrativeTimekeeper | None = None  # 叙事时间感知器
        self._stream_callback = None  # 当前活跃的 WebSocket 流式回调（供 TurnProcessor 调用）
        # [v10] 按 client_id 绑定的回调字典，避免多客户端互相覆盖（H21）
        self._stream_callbacks: dict[str, object] = {}
        self._config_cache: dict | None = None  # config.json 内存缓存
        self.npc_registry: NpcRegistry = NpcRegistry(max_passersby=10)
        # [v10+++] 异步 NPC 生成器（懒加载，首次调用时创建）
        self._npc_spawner = None

    @property
    def npc_spawner(self):
        """[v10+++] 异步 NPC 生成器（懒加载）"""
        if self._npc_spawner is None:
            from .npc_spawner import NpcSpawner
            self._npc_spawner = NpcSpawner(self)
        return self._npc_spawner

    def _bound_llm(self, task_type: str) -> BaseLLM:
        """[v10.5+] 返回绑定指定 task_type 的 LLM 视图。
        - 若 self.llm 是 LLMRouter，返回 TaskBoundLLM 代理（自动路由到对应模型）
        - 否则直接返回 self.llm（兼容测试场景）
        用于 PlayerAgent/WorldAgent/NpcAutonomous 等直接创建的子系统绑定默认 task_type。"""
        if isinstance(self.llm, LLMRouter):
            return self.llm.bind_task_type(task_type)
        return self.llm

    # ── [v10.5] 实例级插件钩子 ──────────────────────────────

    def register_hook(self, hook_name: str, callback):
        """注册一个插件钩子到当前 engine 实例（实例级隔离，不污染全局表）。"""
        with self._plugin_hooks_lock:
            if hook_name not in self._plugin_hooks:
                self._plugin_hooks[hook_name] = []
            self._plugin_hooks[hook_name].append(callback)
        logger.info("Plugin hook registered (instance): %s -> %s", hook_name, getattr(callback, "__name__", str(callback)))

    def trigger_hook(self, hook_name: str, **kwargs):
        """触发当前 engine 实例的插件钩子。
        若实例级表无此钩子，回退到全局 _plugin_hooks（向后兼容）。"""
        # 实例级钩子优先
        with self._plugin_hooks_lock:
            hooks = list(self._plugin_hooks.get(hook_name, []))
        for callback in hooks:
            try:
                callback(**kwargs)
            except Exception as e:
                logger.warning("Plugin hook '%s' error: %s", hook_name, e)
        # 回退：若实例级表完全为空（未加载插件），尝试全局表
        if not hooks:
            from .registry import trigger_hook as _global_trigger
            _global_trigger(hook_name, **kwargs)

    # ── [v10.5] 懒加载服务属性 ──────────────────────────────
    # 这些工具型服务仅在特定 API 端点首次调用时创建，减少启动开销

    @property
    def world_generator(self):
        """懒加载：世界生成器（仅 /api/generate-world 使用）"""
        if self._lazy_world_generator is None and self.llm is not None:
            from .world_generator import WorldGenerator
            self._lazy_world_generator = WorldGenerator(self.llm)
        return self._lazy_world_generator

    @world_generator.setter
    def world_generator(self, value):
        self._lazy_world_generator = value

    @property
    def world_template(self):
        """懒加载：世界模板（仅世界生成时使用）"""
        if self._lazy_world_template is None and self.llm is not None:
            from .world_template import WorldTemplate
            self._lazy_world_template = WorldTemplate(self.llm)
        return self._lazy_world_template

    @world_template.setter
    def world_template(self, value):
        self._lazy_world_template = value

    @property
    def novel_importer(self):
        """懒加载：小说导入器（仅 /api/import-novel 使用）"""
        if self._lazy_novel_importer is None and self.llm is not None:
            from .novel_importer import NovelImporter
            self._lazy_novel_importer = NovelImporter(self.llm)
        return self._lazy_novel_importer

    @novel_importer.setter
    def novel_importer(self, value):
        self._lazy_novel_importer = value

    @property
    def character_card(self):
        """懒加载：角色卡工具（仅 /api/character-card 使用）"""
        if self._lazy_character_card is None:
            from .character_card import CharacterCard
            self._lazy_character_card = CharacterCard()
        return self._lazy_character_card

    @character_card.setter
    def character_card(self, value):
        self._lazy_character_card = value

    @property
    def npc_prediction(self):
        """懒加载：NPC推演引擎（仅 /api/npc-prediction 使用）"""
        if self._lazy_npc_prediction is None and self.llm is not None:
            from .npc_prediction import NpcPredictionEngine
            self._lazy_npc_prediction = NpcPredictionEngine(
                llm=self.llm,
                graph_rag=self.graph_rag,
                npc_registry=self.npc_registry,
                character_state_manager=self.character_state_manager,
            )
        return self._lazy_npc_prediction

    @npc_prediction.setter
    def npc_prediction(self, value):
        self._lazy_npc_prediction = value

    def _load_config(self) -> dict:
        """一次性加载 config.json 并缓存到内存，避免每次调用都读磁盘。
        [P2-6] 自动解密 api_key 字段，调用方无需再手动 decrypt_config_keys。"""
        if self._config_cache is not None:
            return self._config_cache
        config_path = Path(__file__).parent.parent / "config.json"
        if config_path.exists():
            try:
                raw = json.loads(config_path.read_text(encoding="utf-8"))
                # [P2-6] 集中化解密：所有通过 _load_config 获取的 config 都是解密后的
                from .security import decrypt_config_keys
                self._config_cache = decrypt_config_keys(raw)
            except Exception as e:
                logger.warning("Failed to load config.json: %s", e)
                self._config_cache = {}
        else:
            self._config_cache = {}
        return self._config_cache

    def invalidate_config_cache(self):
        """config.json 被修改后调用此方法清除缓存"""
        self._config_cache = None

    def _init_services(self, force: bool = False):
        """通过服务注册表集中创建所有子系统（消除循环导入风险）。
        force=True 时强制重建；默认仅创建尚未初始化的子系统。
        [v10.5] 使用 threading.Lock 保护 check-then-act，防止并发竞态；
                插件钩子注册到 engine 实例而非全局表。
        [Bug] 所有属性赋值必须在锁内完成，否则并发 _init_services(force=False)
              可能在属性赋值完成前返回，读到 None 属性。"""
        # [v10.5] 线程安全保护：防止并发 _init_services 导致重复创建和插件累积
        with self._init_lock:
            if self.services and not force:
                return
            # [v10.5] force 重建时先清空实例级钩子表，防止重复注册累积
            if force:
                with self._plugin_hooks_lock:
                    self._plugin_hooks.clear()
            self.services = create_services(self.llm, self.save_manager)
            svc = self.services
            # [Bug] 以下所有属性赋值必须在锁内，防止并发读到半初始化的引擎
            self.lorebook = svc.lorebook
            self.narrative = svc.narrative
            self.age_system = svc.age_system
            self.npc_agent = svc.npc_agent
            # [Bug] 经济系统根据配置开关决定是否启用
            _econ_cfg = self._load_config().get("game", {})
            self.economy_system = svc.economy_system if _econ_cfg.get("economy_enabled", False) else None
            self.butterfly = svc.butterfly
            self.option_engine = svc.option_engine
            self.streamer = svc.streamer
            # [v10.5] world_generator 改为懒加载属性，不在此赋值
            self.brain_whispers = svc.brain_whispers
            self.memoir = svc.memoir
            self.favor_events = svc.favor_events
            self.faction_wars = svc.faction_wars
            self.visual_engine = svc.visual_engine
            self.gods_codex = svc.gods_codex
            self.destiny_regret = svc.destiny_regret
            self.death_system = svc.death_system
            self.hundred_life_book = svc.hundred_life_book
            self.item_system = svc.item_system
            self.skill_tree = svc.skill_tree
            self.quest_system = svc.quest_system
            # [v10.5] world_template 改为懒加载属性，不在此赋值
            self.weather_effects = svc.weather_effects
            self.reputation_system = svc.reputation_system
            self.llm_cache = svc.llm_cache
            self.npc_perception = svc.npc_perception
            self.influence_network = svc.influence_network
            self.rag_historical = svc.rag_historical
            self.npc_life_evolution = svc.npc_life_evolution
            self.npc_autonomous = svc.npc_autonomous
            # v7 新增服务
            self.branch_planner = svc.branch_planner
            self.group_chat = svc.group_chat
            # [v10.5] novel_importer 和 character_card 改为懒加载属性，不在此赋值
            self.graph_rag = svc.graph_rag
            # [v10.5] character_card 改为懒加载属性
            # [v10+] 新增服务
            self.narrative_reviewer = svc.narrative_reviewer
            self.npc_procedural_memory = svc.npc_procedural_memory
            self.world_task_board = svc.world_task_board
            self.memory_curator = svc.memory_curator
            # [v10++] NPC 技能自学库（Voyager/Hermes 式）
            self.npc_skill_library = svc.npc_skill_library
            # [v10+] 新增服务
            self.foreshadow_lifecycle = svc.foreshadow_lifecycle
            self.continuity_auditor = svc.continuity_auditor
            # [v10++] 上下文引擎：注入到 PlayerAgent（若已创建）和 NarrativeEngine
            self.context_engine = svc.context_engine
            if self.player_agent and self.context_engine:
                self.player_agent.set_context_engine(self.context_engine)
            # [v10++] 角色动态状态管理器（CHIRON 式）
            self.character_state_manager = svc.character_state_manager
            # 将动态状态管理器注入 NPCAgent，供独立交互时使用
            if self.npc_agent is not None:
                self.npc_agent.character_state_manager = self.character_state_manager
            # 将动态状态管理器注入 PlayerAgent，供直接调用时使用
            if self.player_agent is not None:
                self.player_agent.character_state_manager = self.character_state_manager
            # [v11] 将伏笔生命周期管理器注入 PlayerAgent，供线索扩展使用
            if self.player_agent is not None and self.foreshadow_lifecycle is not None:
                self.player_agent.foreshadow_lifecycle = self.foreshadow_lifecycle
            # [v10++] NPC 反思机制（Generative Agents 式）
            self.npc_reflection = svc.npc_reflection
            # 将反思管理器注入 NPCAgent，供构建决策 prompt 时注入洞察
            if self.npc_agent is not None:
                self.npc_agent.reflection_manager = self.npc_reflection
                # [v10++] 将技能自学库注入 NPCAgent，供决策时注入可用技能、
                # 并在行动成功/失败后学习或记录失败（Voyager/Hermes 式）
                self.npc_agent.skill_library = self.npc_skill_library
            # [v10+] 混合检索（BM25 + 向量 + GraphRAG）
            self.bm25_retriever = svc.bm25_retriever
            self.hybrid_retriever = svc.hybrid_retriever
            # [v10+] 叙事场景检测器（GraphRAG 动态启停）
            self.scene_detector = svc.scene_detector
            # [v10++] Agent 自主记忆管理（MemGPT/Letta 式）
            self.autonomous_memory = svc.autonomous_memory
            # [v10+++] 多智能体分工叙事（Agents' Room 式）：仅用于关键剧情
            self.multi_agent_narrative = svc.multi_agent_narrative
            # [v10++] MCP 工具协议层：获取注册表引用并注册内置工具
            # 内置工具通过闭包绑定到 self（GameEngine），从而访问各子系统
            self.mcp_registry = svc.mcp_registry
            if self.mcp_registry is not None:
                try:
                    from .mcp_tools import register_builtin_tools
                    register_builtin_tools(self.mcp_registry, engine=self)
                    logger.info("MCP builtin tools registered with GameEngine")
                except Exception as e:
                    logger.warning("Failed to register MCP builtin tools: %s", e)

            # [v10+] 从 config.json 读取配置并应用到模块
            self._apply_v10_config()

            # [v9] 初始化回合处理器和世界管理器
            from .turn_processor_v2 import TurnProcessorV2
            from .world_manager import WorldManager
            self._turn_processor = TurnProcessorV2(self)
            self._world_manager = WorldManager(self)

            # [v10.5] 加载插件 — 钩子注册到 engine 实例（而非全局表），实现多实例隔离
            # 放在方法末尾，确保所有子系统已就绪供插件访问
            try:
                _load_plugins(svc, engine=self)
            except Exception as e:
                logger.warning("Plugin loading failed: %s", e)

    def _warmup_services(self, world_id: str):
        """[v10.6+] 游戏加载完成后主动预热各子系统，避免第一次玩家输入时才懒加载导致等待。
        预热项：
        1. SocialNetwork 社会关系网
        2. ContextEngine 上下文引擎缓存（构建一次上下文，让 Prompt Caching 生效）
        3. BM25 索引（如果有文本数据）"""
        import time
        t0 = time.time()
        warm_steps = []

        # 1. 预热社会关系网络
        if self._turn_processor and self.npc_states and self.world_state:
            if not hasattr(self._turn_processor, '_social_initialized') or not self._turn_processor._social_initialized:
                try:
                    self._turn_processor.social_network.initialize(self.npc_states, self.world_state)
                    self._turn_processor._social_initialized = True
                    warm_steps.append(f"social_network({len(self._turn_processor.social_network.links)} links)")
                except Exception as e:
                    logger.warning("Warmup social_network failed: %s", e)

        # 2. 预热 ContextEngine（构建一次 player_agent 上下文，触发缓存前缀记录）
        if self.player_agent and self.context_engine and self.player_state:
            try:
                from .prompt_utils import build_npc_context, build_world_context, build_player_context, build_history_context
                from .context_budget import estimate_tokens
                ws_dict = self.world_state.model_dump() if self.world_state else None
                world_text = build_world_context(ws_dict) if ws_dict else ""
                npc_text = build_npc_context(self.npc_states, "热身", ws_dict) if self.npc_states else ""
                player_text = build_player_context(self.player_state, ws_dict)
                history_text = build_history_context("热身", self.narrative_history) if self.narrative_history else ""
                # 用假的 system_prompt 走一遍缓存路径，不实际调用 LLM
                dummy_sys = "你是一个叙事AI助手。"
                try:
                    self.player_agent._build_context_with_engine(
                        system_prompt=dummy_sys,
                        world_text=world_text,
                        npc_text=npc_text,
                        identity_text="",
                        lorebook_text="",
                        rag_text="",
                        history_text=history_text,
                        player_text=player_text,
                        fixed_prompt="",
                        state=self.player_state,
                        max_context=self._get_max_context(),
                    )
                    warm_steps.append("context_engine(prefix_cached)")
                except Exception as e:
                    logger.debug("Warmup context_engine failed: %s", e)
            except Exception as e:
                logger.warning("Warmup context_engine setup failed: %s", e)

        # 3. 预热 BM25 索引（从记忆库加载一次）
        if self.bm25_retriever and self.hybrid_retriever:
            try:
                # hybrid_retriever 首次检索时会从 memory 加载 BM25 索引
                self.bm25_retriever.search("热身", top_k=1)
                warm_steps.append("bm25_index")
            except Exception as e:
                logger.debug("Warmup bm25 failed: %s", e)

        elapsed = (time.time() - t0) * 1000
        logger.info("Service warmup done in %.0fms: %s", elapsed, ", ".join(warm_steps) if warm_steps else "nothing to warm")

    def _apply_v10_config(self):
        """[v10+] 从 config.json 读取配置并应用到各模块"""
        config = self._load_config()
        v10_cfg = config.get("v10", {})

        # 伏笔生命周期配置
        fs_cfg = v10_cfg.get("foreshadow_lifecycle", {})
        if self.foreshadow_lifecycle:
            self.foreshadow_lifecycle.STALE_THRESHOLD_DAYS = fs_cfg.get("stale_threshold_days", 30)
            self.foreshadow_lifecycle.BURST_THRESHOLD = fs_cfg.get("burst_threshold", 8)
            self.foreshadow_lifecycle.WARN_BURST_THRESHOLD = fs_cfg.get("warn_burst_threshold", 12)
            self.foreshadow_lifecycle.reminder_mode = fs_cfg.get("reminder_mode", "normal")

        # 连续性审计配置
        ca_cfg = v10_cfg.get("continuity_auditor", {})
        if self.continuity_auditor:
            self.continuity_auditor.audit_interval = ca_cfg.get("audit_interval", 5)

        # 分层记忆配置
        lm_cfg = v10_cfg.get("layered_memory", {})
        if self.memory and hasattr(self.memory, 'configure_ranked_weights'):
            self.memory.configure_ranked_weights(
                weights={
                    "importance": lm_cfg.get("importance_weight", 0.25),
                    "emotional": lm_cfg.get("emotional_weight", 0.1),
                },
                half_life=lm_cfg.get("time_decay_half_life_days", 30),
            )

        # 叙事回顾配置
        nr_cfg = v10_cfg.get("narrative_reviewer", {})
        if self.narrative_reviewer:
            self.narrative_reviewer.review_interval = nr_cfg.get("review_interval", 5)

        # 记忆 Curator 配置
        mc_cfg = v10_cfg.get("memory_curator", {})
        if self.memory_curator:
            self.memory_curator.curate_interval = mc_cfg.get("curate_interval", 15)

        # 蝴蝶效应审批门配置
        ba_cfg = v10_cfg.get("butterfly_approval_gate", {})
        if self.butterfly:
            self.butterfly.approval_gate_enabled = ba_cfg.get("enabled", False)
            self.butterfly.approval_threshold = ba_cfg.get("threshold", 7.0)

        # [v10+++] 多智能体分工叙事配置（Agents' Room 式）
        man_cfg = v10_cfg.get("multi_agent_narrative", {})
        if self.multi_agent_narrative:
            self.multi_agent_narrative._enabled = man_cfg.get("enabled", True)
            self.multi_agent_narrative._max_revisions = man_cfg.get("max_revisions", 1)
        self.multi_agent_sensitivity = man_cfg.get("sensitivity", "normal")

    def _wire_hybrid_retrieval(self):
        """[v10+] 接线混合检索：将 MemoryStore 注入 HybridRetriever，
        重建 BM25 索引，并将 HybridRetriever 注入 PlayerAgent。
        在 create_world / load_game 创建 memory 和 player_agent 之后调用。
        """
        if not self.memory:
            return
        # 将 MemoryStore 注入 HybridRetriever 作为向量检索后端
        if self.hybrid_retriever:
            self.hybrid_retriever.set_vector_store(self.memory)
            logger.info("HybridRetriever wired to MemoryStore")
        # 将 BM25Retriever 注入 MemoryStore 并从现有记忆重建索引
        if self.bm25_retriever:
            self.memory.set_bm25_retriever(self.bm25_retriever)
        # 将 HybridRetriever 注入 PlayerAgent
        if self.player_agent and self.hybrid_retriever:
            self.player_agent.set_hybrid_retriever(self.hybrid_retriever)
        # [v10++] 将 MemoryStore 注入 NPC 反思管理器，供检索/存储洞察使用
        if self.npc_reflection:
            self.npc_reflection.set_memory_store(self.memory)
        # [v10++] 将 MemoryStore 注入 NPC 技能自学库，供语义检索/存储技能使用（Voyager/Hermes 式）
        if self.npc_skill_library:
            self.npc_skill_library.set_memory_store(self.memory)
        # [v10++] 将 MemoryStore 注入自主记忆管理器，供 Agent 自主管理记忆
        if self.autonomous_memory:
            self.autonomous_memory.set_memory_store(self.memory)
            logger.info("AutonomousMemoryManager wired to MemoryStore")

    def _get_fixed_prompt(self) -> str:
        config = self._load_config()
        fp = config.get("fixed_prompt", {})
        if fp.get("enabled", True):
            return fp.get("content", "")
        return ""

    def _get_strip_gray_narrative(self) -> bool:
        config = self._load_config()
        return config.get("ui", {}).get("strip_gray_narrative", True)

    def _get_time_context(self) -> str:
        """获取叙事时间感知上下文，注入到 LLM prompt 中"""
        if self.timekeeper:
            return self.timekeeper.get_time_context_for_prompt()
        return ""

    def _get_max_context(self) -> int:
        config = self._load_config()
        return config.get("game", {}).get("max_context", 32768)

    def _get_narrative_max_chars(self) -> int:
        """[v10.6] 获取叙事最大字数（从 config.game.narrative_max_chars 读取，默认 1000）"""
        config = self._load_config()
        return config.get("game", {}).get("narrative_max_chars", 1000)

    def init_llm(self, api_key: str, base_url: str = None, model_name: str = None,
                 cheap_api_key: str = None, cheap_base_url: str = None, cheap_model_name: str = None,
                 dialogue_api_key: str = None, dialogue_base_url: str = None, dialogue_model_name: str = None):
        config = self._load_config()
        main_key = api_key
        main_url = base_url or "https://token-plan-cn.xiaomimimo.com/v1"
        main_model = model_name or "mimo-V2.5-Pro"
        # [Bug] 从配置读取 max_tokens（0 = 不限制）
        main_max_tokens = config.get("llm", {}).get("max_tokens", 0)

        cheap_cfg = config.get("cheap_llm", {})
        c_key = cheap_api_key or cheap_cfg.get("api_key", "")
        c_url = cheap_base_url or cheap_cfg.get("base_url", "")
        c_model = cheap_model_name or cheap_cfg.get("model_name", "")

        # [v10.5+] 对话模型：从 config 读取，函数参数优先（前端 load/create 请求可传入）
        dlg_cfg = config.get("dialogue_llm", {})
        d_key = dialogue_api_key or dlg_cfg.get("api_key", "")
        d_url = dialogue_base_url or dlg_cfg.get("base_url", "")
        d_model = dialogue_model_name or dlg_cfg.get("model_name", "")

        self.main_llm = MimoLLM(
            api_key=main_key,
            base_url=main_url,
            model_name=main_model,
            default_max_tokens=main_max_tokens,
        )

        if c_key and c_url and c_model:
            try:
                self.cheap_llm = MimoLLM(
                    api_key=c_key,
                    base_url=c_url,
                    model_name=c_model,
                )
                logger.info("Cheap/fallback LLM configured: %s @ %s", c_model, c_url)
            except Exception as e:
                logger.warning("Failed to init cheap LLM: %s", e)
                self.cheap_llm = None
        else:
            self.cheap_llm = None

        # [v11] 对话模型已屏蔽，所有内容走主力模型
        self.dialogue_llm = None
        logger.info("Dialogue LLM disabled, all tasks route to main model")

        self.llm = LLMRouter(self.main_llm, self.cheap_llm, self.dialogue_llm)
        # [v10.6] 叙事最大字数（从 config 读取，供 turn_processor / player_agent 使用）
        self.narrative_max_chars = self._get_narrative_max_chars()
        # [v10.5] 初始化文本嵌入函数（SiliconFlow bge-m3）
        self._init_embedding_function(config)
        self._init_services(force=True)

    # ── [v10.6] LLM 热更新 ──────────────────────────────────
    def reload_llm_from_config(self):
        """[v10.6] 热更新 LLM 配置：从最新 config.json 重建 LLM 实例和所有服务。
        用于游戏中途修改设置后即时生效，无需重启游戏或重新加载存档。"""
        logger.info("Hot-reloading LLM config from config.json...")
        self.invalidate_config_cache()
        config = self._load_config()
        llm_cfg = config.get("llm", {})
        # 关闭旧 LLM 连接池，防止 httpx 连接泄漏
        for old_llm in (self.main_llm, self.cheap_llm, self.dialogue_llm):
            if old_llm and hasattr(old_llm, 'close'):
                try:
                    old_llm.close()
                except Exception:
                    pass
        # init_llm 会读取 config 中的 cheap_llm / dialogue_llm 配置
        # 并调用 _init_services(force=True) 重建所有子服务
        self.init_llm(
            api_key=llm_cfg.get("api_key", ""),
            base_url=llm_cfg.get("base_url"),
            model_name=llm_cfg.get("model_name"),
        )
        # 更新直接持有 LLM 引用的 Agent（不受 _init_services 管理）
        self._rebind_agent_llms()
        logger.info("LLM config hot-reloaded: main=%s, cheap=%s, dialogue=%s",
                     self.main_llm.model_name if self.main_llm else "None",
                     self.cheap_llm.model_name if self.cheap_llm else "None",
                     self.dialogue_llm.model_name if self.dialogue_llm else "None")

    def _rebind_agent_llms(self):
        """将新的 LLM 路由注入到直接持有 LLM 引用的 Agent 中。
        PlayerAgent / WorldAgent / NpcAutonomous 在 create_new_game / load_game
        中单独创建，不受 _init_services(force=True) 管理，需要手动更新。"""
        if self.player_agent:
            self.player_agent.llm = self._bound_llm(TASK_DIALOGUE)
        if self.world_agent:
            self.world_agent.llm = self._bound_llm(TASK_SIMPLE)
        if self.npc_autonomous:
            self.npc_autonomous.llm = self._bound_llm(TASK_DIALOGUE)

    def _init_embedding_function(self, config: dict | None = None):
        """[v10.5] 从配置创建 SiliconFlowEmbeddingFunction 并注入 SaveManager。
        若配置缺失或 key 为空，则不创建（ChromaDB 回退到默认 MiniLM 模型）。
        [P2-6] _load_config 已自动解密，无需再手动 decrypt_config_keys。"""
        if config is None:
            config = self._load_config()
        emb_cfg = config.get("embedding", {})
        api_key = emb_cfg.get("api_key", "")
        if not api_key:
            logger.info("Embedding API key 未配置，ChromaDB 使用默认嵌入模型")
            return
        try:
            from .db.embedding_function import SiliconFlowEmbeddingFunction
            ef = SiliconFlowEmbeddingFunction(
                api_key=api_key,
                base_url=emb_cfg.get("base_url", "https://api.siliconflow.cn/v1"),
                model_name=emb_cfg.get("model_name", "BAAI/bge-m3"),
            )
            self.save_manager.set_embedding_function(ef)
            self._embedding_function = ef
            logger.info(
                "Embedding function configured: %s @ %s",
                emb_cfg.get("model_name", "BAAI/bge-m3"),
                emb_cfg.get("base_url", "https://api.siliconflow.cn/v1"),
            )
        except Exception as e:
            logger.warning("Failed to init embedding function: %s", e)

    def create_new_game(self, world_data: dict, player_data: dict,
                        npc_data_list: list[dict], world_name: str = "新世界") -> str:
        if not self.llm:
            raise RuntimeError("请先调用 init_llm() 初始化LLM")

        self.current_world_id = self.save_manager.create_world(
            world_def_data=world_data,
            player_data=player_data,
            npc_data_list=npc_data_list,
            world_name=world_name,
        )

        loaded = self.save_manager.load_state(self.current_world_id)
        self.meta = loaded["meta"]
        self.world_state = loaded["world_state"]
        self.player_state = loaded["player_state"]
        self.npc_states = loaded["npc_states"]

        self.npc_registry = NpcRegistry(max_passersby=10)
        visibility = self._load_config().get("npc_info_visibility", "immersive")
        self.npc_registry.set_info_visibility(visibility)
        self.npc_registry.current_day = self.world_state.current_day if self.world_state else 1
        player_power = ""
        if "power_system" in world_data and "player_start" in world_data:
            player_power = world_data["player_start"].get("power_level", "")
        self.npc_registry.player_power_level = player_power

        for npc_id, npc_info in world_data.get("npcs", {}).items():
            npc_info_with_id = {"npc_id": npc_id, **npc_info}
            self.npc_registry.register_world_npc(npc_info_with_id)
        logger.info("NpcRegistry initialized with %d world NPCs", len(self.npc_registry.world_npcs))

        # v7: 为没有 MBTI 的 NPC 分配类型
        if self.npc_agent:
            for npc in self.npc_states.values():
                if not npc.mbti_type:
                    self.npc_agent.assign_mbti(npc)

        self.memory = self.save_manager.get_memory(self.current_world_id)
        self.lorebook = Lorebook()
        if self.world_state:
            self.lorebook.init_default_entries(self.world_state.world_type, self.npc_states)
        self.player_agent = PlayerAgent(self._bound_llm(TASK_DIALOGUE), self.memory, self.lorebook)
        # [v10++] 注入上下文引擎（若服务已初始化）
        if self.context_engine:
            self.player_agent.set_context_engine(self.context_engine)
        # [v10+] 接线混合检索（BM25 + 向量 + GraphRAG）
        self._wire_hybrid_retrieval()
        self.world_def = world_data
        self.world_agent = WorldAgent(
            self._bound_llm(TASK_SIMPLE), self.save_manager.get_db(self.current_world_id), world_data
        )

        if self.world_state and self.world_state.economy and self.economy_system:
            self.economy_system.initialize(self.world_state.economy)

        self.event_log_today = []
        self.action_log_today = []
        self.player_impacts_today = []
        self.world_changes_today = []

        initial_event = world_data.get("initial_event", "")
        if initial_event:
            self.world_state.event_history_summary = initial_event
            self.player_agent.update_memory(self.player_state, initial_event, 1)
            self._extract_relations_from_narrative(initial_event, world_data)

        self.npc_autonomous = NpcAutonomous(self._bound_llm(TASK_DIALOGUE))

        if self.influence_network and self.player_state and self.npc_states:
            self.influence_network.initialize_from_player(self.player_state, self.npc_states)

        if self.rag_historical and self.world_state:
            era = self.world_state.era_name or self.world_state.world_name
            self.rag_historical.load_era_knowledge(era, self.world_state.world_type)

        if self.npc_perception and self.player_state and self.npc_states:
            self.npc_perception.batch_classify(
                list(self.npc_states.values()), self.player_state, self.world_state
            )

        if self.hundred_life_book:
            self.hundred_life_book.load_book()
            self.hundred_life_book.start_new_life(
                self.player_state.name, self.world_state.current_day
            )

        self.timekeeper = NarrativeTimekeeper()

        # [v11] 同步 NPC 关系到 player_state.relations（确保 NPC 好感度能正确显示）
        self._sync_npc_relations_to_player()

        return self.current_world_id

    def load_game(self, world_id: str) -> dict:
        if not self.llm:
            raise RuntimeError("请先调用 init_llm() 初始化LLM")

        self.current_world_id = world_id

        # 优先加载最新slot，没有则加载基础存档
        timeline = self.save_manager.get_timeline(world_id)
        slots = timeline.list_slots()
        slot_narrative_history = None  # [Bug] slot 自带的 narrative_history，加载后要优先于 JSONL
        if slots:
            latest_slot = slots[-1]
            state = timeline.load_slot(latest_slot["slot_id"])
            if state:
                from .schemas import SaveMeta, WorldState, PlayerState, NPCState
                self.meta = SaveMeta(**state["meta"])
                self.world_state = WorldState(**state["world_state"])
                self.player_state = PlayerState(**state["player_state"])
                self.npc_states = {}
                for k, v in state.get("npc_states", {}).items():
                    self.npc_states[k] = NPCState(**v)
                # [Bug] slot 内部保存了 narrative_history，要记住它，避免被 JSONL 覆盖
                if isinstance(state.get("narrative_history"), list):
                    slot_narrative_history = state["narrative_history"]
            else:
                loaded = self.save_manager.load_state(world_id)
                self.meta = loaded["meta"]
                self.world_state = loaded["world_state"]
                self.player_state = loaded["player_state"]
                self.npc_states = loaded["npc_states"]
        else:
            loaded = self.save_manager.load_state(world_id)
            self.meta = loaded["meta"]
            self.world_state = loaded["world_state"]
            self.player_state = loaded["player_state"]
            self.npc_states = loaded["npc_states"]

        self.memory = self.save_manager.get_memory(world_id)
        self.lorebook = Lorebook()
        if self.world_state:
            self.lorebook.init_default_entries(self.world_state.world_type, self.npc_states)
        self.player_agent = PlayerAgent(self._bound_llm(TASK_DIALOGUE), self.memory, self.lorebook)
        # [v10++] 注入上下文引擎（若服务已初始化）
        if self.context_engine:
            self.player_agent.set_context_engine(self.context_engine)
        # [v10+] 接线混合检索（BM25 + 向量 + GraphRAG）
        self._wire_hybrid_retrieval()

        world_def = self.save_manager._read_json(
            self.save_manager.base_dir / world_id / "world_def" / "world.json"
        )
        self.world_def = world_def
        self.world_agent = WorldAgent(
            self._bound_llm(TASK_SIMPLE), self.save_manager.get_db(world_id), world_def
        )

        self._init_services()

        self._load_game_state(world_id)

        # [Bug] 如果 slot 携带了 narrative_history，并且 JSONL 没有更全的数据（同等或更少），以 slot 为准
        # 避免手动 slot 保存后被陈旧 JSONL 覆盖导致历史记录"消失"
        if slot_narrative_history is not None and len(slot_narrative_history) >= len(self.narrative_history):
            self.narrative_history = list(slot_narrative_history)
            self._persisted_narrative_count = len(self.narrative_history)
            self._narrative_compressed = False
            logger.info("Restored narrative_history from slot: %d entries (jsonl had %d)",
                        len(self.narrative_history), len(slot_narrative_history))

        if not self.timekeeper:
            self.timekeeper = NarrativeTimekeeper()
            self.timekeeper.last_game_day = self.world_state.current_day if self.world_state else 1

        if self.world_state and self.world_state.economy and self.economy_system:
            self.economy_system.initialize(self.world_state.economy)

        # [v10.6+] 主动预热：避免第一次玩家输入时才懒加载导致等待
        self._warmup_services(world_id)

        # [v11] 同步 NPC 关系到 player_state.relations
        self._sync_npc_relations_to_player()

        self.event_log_today = []
        self.action_log_today = []
        self.player_impacts_today = []
        self.world_changes_today = []

        return {
            "world_id": world_id,
            "day": self.world_state.current_day,
            "time": self.world_state.current_time,
            "turn": self.meta.current_turn,
            "player_name": self.player_state.name,
            "player_age": self.player_state.age,
            "location": self.player_state.location,
        }

    def register_stream_callback(self, client_id, callback):
        """注册按 client_id 绑定的 WebSocket 流式回调，用于打字机效果（H21）"""
        self._stream_callbacks[client_id] = callback

    def set_active_stream_client(self, client_id):
        """在处理某客户端输入前，将其回调设为活跃，供 TurnProcessor 通过 _stream_callback 调用（H21）"""
        self._stream_callback = self._stream_callbacks.get(client_id)

    def clear_stream_callback(self, client_id=None):
        """注销流式回调；传入 client_id 仅清除该客户端，否则全部清除（H21）"""
        if client_id is None:
            self._stream_callbacks.clear()
            self._stream_callback = None
        else:
            self._stream_callbacks.pop(client_id, None)
            # [Bug#18] 只在被清除的客户端是当前活跃客户端时才清空 _stream_callback，
            # 避免清除其他客户端的回调时误杀正在进行的流
            # 检查当前 _stream_callback 是否还被其他客户端引用
            if self._stream_callback is not None:
                still_active = any(cb is self._stream_callback for cb in self._stream_callbacks.values())
                if not still_active:
                    self._stream_callback = None

    def process_player_input(self, player_input: str) -> dict:
        """
        [v9] 处理玩家输入 — 委托给 TurnProcessorV2。
        保留原方法签名以保持向后兼容。
        注意：并发安全由调用方（game_routes.py）通过 _game_lock 保证。
        """
        if not self._turn_processor:
            raise RuntimeError("TurnProcessorV2 未初始化，请先调用 init_llm()")

        result = self._turn_processor.process(player_input)
        # [v10.5] TurnResult 结构化输出契约 → 转 dict 以保持后续代码向后兼容
        from .turn_result import TurnResult
        result = result.to_dict() if isinstance(result, TurnResult) else result
        # 补充 auto_event（TurnProcessor 不处理 _maybe_trigger_world_event）
        auto_event = self._maybe_trigger_world_event()
        if auto_event:
            result["auto_event"] = auto_event
            self.narrative_history.append({
                "type": "event",
                "day": self.world_state.current_day if self.world_state else 0,
                "time": self.world_state.current_time if self.world_state else "",
                "text": auto_event.get("narrative", ""),
                "event_type": auto_event.get("event_type", ""),
            })
        # 插件钩子
        # [v10.5] 使用实例级 trigger_hook 而非全局
        self.trigger_hook("on_turn_end",
                     narrative=result.get("narrative", ""),
                     player_input=player_input,
                     world_state=self.world_state,
                     player_state=self.player_state)

        # [v10.1] 长时记忆滚动摘要：每10回合自动压缩历史
        if self.memory_curator and self.meta:
            current_turn = self.meta.current_turn
            current_day = self.world_state.current_day if self.world_state else 0
            if self.memory_curator.should_summarize(current_turn):
                try:
                    summary_result = self.memory_curator.summarize_history(
                        self.narrative_history, current_turn, current_day
                    )
                    if summary_result.get("status") == "success":
                        self.narrative_history = summary_result["replacement"] + summary_result["remaining"]
                        logger.info("History summarized: %d entries compressed, %d total remaining",
                                    summary_result["summarized_count"], len(self.narrative_history))
                        result["summary_generated"] = True
                        result["summary_text"] = summary_result.get("summary", {}).get("text", "")
                        # 摘要成功后全量重写 JSONL，避免磁盘增量与内存压缩不一致
                        try:
                            narrative_file = self.save_manager.base_dir / self.current_world_id / "state" / "narrative_history.jsonl"
                            narrative_file.parent.mkdir(parents=True, exist_ok=True)
                            with open(narrative_file, "w", encoding="utf-8") as f:
                                for entry in self.narrative_history:
                                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                            self._persisted_narrative_count = len(self.narrative_history)
                            self._narrative_compressed = True
                        except Exception as rewrite_err:
                            logger.warning("Narrative JSONL full rewrite failed: %s", rewrite_err)
                except Exception as e:
                    logger.warning("History summary failed: %s", e, exc_info=True)

        # [v10.1] 人物卡闭环：更新NPC对玩家的印象
        if result.get("narrative") and self.npc_states:
            try:
                self._update_npc_impressions(player_input, result.get("narrative", ""))
            except Exception as e:
                logger.warning("NPC impression update failed: %s", e, exc_info=True)

        return result

    def _classify_action_type(self, player_input: str, narrative: str) -> str:
        text = (player_input + " " + narrative).lower()
        if any(kw in text for kw in ["战斗", "攻击", "杀", "打", "战", "剑", "刀", "拳", "武", "对战", "厮杀"]):
            return "fight"
        if any(kw in text for kw in ["危机", "死亡", "致命", "险境", "绝境", "命悬一线", "重伤", "垂死"]):
            return "crisis"
        if any(kw in text for kw in ["宝物", "秘宝", "奇遇", "宝藏", "灵药", "神兵", "法宝", "捡到"]):
            return "treasure"
        if any(kw in text for kw in ["丹药", "服用", "炼药", "吃药", "灵丹", "吞服", "药"]):
            return "pill"
        if any(kw in text for kw in ["事件", "灾", "变故", "异变", "天降", "突变", "意外"]):
            return "event"
        if any(kw in text for kw in ["对话", "交谈", "聊天", "谈话", "倾诉", "商议", "劝说"]):
            return "dialogue"
        if any(kw in text for kw in ["休息", "睡觉", "歇", "安歇", "入眠", "养神", "静坐"]):
            return "rest"
        return "other"

    def _update_npc_impressions(self, player_input: str, narrative: str):
        """
        [v10.1] 人物卡闭环：更新NPC对玩家的印象
        - 简单规则更新信任度和互动计数
        - 每3次互动调用LLM更新印象总结
        """
        # TODO: 当前 trust_delta 基于全局文本关键词计算，对所有被提及 NPC 应用相同变更，
        # 未做到 NPC 特异（即同一行为对不同 NPC 应有不同信任度影响）。
        # 完整修复需要结合上下文与 NPC 性格做差异化计算，暂保留以避免崩溃。
        current_day = self.world_state.current_day if self.world_state else 0
        npcs_to_update = []
        text = player_input + " " + narrative

        for npc_id, npc in self.npc_states.items():
            if npc.name and len(npc.name) >= 2 and npc.name in text:
                imp = npc.impression_of_player
                imp["interaction_count"] = imp.get("interaction_count", 0) + 1
                imp["last_updated_day"] = current_day

                trust_delta = 0
                positive_kws = ["感谢", "感激", "帮忙", "救", "赠", "送", "友好", "微笑", "点头", "称赞", "欣赏", "信任"]
                negative_kws = ["骗", "偷", "抢", "杀", "打", "骂", "威胁", "恐吓", "愤怒", "厌恶", "憎恨", "背叛"]
                dialog_kws = ["说", "道", "问", "答", "交谈", "聊", "谈话"]

                for kw in positive_kws:
                    if kw in text:
                        trust_delta += 3
                for kw in negative_kws:
                    if kw in text:
                        trust_delta -= 5
                for kw in dialog_kws:
                    if kw in text:
                        trust_delta += 1

                current_trust = imp.get("trust_level", 50)
                imp["trust_level"] = max(0, min(100, current_trust + trust_delta))

                interaction_record = {
                    "day": current_day,
                    "player_action": player_input[:100],
                    "summary": narrative[:200] if narrative else "",
                    "trust_delta": trust_delta,
                }

                memorable = imp.get("memorable_interactions", [])
                memorable.append(interaction_record)
                if len(memorable) > 5:
                    memorable[:] = memorable[-5:]

                imp["memorable_interactions"] = memorable

                if imp["interaction_count"] % 3 == 0 and self.llm:
                    npcs_to_update.append(npc)

        if npcs_to_update and self.llm:
            try:
                self._update_npc_impressions_with_llm(npcs_to_update, player_input, narrative, current_day)
            except Exception as e:
                logger.debug("LLM impression update skipped: %s", e)

    def _update_npc_impressions_with_llm(self, npcs, player_input: str, narrative: str, day: int):
        """使用LLM深度更新NPC对玩家的印象总结"""
        for npc in npcs[:2]:
            imp = npc.impression_of_player
            recent_interactions = "\n".join([
                f"- 第{m['day']}天：{m.get('summary', '')[:150]}"
                for m in imp.get("memorable_interactions", [])[-3:]
            ])

            prompt = f"""你是NPC「{npc.name}」，现在根据近期互动更新你对玩家的印象。

【你的身份】
名字：{npc.name}
性格：{npc.personality or '普通人'}
身份：{npc.role or '普通NPC'}
当前对玩家信任度：{imp.get('trust_level', 50)}/100
之前对玩家的印象：{imp.get('summary', '还不太了解这个人')}

【近期互动】
{recent_interactions or '第一次互动'}

【本次互动】
玩家行为：{player_input[:200]}
结果：{narrative[:300]}

【任务】
更新你对玩家的印象。只输出JSON，格式：
{{
  "summary": "一段50-100字的总体印象描述，从{npc.name}的视角出发",
  "known_traits": ["观察到的玩家特质1", "特质2", "特质3"],
  "trust_change": 0到10或-10到0的信任度变化
}}

只输出JSON。"""

            try:
                result = self.llm.chat_json(prompt, temperature=0.5, max_tokens=0)
                if result.get("summary"):
                    imp["summary"] = result["summary"]
                if result.get("known_traits"):
                    existing = set(imp.get("known_traits", []))
                    for t in result["known_traits"]:
                        if t and t not in existing:
                            existing.add(t)
                    imp["known_traits"] = list(existing)[:8]
                if result.get("trust_change"):
                    imp["trust_level"] = max(0, min(100, imp.get("trust_level", 50) + int(result["trust_change"])))
                logger.debug("Updated impression for NPC %s: trust=%d", npc.name, imp["trust_level"])
            except Exception as e:
                logger.debug("LLM impression update failed for %s: %s", npc.name, e)

    def _sync_npc_relations_to_player(self):
        """[v11] 将 NPC 的 relation_to_player 同步到 player_state.relations。
        确保侧边栏关系面板能正确显示好感度（默认50），而非0。"""
        if not self.player_state or not self.npc_states:
            return
        for npc_id, npc in self.npc_states.items():
            npc_name = npc.name
            if not npc_name:
                continue
            existing = self.player_state.relations.get(npc_name)
            if existing:
                # 已有记录：同步 NPC 端的好感度（NPC 端可能有更新）
                npc_favor = 50
                npc_rel_type = "陌生人"
                if hasattr(npc, 'relation_to_player'):
                    rtp = npc.relation_to_player
                    if isinstance(rtp, dict):
                        npc_favor = rtp.get("favor", 50)
                        npc_rel_type = rtp.get("relation_type", "陌生人")
                    elif hasattr(rtp, 'favor'):
                        npc_favor = rtp.favor
                        npc_rel_type = getattr(rtp, 'relation_type', '陌生人')
                # 如果 player 侧好感到0但NPC侧不是0，以NPC侧为准
                if existing.favor == 0 and npc_favor > 0:
                    existing.favor = npc_favor
                    existing.relation_type = npc_rel_type
                    logger.info("Synced relation %s: favor 0 → %d", npc_name, npc_favor)
            else:
                # 没有记录：从 NPC 侧初始化
                npc_favor = 50
                npc_rel_type = "陌生人"
                if hasattr(npc, 'relation_to_player'):
                    rtp = npc.relation_to_player
                    if isinstance(rtp, dict):
                        npc_favor = rtp.get("favor", 50)
                        npc_rel_type = rtp.get("relation_type", "陌生人")
                    elif hasattr(rtp, 'favor'):
                        npc_favor = rtp.favor
                        npc_rel_type = getattr(rtp, 'relation_type', '陌生人')
                from .schemas import RelationEntry
                self.player_state.relations[npc_name] = RelationEntry(
                    favor=npc_favor, relation_type=npc_rel_type
                )
                logger.info("Initialized relation %s: favor=%d, type=%s", npc_name, npc_favor, npc_rel_type)
        # [Bug] 将 npc_states 的关系同步到 npc_registry.world_npcs，
        # 否则 who-is-who 面板始终显示 world_def 里的初始关系（陌生人）
        if self.npc_registry:
            for npc_id, npc in self.npc_states.items():
                if npc_id in self.npc_registry.world_npcs:
                    rtp = npc.relation_to_player
                    if hasattr(rtp, 'favor'):
                        self.npc_registry.world_npcs[npc_id].relation_to_player = {
                            "favor": rtp.favor, "relation_type": getattr(rtp, 'relation_type', '陌生人')
                        }

    def _extract_relations_from_narrative(self, narrative: str, world_data: dict):
        if not self.llm or not self.npc_states or not self.player_state:
            return
        npc_list = ", ".join([f"{npc.name}({npc_id})" for npc_id, npc in self.npc_states.items()])
        existing_info = ""
        for nid, npc in self.npc_states.items():
            rel = self.player_state.relations.get(npc.name)
            if rel:
                existing_info += f"- {npc.name}: 好感{rel.favor}, 关系={rel.relation_type}\n"
        prompt = f"""根据以下叙事文本，分析NPC与主角的关系变化。

【叙事文本】
{narrative[:800]}

【NPC列表】
{npc_list}

【当前已知关系】
{existing_info or "无"}

【分析规则】
- 如果叙事中NPC的行为或态度发生重大变化（如从友善变敌对、从陌生变亲密），必须更新relation_type
- 如果只是日常互动没有实质变化，只更新favor微调，不改relation_type
- relation_type必须准确反映当前关系：爱人、侍女、下属、敌人、师徒、挚友、陌生人等
- 输出的npc_id必须是NPC的名字（与NPC列表中的名字一致），不能用编号

【输出JSON格式】
{{"relations": {{"NPC名字": {{"relation_type": "关系类型", "favor": 好感度0-100, "changed": true/false}}}}}}

只输出JSON。"""
        try:
            result = self.llm.chat_json(prompt, temperature=0.3)
            if "relations" in result:
                # [v10.5] 兼容 LLM 返回 list 格式
                rel_data_raw = result["relations"]
                if isinstance(rel_data_raw, list):
                    rel_data_raw = {r.get("npc_id", r.get("name", "")): r for r in rel_data_raw if isinstance(r, dict)}
                if not isinstance(rel_data_raw, dict):
                    rel_data_raw = {}
                for npc_id, rel_data in rel_data_raw.items():
                    matched_id = npc_id
                    if npc_id not in self.npc_states:
                        for nid in self.npc_states:
                            if npc_id in nid or nid in npc_id:
                                matched_id = nid
                                break
                    if matched_id in self.npc_states:
                        npc_name = self.npc_states[matched_id].name
                        rt = rel_data.get("relation_type", "陌生人")
                        fv = rel_data.get("favor", 50)
                        changed = rel_data.get("changed", False)
                        existing_rel = self.player_state.relations.get(npc_name)
                        is_stranger = existing_rel and existing_rel.relation_type == "陌生人"
                        if changed or is_stranger or not existing_rel:
                            self.player_state.relations[npc_name] = RelationEntry(
                                favor=fv, relation_type=rt
                            )
                            self.npc_states[matched_id].relation_to_player = RelationEntry(
                                favor=fv, relation_type=rt
                            )
                        elif existing_rel:
                            delta = fv - existing_rel.favor
                            if abs(delta) >= 10:
                                existing_rel.favor = max(0, min(100, fv))
        except Exception as e:
            logger.warning("Failed to extract relations from narrative: %s", e)

    def _maybe_trigger_world_event(self) -> dict | None:
        if not self.world_agent or not self.world_state or not self.player_state:
            return None

        if not hasattr(self, '_last_event_day'):
            self._last_event_day = 0
        if not hasattr(self, '_consecutive_passive'):
            self._consecutive_passive = 0

        days_since_event = self.world_state.current_day - self._last_event_day
        actions_today = len(self.action_log_today)

        if actions_today <= 1:
            self._consecutive_passive += 1
        else:
            self._consecutive_passive = 0

        should_trigger = False
        reason = ""

        if days_since_event >= 7:
            should_trigger = True
            reason = "距离上次事件已过7天"
        elif days_since_event >= 5 and self._consecutive_passive >= 5:
            should_trigger = True
            reason = "玩家太佛系了，需要事件推动"
        elif days_since_event >= 3 and self.world_state.crisis_level >= 6:
            should_trigger = True
            reason = "危机等级高，世界动荡"
        elif days_since_event >= 5 and actions_today >= 6:
            should_trigger = True
            reason = "玩家行动频繁，世界该有回应了"

        if not should_trigger:
            return None

        import random
        if days_since_event < 3 and random.random() > 0.3:
            return None

        event_result = self.world_agent.generate_event(
            self.world_state, self.world_state.current_day
        )
        event = event_result.get("event") if isinstance(event_result, dict) else None
        if event is None:
            logger.warning("world_agent.generate_event returned no event in _maybe_trigger_world_event")
            return None
        self.world_agent.update_world_state(self.world_state, event)

        narrative = self.world_agent.propagate_event(
            event, self.player_state, self.world_state.current_time
        )

        self._last_event_day = self.world_state.current_day

        self.event_log_today.append({
            "day": self.world_state.current_day,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "description": event.description,
            "impact_level": event.impact_level,
        })

        self.world_changes_today.append(f"世界事件: {event.description[:100]}")

        if self.world_state.economy and self.economy_system:
            self.economy_system.update_prices(
                self.world_state.economy, self.world_state, event.event_type
            )

        self.player_agent.update_memory(
            self.player_state, event.description[:400],
            self.world_state.current_day
        )

        return {
            "event": event.model_dump(),
            "narrative": narrative,
            "event_type": event.event_type,
            "impact_level": event.impact_level,
            "trigger_reason": reason,
        }

    def trigger_world_event(self) -> dict:
        if not self.world_agent or not self.world_state or not self.player_state:
            return {"narrative": "", "event": None}

        event_result = self.world_agent.generate_event(
            self.world_state, self.world_state.current_day
        )
        event = event_result.get("event") if isinstance(event_result, dict) else None
        if event is None:
            logger.warning("world_agent.generate_event returned no event in trigger_world_event")
            return {"narrative": "", "event": None}
        self.world_agent.update_world_state(self.world_state, event)

        narrative = self.world_agent.propagate_event(
            event, self.player_state, self.world_state.current_time
        )

        self.event_log_today.append({
            "day": self.world_state.current_day,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "description": event.description,
            "impact_level": event.impact_level,
        })

        self.world_changes_today.append(f"世界事件: {event.description[:100]}")

        self.player_agent.update_memory(
            self.player_state, event.description[:400],
            self.world_state.current_day
        )

        if self.world_state and self.world_state.economy and self.economy_system:
            price_changes = self.economy_system.update_prices(
                self.world_state.economy, self.world_state, event.event_type
            )
            if price_changes:
                self.world_changes_today.append(f"物价变化: {price_changes}")

        options = self.narrative.generate_dynamic_options(
            narrative, self.player_state
        ) if self.narrative else []

        return {
            "narrative": narrative,
            "event": event.model_dump(),
            "options": options,
        }

    def advance_time(self, time_slot: str = None) -> dict:
        """[v9] 推进时间 — 委托给 WorldManager"""
        if not self._world_manager:
            raise RuntimeError("WorldManager 未初始化，请先调用 init_llm()")
        return self._world_manager.advance_time(time_slot)

    def _on_new_day(self):
        """[v9] 新一天处理 — 委托给 WorldManager"""
        if not self._world_manager:
            raise RuntimeError("WorldManager 未初始化，请先调用 init_llm()")
        self._world_manager.on_new_day()

    def trigger_npc_reflection(self) -> dict:
        """[v10++] 触发 NPC 批量反思（Generative Agents 式）。
        在每日例程或时间推进时调用，由 NPCReflection 内部节流（每 N 天一次）。
        失败时不影响主流程。"""
        if not self.npc_reflection or not self.npc_states:
            return {}
        if not self.meta or not self.world_state:
            return {}
        try:
            return self.npc_reflection.batch_reflect(
                npc_states=self.npc_states,
                current_turn=self.meta.current_turn,
                current_day=self.world_state.current_day,
                max_npcs=10,
            )
        except Exception as e:
            logger.warning("NPC 反思触发失败: %s", e)
            return {}

    def trigger_autonomous_memory(self) -> dict:
        """[v10++] 触发 Agent 自主记忆管理（MemGPT/Letta 式）。
        评估当前上下文压力与记忆冗余，自主执行 store/retrieve/summarize/discard 等操作。
        失败时不影响主流程；通常由 TurnProcessorV2 在后台任务中调用。"""
        if not self.autonomous_memory or not self.memory:
            return {}
        if not self.meta or not self.world_state or not self.player_state:
            return {}
        try:
            # 估算当前上下文压力：近期叙事历史的 token 数
            context_size = self._estimate_context_pressure()
            entity_id = self.player_state.agent_id if hasattr(self.player_state, "agent_id") else self.player_state.name
            decisions = self.autonomous_memory.evaluate_and_act(
                entity_id=entity_id,
                context_size=context_size,
                current_turn=self.meta.current_turn,
                current_day=self.world_state.current_day,
            )
            if decisions:
                return {
                    "actions": [
                        {"action": d.action.value, "target": d.target, "reason": d.reason}
                        for d in decisions
                    ],
                    "stats": self.autonomous_memory.get_stats(),
                }
            return {}
        except Exception as e:
            logger.warning("自主记忆管理触发失败: %s", e)
            return {}

    def _estimate_context_pressure(self) -> int:
        """估算当前上下文压力（token 数）。
        基于近期叙事历史 + 玩家短期记忆的 token 估算。"""
        try:
            from .context_budget import estimate_tokens
        except Exception:
            # 回退：粗略估算（1 中文字 ≈ 1.5 token）
            estimate_tokens = lambda text: int(len(text or "") * 1.2)
        total = 0
        # 近期叙事历史（最多取最近 30 条，避免全量计算）
        for entry in self.narrative_history[-30:]:
            total += estimate_tokens(entry.get("text", ""))
        # 玩家短期记忆
        if self.player_state and self.player_state.memory:
            for m in self.player_state.memory.short_term[-10:]:
                total += estimate_tokens(m)
        return total

    def get_autonomous_memory_stats(self) -> dict:
        """获取自主记忆管理统计"""
        if not self.autonomous_memory:
            return {"error": "自主记忆管理器未初始化"}
        return self.autonomous_memory.get_stats()

    def generate_morning_intro(self) -> str:
        if not self.narrative or not self.player_state or not self.world_state:
            return ""
        yesterday = "; ".join(self.action_log_today[-3:]) if self.action_log_today else ""
        return self.narrative.generate_morning_intro(
            self.player_state, self.world_state, yesterday
        )

    def generate_return_narrative(self) -> str:
        """生成'物是人非'回归叙事：当玩家回到久违的地点时触发"""
        if not self.npc_life_evolution or not self.npc_states or not self.player_state:
            return ""

        current_year = self.world_state.current_day // 365 if self.world_state else 0
        years_away = current_year - self.npc_life_evolution.last_year_evolved
        if years_away < 1:
            return ""

        prompt = self.npc_life_evolution.generate_return_narrative_prompt(
            self.npc_states,
            self.player_state.location,
            years_away,
            self.world_state
        )
        if not prompt:
            return ""

        # 用LLM将演化日志润色为叙事
        return self.llm.chat(
            f"你是一个小说叙事助手。请根据以下信息，写一段150-300字的'物是人非'场景描写：\n\n{prompt}",
            temperature=0.7, max_tokens=1024
        )

    def get_npc_evolution_summary(self, npc_id: str) -> list[dict]:
        """获取某个NPC的完整演化历史"""
        if self.npc_life_evolution:
            return self.npc_life_evolution.get_evolution_summary(npc_id)
        return []

    def generate_novel_chapter(self) -> dict:
        if not self.narrative or not self.player_state or not self.world_state:
            return {"chapter": "", "checkpoint": 0, "entries_count": 0}

        recent = self.narrative_history[self.last_novel_checkpoint:]

        if not recent:
            return {"chapter": "", "checkpoint": self.last_novel_checkpoint,
                    "entries_count": 0, "message": "上次生成小说后没有新的互动记录"}

        full_log = "\n".join([
            f"[{h.get('type', '')}] 第{h.get('day', '?')}天 {h.get('time', '')}: "
            f"{'玩家: ' + h.get('player_input', '') + ' → ' if h.get('player_input') else ''}"
            f"{h.get('text', '')}"
            for h in recent
        ])

        age_info = f"当前年龄: {self.player_state.age}岁"
        economy_info = ""
        if self.world_state and self.world_state.economy and self.economy_system:
            economy_info = self.economy_system.get_market_report(self.world_state.economy)
        butterfly_info = self.butterfly.get_world_memory() if self.butterfly else ""

        chapter = self.narrative.generate_novel_chapter(
            self.player_state, self.world_state, full_log,
            age_info, economy_info, butterfly_info
        )

        self.last_novel_checkpoint = len(self.narrative_history)

        chapter_data = {
            "type": "novel_chapter",
            "chapter": chapter,
            "from_day": recent[0].get("day", 0) if recent else 0,
            "to_day": recent[-1].get("day", 0) if recent else 0,
            "entries_count": len(recent),
        }
        narrative_dir = self.save_manager.base_dir / self.current_world_id / "narrative"
        narrative_dir.mkdir(parents=True, exist_ok=True)
        # [v10.1] 使用时间戳生成唯一文件名，避免并发调用时章节号竞态导致文件覆盖
        chapter_stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        chapter_file = narrative_dir / f"chapter_{chapter_stamp}.json"
        self.save_manager._write_json(chapter_file, chapter_data)

        return {
            "chapter": chapter,
            "checkpoint": self.last_novel_checkpoint,
            "entries_count": len(recent),
            "from_day": recent[0].get("day", 0) if recent else 0,
            "to_day": recent[-1].get("day", 0) if recent else 0,
        }

    def generate_world_evolution(self) -> str:
        if not self.narrative or not self.world_state:
            return ""

        all_events = "\n".join([
            f"- {e.get('description', '')[:100]}" for e in self.event_log_today
        ]) or "无事件"

        player_impacts = "\n".join(self.player_impacts_today[-5:]) or "无"

        world_changes = "\n".join(self.world_changes_today[-5:]) or "无变化"

        return self.narrative.generate_world_evolution(
            all_events, player_impacts, world_changes
        )

    def get_game_state(self) -> dict:
        if not self.player_state or not self.world_state:
            return {}

        return {
            "world_id": self.current_world_id,
            "day": self.world_state.current_day,
            "time": self.world_state.current_time,
            "season": self.world_state.season,
            "weather": self.world_state.weather,
            "crisis_level": self.world_state.crisis_level,
            "turn": self.meta.current_turn if self.meta else 0,
            "player": {
                "name": self.player_state.name,
                "age": self.player_state.age,
                "max_age": self.player_state.max_age,
                "health": self.player_state.stats.health,
                "max_health": self.player_state.stats.max_health,
                "energy": self.player_state.stats.energy,
                "max_energy": self.player_state.stats.max_energy,
                "strength": self.player_state.stats.strength,
                "agility": self.player_state.stats.agility,
                "intelligence": self.player_state.stats.intelligence,
                "luck": self.player_state.stats.luck,
                "gold": self.player_state.social.gold,
                "reputation": self.player_state.social.reputation,
                "position": self.player_state.social.position,
                "location": self.player_state.location,
                "tags": self.player_state.tags,
                "status_effects": self.player_state.status_effects,
                "goal": self.player_state.current_goal,
                "relations": {
                    k: {
                        "favor": v.favor,
                        "type": v.relation_type,
                        "name": next((n.name for n in self.npc_states.values() if n.name == k), k),
                    }
                    for k, v in self.player_state.relations.items()
                },
                "memory_count": self.memory.get_memory_count() if self.memory else 0,
            },
            "faction_reputation": self.reputation_system.faction_reputation if self.reputation_system else {},
            "wanted_level": self.reputation_system.wanted_level if self.reputation_system else 0,
            "world": {
                "name": self.world_state.world_name,
                "era_name": self.world_state.era_name,
                "era_year": self.world_state.era_year,
                "current_year": self.world_state.current_year,
                "current_month": self.world_state.current_month,
                "current_day_of_month": self.world_state.current_day_of_month,
                "active_events": len(self.world_state.active_events),
                "factions": {
                    k: v.power for k, v in self.world_state.factions.items()
                },
                "power_system": self._build_power_system_display(),
            },
            "divergence_rate": round(min(100, self.butterfly.world_impact_score), 1) if self.butterfly else 0,
            "time_status": self._build_time_status(),
        }

    def _build_time_status(self) -> dict:
        """构建时间状态栏数据：故事总天数、年/月/日拆分、叙事偏移"""
        game_day = self.world_state.current_day if self.world_state else 1
        narrative_offset = self.timekeeper.narrative_day_offset if self.timekeeper else 0
        total_days = game_day  # game_day 已包含叙事时间推进

        years = total_days // 365
        remaining = total_days % 365
        months = remaining // 30
        days = remaining % 30

        # 生成人性化显示
        if years > 0:
            display = f"第{years}年"
            if months > 0:
                display += f" {months}个月"
            display += f"（总计{total_days}天）"
        elif months > 0:
            display = f"第{months}个月{days}天（总计{total_days}天）"
        else:
            display = f"第{total_days}天"

        return {
            "total_days": total_days,
            "years": years,
            "months": months,
            "days": days,
            "display": display,
            "game_day": game_day,
            "narrative_offset": narrative_offset,
            "season": self.world_state.season if self.world_state else "春季",
            "weather": self.world_state.weather if self.world_state else "晴朗",
            "time_of_day": self.world_state.current_time if self.world_state else "清晨",
        }

    def _build_power_system_display(self) -> dict | None:
        """从LevelSystem构建前端显示用的力量体系数据"""
        if not self.level_system or self.level_system.system_type == "none":
            return None
        levels = self.level_system.config.get("levels", [])
        current = self.level_system.get_current_level()
        display_levels = []
        for lv in levels:
            display_levels.append({
                "name": lv["name"],
                "description": f"需要{lv['min_exp']}经验",
            })
        return {
            "name": self.level_system.config.get("name", "等级"),
            "levels": display_levels,
            "player_level": current["name"],
            "level_description": f"当前境界：{current['name']}",
        }

    def _maybe_auto_image(self, narrative: str) -> dict | None:
        """[Disabled] 自动生成插图已关闭，改为仅手动生成"""
        return None

    def generate_scene_image(self, narrative: str) -> dict:
        if self.visual_engine and self.player_state:
            day = self.world_state.current_day if self.world_state else 0
            time_str = self.world_state.current_time if self.world_state else ""
            return self.visual_engine.generate_scene_image(
                narrative, self.player_state,
                self.player_state.location,
                self.world_state.weather if self.world_state else "晴朗",
                day=day, time_str=time_str
            )
        return {"generated": False}

    def handle_death_choice(self, choice: str) -> dict:
        book = self.hundred_life_book
        if self.player_state and self.world_state and self.death_system:
            cause = "未知"
            if self.death_system.death_history:
                cause = self.death_system.death_history[-1]["cause"]
            book.seal_current_life(self.player_state, self.world_state, cause)

        if choice == "reload":
            return {"action": "reload", "message": "请从存档列表选择一个存档加载"}

        elif choice == "reincarnate":
            if not self.player_state or not self.world_state:
                return {"error": "状态未初始化"}
            if book.pages_remaining <= 0:
                return {"action": "true_death",
                        "narrative": "百世书的最后一页已经黯淡无光。你感到一股无法抗拒的力量正在将你的灵魂从这个时间线中抹去。没有下一次了。这就是...终点。"}

            reinc_data = self.death_system.prepare_reincarnation(
                self.player_state, self.world_state
            )
            narrative = self.death_system.generate_reincarnation_narrative(reinc_data)

            inherited_tags = book.get_inherited_tags()
            inherited_knowledge = book.get_inherited_knowledge()

            self.player_state.age = 18
            self.player_state.stats.health = 100
            self.player_state.stats.energy = 100
            self.player_state.tags.extend(inherited_tags)
            self.player_state.memory.short_term = [
                f"前世知识: {k}" for k in inherited_knowledge[:5]
            ] + [f"前世记忆: {m}" for m in reinc_data.get("previous_life", {}).get("memories", [])]
            self.player_state.current_goal = "带着前世记忆，重新活一次"

            book.start_new_life(self.player_state.name, self.world_state.current_day)

            world_type = self.world_state.world_type if self.world_state else "custom"
            karma_narrative = book.get_karma_narrative(world_type)

            return {"action": "reincarnate", "narrative": narrative,
                    "reincarnation_data": reinc_data,
                    "inherited_tags": inherited_tags,
                    "pages_remaining": book.pages_remaining,
                    "karma_level": book.karma_level,
                    "karma_narrative": karma_narrative,
                    "revival_restriction": book.get_revival_restriction()}

        elif choice == "new_world":
            return {"action": "new_world", "message": "请在首页描述你想要的新世界"}
        return {"action": "none"}

    def hundred_book_rewind(self, slot_id: str) -> dict:
        """[v11] 百世书回滚：封印当前生命 → 加载目标存档 → 删除后续存档"""
        book = self.hundred_life_book
        if not book or not self.player_state or not self.world_state:
            return {"error": "百世书或游戏状态未初始化"}

        # 1. 封印当前生命
        cause = "未知"
        if self.death_system and self.death_system.death_history:
            cause = self.death_system.death_history[-1]["cause"]
        seal_result = book.seal_current_life(self.player_state, self.world_state, cause)

        # 2. 获取当前世界的时间线存档列表
        if not self.current_world_id:
            return {"error": "当前世界未初始化"}
        timeline = self.save_manager.get_timeline(self.current_world_id)
        all_slots = timeline.list_slots()

        # 3. 找到目标存档及其之后的所有存档
        target_idx = None
        for i, slot in enumerate(all_slots):
            if slot.get("slot_id") == slot_id:
                target_idx = i
                break
        if target_idx is None:
            return {"error": f"未找到存档: {slot_id}"}

        # 4. 删除目标存档之后的所有存档
        deleted_slots = []
        for slot in all_slots[target_idx + 1:]:
            sid = slot.get("slot_id")
            if sid:
                timeline.delete_slot(sid)
                deleted_slots.append(sid)

        # 5. 加载目标存档（恢复完整状态）
        ok = self.load_from_slot(slot_id)
        if not ok:
            return {"error": f"加载存档失败: {slot_id}"}

        # 6. 获取更新后的状态
        state = self.get_game_state()
        # narrative_history 不在 get_game_state 里，单独附加
        state["narrative_history"] = self.narrative_history

        return {
            "action": "rewind",
            "narrative": f"百世书翻动书页，封印了第{seal_result['life_number']}世的记忆。\n"
                         f"时间线回溯至选定的存档点。剩余书页: {seal_result['pages_remaining']}",
            "pages_remaining": seal_result["pages_remaining"],
            "karma_level": seal_result["karma_level"],
            "deleted_slots": deleted_slots,
            "state": state,
        }

    def _save_game_state(self):
        if not self.current_world_id:
            return
        world_dir = self.save_manager.base_dir / self.current_world_id
        state_dir = world_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        game_state = {
            "age_system": self.age_system.to_dict() if self.age_system else {},
            "hundred_life_book": {
                "current_life": self.hundred_life_book.current_life if self.hundred_life_book else 0,
                "pages_remaining": self.hundred_life_book.pages_remaining if self.hundred_life_book else 100,
                "karma_level": self.hundred_life_book.karma_level if self.hundred_life_book else 0,
                "observed_by": self.hundred_life_book.observed_by if self.hundred_life_book else [],
            },
            "level_system": self.level_system.to_dict() if self.level_system else {"system_type": self.current_level_system_type, "experience": 0},
            "destiny_regret": {
                "missed": self.destiny_regret.missed_opportunities if self.destiny_regret else [],
            },
            "memoir": self.memoir.to_dict() if self.memoir else {},
            "butterfly": self.butterfly.to_dict() if self.butterfly else {},
            "death_system": self.death_system.to_dict() if self.death_system else {},
            "brain_whispers": self.brain_whispers.to_dict() if self.brain_whispers else {},
            "faction_wars": self.faction_wars.to_dict() if self.faction_wars else {},
            "visual_engine": {
                "image_history": self.visual_engine.image_history if self.visual_engine else [],
            },
            # [v9] narrative_history 改为增量持久化，此处仅保存条数用于校验
            "narrative_history_count": len(self.narrative_history),
            "last_novel_checkpoint": self.last_novel_checkpoint,
            "reputation": self.reputation_system.to_dict() if self.reputation_system else {},
            "skill_tree": {
                "unlocked": self.skill_tree.unlocked_skills if self.skill_tree else [],
                "points": self.skill_tree.skill_points if self.skill_tree else 0,
            },
            "quest_system": self.quest_system.to_dict() if self.quest_system else {},
            "influence_network": self.influence_network.to_dict() if self.influence_network else {},
            "npc_perception_zones": self.npc_perception.to_dict() if self.npc_perception else {},
            "npc_life_evolution": self.npc_life_evolution.to_dict() if self.npc_life_evolution else {},
            "_last_year_evolved": self._last_year_evolved,
            "timekeeper": self.timekeeper.to_dict() if self.timekeeper else {},
            # [v10] 新增模块状态
            "narrative_reviewer": self.narrative_reviewer.to_dict() if self.narrative_reviewer else {},
            "npc_procedural_memory": self.npc_procedural_memory.to_dict() if self.npc_procedural_memory else {},
            "world_task_board": self.world_task_board.to_dict() if self.world_task_board else {},
            "memory_curator": self.memory_curator.to_dict() if self.memory_curator else {},
            # [v10++] NPC 技能自学库（Voyager/Hermes 式）
            "npc_skill_library": self.npc_skill_library.to_dict() if self.npc_skill_library else {},
            # [v10+] 新增模块状态
            "foreshadow_lifecycle": self.foreshadow_lifecycle.to_dict() if self.foreshadow_lifecycle else {},
            "continuity_auditor": self.continuity_auditor.to_dict() if self.continuity_auditor else {},
            "npc_registry": self.npc_registry.to_dict() if self.npc_registry else {},
            # [v10++] 角色动态状态管理器（CHIRON 式）
            "character_state_manager": self.character_state_manager.to_dict() if self.character_state_manager else {},
            # [v10++] NPC 反思机制（Generative Agents 式）
            "npc_reflection": self.npc_reflection.to_dict() if self.npc_reflection else {},
            # [v10++] Agent 自主记忆管理（MemGPT/Letta 式）
            "autonomous_memory": self.autonomous_memory.to_dict() if self.autonomous_memory else {},
            # [v10.1] 世界事件触发状态持久化，避免加载后立即触发事件
            "_last_event_day": getattr(self, "_last_event_day", 0),
            "_consecutive_passive": getattr(self, "_consecutive_passive", 0),
            # [v10.1] 持久化 Curator 历史摘要，避免重启后丢失
            "_history_summaries": self.memory_curator._history_summaries if self.memory_curator else [],
            "_summary_counter": self.memory_curator._summary_counter if self.memory_curator else 0,
            "_summarized_up_to": self.memory_curator._summarized_up_to if self.memory_curator else 0,
        }

        atomic_write_json(
            state_dir / "game_state.json",
            game_state,
            indent=2,
            ensure_ascii=False
        )

        # [v9] 叙事历史持久化 — 增量追加或全量重写
        # 若 narrative_history 被 Curator 压缩过，或计数器超出当前长度，则全量重写 JSONL；否则增量追加
        narrative_file = state_dir / "narrative_history.jsonl"
        try:
            if getattr(self, "_narrative_compressed", False) or self._persisted_narrative_count > len(self.narrative_history):
                # 全量重写：内存已被压缩或计数器超出当前长度
                with open(narrative_file, "w", encoding="utf-8") as f:
                    for entry in self.narrative_history:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._persisted_narrative_count = len(self.narrative_history)
                self._narrative_compressed = False
            else:
                # 增量追加：仅写入尚未持久化的条目
                new_entries = self.narrative_history[self._persisted_narrative_count:]
                if new_entries:
                    with open(narrative_file, "a", encoding="utf-8") as f:
                        for entry in new_entries:
                            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    self._persisted_narrative_count = len(self.narrative_history)
        except Exception as e:
            logger.warning("Narrative history save failed: %s", e)

        # [Bug#35] None 检查
        if self.hundred_life_book:
            self.hundred_life_book._save_book()

    def _load_game_state(self, world_id: str):
        world_dir = self.save_manager.base_dir / world_id
        state_file = world_dir / "state" / "game_state.json"

        if not state_file.exists():
            return

        try:
            gs = load_json_safe(state_file, default=None)
            if gs is None:
                logger.error("Failed to load game_state.json (no valid backup)")
                return
        except Exception as e:
            logger.error("Failed to parse game_state.json: %s", e)
            return

        age_data = gs.get("age_system", {})
        if self.age_system:
            self.age_system.from_dict(age_data)

        book_data = gs.get("hundred_life_book", {})
        # [Bug#35] None 检查
        if self.hundred_life_book:
            self.hundred_life_book.load_book()

        level_data = gs.get("level_system", {})
        self.current_level_system_type = level_data.get("system_type", "none")
        self.level_system = LevelSystem(self.current_level_system_type)
        self.level_system.from_dict(level_data)

        regret_data = gs.get("destiny_regret", {})
        if self.destiny_regret:
            self.destiny_regret.missed_opportunities = regret_data.get("missed", [])

        memoir_data = gs.get("memoir", {})
        if self.memoir:
            self.memoir.from_dict(memoir_data)

        butterfly_data = gs.get("butterfly", {})
        if self.butterfly:
            self.butterfly.from_dict(butterfly_data)
            # 向后兼容：旧存档将审批门字段单独存放在 butterfly_approval_gate 下
            ba_data = gs.get("butterfly_approval_gate")
            if ba_data:
                self.butterfly.approval_gate_enabled = ba_data.get("enabled", self.butterfly.approval_gate_enabled)
                self.butterfly.approval_threshold = ba_data.get("threshold", self.butterfly.approval_threshold)
                self.butterfly.pending_approvals = ba_data.get("pending", self.butterfly.pending_approvals)
                self.butterfly.approval_history = ba_data.get("history", self.butterfly.approval_history)

        death_data = gs.get("death_system", {})
        if self.death_system:
            self.death_system.from_dict(death_data)

        whispers_data = gs.get("brain_whispers", {})
        if self.brain_whispers:
            self.brain_whispers.from_dict(whispers_data)

        wars_data = gs.get("faction_wars", {})
        if self.faction_wars:
            self.faction_wars.from_dict(wars_data)

        visual_data = gs.get("visual_engine", {})
        if self.visual_engine:
            self.visual_engine.image_history = visual_data.get("image_history", [])

        # [v9] 叙事历史加载 — 优先从 JSONL 文件读取，向后兼容旧格式
        narrative_file = world_dir / "state" / "narrative_history.jsonl"
        if narrative_file.exists():
            self.narrative_history = []
            try:
                with open(narrative_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            self.narrative_history.append(json.loads(line))
            except Exception as e:
                logger.warning("Failed to load narrative_history.jsonl: %s", e)
                self.narrative_history = gs.get("narrative_history", [])
        else:
            # 向后兼容：旧存档中 narrative_history 在 game_state.json 里
            self.narrative_history = gs.get("narrative_history", [])
        self.last_novel_checkpoint = gs.get("last_novel_checkpoint", 0)
        # [v10.1] 同步已持久化计数器，避免下次保存时重复追加
        self._persisted_narrative_count = len(self.narrative_history)
        self._narrative_compressed = False
        # [v10.1] 恢复 Curator 历史摘要
        if self.memory_curator:
            self.memory_curator._history_summaries = gs.get("_history_summaries", [])
            self.memory_curator._summary_counter = gs.get("_summary_counter", 0)
            self.memory_curator._summarized_up_to = gs.get("_summarized_up_to", 0)
        logger.info("Loaded game_state: narrative_history=%d entries, summaries=%d",
                     len(self.narrative_history),
                     len(gs.get("_history_summaries", [])))

        rep_data = gs.get("reputation", {})
        if self.reputation_system:
            self.reputation_system.from_dict(rep_data)

        skill_data = gs.get("skill_tree", {})
        if self.skill_tree:
            self.skill_tree.unlocked_skills = skill_data.get("unlocked", [])
            self.skill_tree.skill_points = skill_data.get("points", 0)

        quest_data = gs.get("quest_system", {})
        if self.quest_system:
            self.quest_system.from_dict(quest_data)

        inf_data = gs.get("influence_network", {})
        if self.influence_network and inf_data:
            self.influence_network.from_dict(inf_data)

        zone_data = gs.get("npc_perception_zones", {})
        if self.npc_perception and zone_data:
            self.npc_perception.from_dict(zone_data)

        evo_data = gs.get("npc_life_evolution", {})
        if self.npc_life_evolution and evo_data:
            self.npc_life_evolution.from_dict(evo_data)

        self._last_year_evolved = gs.get("_last_year_evolved", 0)

        # [v10.1] 恢复世界事件触发状态，避免加载后立即触发事件
        self._last_event_day = gs.get("_last_event_day", 0)
        self._consecutive_passive = gs.get("_consecutive_passive", 0)

        tk_data = gs.get("timekeeper", {})
        if self.timekeeper and tk_data:
            self.timekeeper.from_dict(tk_data)
        elif not self.timekeeper:
            self.timekeeper = NarrativeTimekeeper()
            if tk_data:
                self.timekeeper.from_dict(tk_data)

        # [v10] 加载新模块状态
        reviewer_data = gs.get("narrative_reviewer", {})
        if self.narrative_reviewer and reviewer_data:
            self.narrative_reviewer.from_dict(reviewer_data)

        proc_data = gs.get("npc_procedural_memory", {})
        if self.npc_procedural_memory and proc_data:
            self.npc_procedural_memory.from_dict(proc_data)

        task_data = gs.get("world_task_board", {})
        if self.world_task_board and task_data:
            self.world_task_board.from_dict(task_data)

        curator_data = gs.get("memory_curator", {})
        if self.memory_curator and curator_data:
            self.memory_curator.from_dict(curator_data)

        # [v10++] 加载 NPC 技能自学库状态（Voyager/Hermes 式）
        skill_lib_data = gs.get("npc_skill_library", {})
        if self.npc_skill_library and skill_lib_data:
            try:
                self.npc_skill_library.from_dict(skill_lib_data)
                stats = self.npc_skill_library.get_stats()
                logger.info("Loaded NPCSkillLibrary: %d NPCs with skills, %d total skills",
                           stats["npcs_with_skills"], stats["total_skills"])
            except Exception as e:
                logger.warning("Failed to load npc_skill_library: %s", e)

        # [v10+] 加载新模块状态
        fs_data = gs.get("foreshadow_lifecycle", {})
        if self.foreshadow_lifecycle and fs_data:
            self.foreshadow_lifecycle.from_dict(fs_data)

        ca_data = gs.get("continuity_auditor", {})
        if self.continuity_auditor and ca_data:
            self.continuity_auditor.from_dict(ca_data)

        npc_reg_data = gs.get("npc_registry", {})
        if npc_reg_data:
            try:
                self.npc_registry = NpcRegistry.from_dict(npc_reg_data)
                logger.info("Loaded NpcRegistry: %d world NPCs, %d local, %d passersby",
                           len(self.npc_registry.world_npcs),
                           len(self.npc_registry.local_npcs),
                           len(self.npc_registry.passerby_npcs))
            except Exception as e:
                logger.warning("Failed to load npc_registry, creating new: %s", e)
                self.npc_registry = NpcRegistry(max_passersby=10)
        else:
            self.npc_registry = NpcRegistry(max_passersby=10)

        # [v10++] 加载角色动态状态管理器（CHIRON 式）
        csm_data = gs.get("character_state_manager", {})
        if self.character_state_manager and csm_data:
            try:
                self.character_state_manager.from_dict(csm_data)
                stats = self.character_state_manager.get_stats()
                logger.info("Loaded CharacterStateManager: %d tracked characters, %d total changes",
                           stats["tracked_characters"], stats["total_changes"])
            except Exception as e:
                logger.warning("Failed to load character_state_manager: %s", e)

        # [v10++] 加载 NPC 反思机制状态（Generative Agents 式）
        reflection_data = gs.get("npc_reflection", {})
        if self.npc_reflection and reflection_data:
            try:
                self.npc_reflection.from_dict(reflection_data)
                stats = self.npc_reflection.get_stats()
                logger.info("Loaded NPCReflection: %d NPCs with insights, %d total insights",
                           stats["npcs_with_insights"], stats["total_insights"])
            except Exception as e:
                logger.warning("Failed to load npc_reflection: %s", e)

        # [v10++] 加载自主记忆管理状态（MemGPT/Letta 式）
        amm_data = gs.get("autonomous_memory", {})
        if self.autonomous_memory and amm_data:
            try:
                self.autonomous_memory.from_dict(amm_data)
                stats = self.autonomous_memory.get_stats()
                logger.info("Loaded AutonomousMemoryManager: %d total actions",
                           stats["total_actions"])
            except Exception as e:
                logger.warning("Failed to load autonomous_memory: %s", e)

    def get_map_data(self) -> dict:
        """返回世界地图数据：地点、NPC位置、玩家位置、连线"""
        locations = {}
        if self.world_def and "locations" in self.world_def:
            locations = self.world_def["locations"]
        elif self.world_state and self.world_state.locations:
            locations = self.world_state.locations

        # 地点节点
        nodes = []
        for code, info in locations.items():
            name = info.get("location_name", code) if isinstance(info, dict) else str(info)
            desc = (info.get("description", "") if isinstance(info, dict) else "")[:60]
            nodes.append({"id": code, "name": name, "description": desc})

        # 玩家位置
        player_loc = self.player_state.location if self.player_state else ""

        # NPC 在各位置的分布
        npc_at_locations = {}
        if self.npc_states:
            for nid, npc in self.npc_states.items():
                loc = npc.current_location or "未知"
                npc_at_locations.setdefault(loc, []).append({
                    "id": nid, "name": npc.name, "role": npc.role
                })

        # 地图距离连线（如果有 map.csv）
        edges = []
        if self.world_def and "map" in self.world_def:
            map_data = self.world_def["map"]
            if isinstance(map_data, dict):
                loc_names = list(map_data.keys())
                for i, src in enumerate(loc_names):
                    for j, dst in enumerate(loc_names):
                        if i < j:
                            dist = map_data[src].get(dst, 0) if isinstance(map_data[src], dict) else 0
                            if dist:
                                edges.append({"source": src, "target": dst, "distance": dist})

        return {
            "locations": nodes,
            "player_location": player_loc,
            "npc_locations": npc_at_locations,
            "edges": edges,
        }

    # ── v7 新增方法 ────────────────────────────────────────

    def process_group_input(self, player_input: str,
                             npc_ids: list[str] = None) -> dict:
        """
        处理群聊/多NPC对话输入。
        
        Args:
            player_input: 玩家输入
            npc_ids: 参与群聊的NPC ID列表，None时自动选择同地点NPC
        """
        if not self.group_chat or not self.player_state:
            return {"error": "群聊系统未初始化"}

        # 选择参与者
        if npc_ids:
            npcs = [self.npc_states[nid] for nid in npc_ids
                    if nid in self.npc_states]
        else:
            # 自动选择同地点NPC
            npcs = [npc for npc in self.npc_states.values()
                    if npc.current_location == self.player_state.location]

        if len(npcs) < 2:
            return {"error": "附近NPC不足，无法开启群聊"}

        # 开始群聊场景
        scene = self.group_chat.start_group_scene(
            npcs, self.player_state, self.world_state, player_input)

        # 决定发言顺序
        reply_order = self.group_chat.decide_reply_order(
            npcs, player_input, strategy="natural")

        # 生成NPC回复
        replies = []
        for npc in reply_order[:3]:  # 最多3个NPC回复
            reply = self.group_chat.generate_npc_reply(
                npc, self.player_state, self.world_state,
                player_input, self.player_state.name,
                other_npcs=[n for n in npcs if n != npc])
            replies.append(reply)

        # 生成群聊叙事
        narrative = self.group_chat.generate_group_narrative(
            self.player_state, self.world_state)

        return {
            "scene_narrative": scene.get("scene_narrative", ""),
            "replies": replies,
            "narrative": narrative,
            "participants": scene.get("participants", []),
        }

    def import_novel(self, text: str, world_type: str = "auto") -> dict:
        """
        从小说文本导入世界。
        
        Args:
            text: 小说文本
            world_type: 世界类型，"auto"时自动推断
        """
        if not self.novel_importer:
            return {"error": "小说导入器未初始化"}
        try:
            world_data = self.novel_importer.import_from_text(text, world_type)
            return {"success": True, "world_data": world_data}
        except Exception as e:
            logger.error("小说导入失败: %s", e)
            return {"error": str(e)}

    def query_graph_rag(self, question: str) -> dict:
        """查询知识图谱"""
        if not self.graph_rag:
            return {"results": [], "context": ""}
        results = self.graph_rag.query(question)
        context = self.graph_rag.get_context_for_prompt(question)
        return {"results": results, "context": context}

    def get_graph_visualization(self) -> dict:
        """获取知识图谱可视化数据"""
        if not self.graph_rag:
            return {"nodes": [], "edges": []}
        return self.graph_rag.to_visualization_data()

    # ── [v10] 新增 API 方法 ────────────────────────────────

    def get_narrative_review(self) -> dict:
        """获取叙事回顾结果和质量趋势"""
        if not self.narrative_reviewer:
            return {"error": "叙事回顾器未初始化"}
        return {
            "quality_trend": self.narrative_reviewer.get_quality_trend(),
            "lessons_count": len(self.narrative_reviewer.lessons),
            "active_lessons": [
                l.to_dict() for l in sorted(
                    self.narrative_reviewer.lessons,
                    key=lambda x: x.importance, reverse=True
                )[:10]
            ],
        }

    def get_task_board(self) -> dict:
        """获取世界任务板状态"""
        if not self.world_task_board:
            return {"error": "任务板未初始化"}
        return self.world_task_board.get_board_summary()

    def get_butterfly_approvals(self) -> list[dict]:
        """获取待审批的蝴蝶效应"""
        if not self.butterfly:
            return []
        return self.butterfly.get_pending_approvals()

    def approve_butterfly_effect(self, approval_id: str,
                                  decision: str = "approve") -> dict:
        """审批蝴蝶效应后果"""
        if not self.butterfly:
            return {"error": "蝴蝶效应系统未初始化"}
        result = self.butterfly.approve_consequence(approval_id, decision)
        if result.get("approved") and result.get("impact"):
            # 执行已批准的后果
            consequence = self.butterfly.generate_consequence(
                result["impact"], self.world_state
            )
            if consequence:
                if self.world_agent:
                    self.world_agent.update_world_state(self.world_state, consequence)
                result["consequence"] = consequence.model_dump()
                # [v10.5] 使用实例级 trigger_hook 而非全局
                self.trigger_hook("on_butterfly_approval",
                             approval_id=approval_id, consequence=consequence)
        return result

    def get_curator_stats(self) -> dict:
        """获取记忆 Curator 统计"""
        if not self.memory_curator:
            return {"error": "Curator 未初始化"}
        return self.memory_curator.get_curate_stats()

    def get_npc_procedural_stats(self) -> dict:
        """获取 NPC 程序性记忆统计"""
        if not self.npc_procedural_memory:
            return {"error": "NPC程序性记忆未初始化"}
        return self.npc_procedural_memory.get_stats()

    def get_npc_skill_library_stats(self) -> dict:
        """[v10++] 获取 NPC 技能自学库统计（Voyager/Hermes 式）"""
        if not self.npc_skill_library:
            return {"error": "NPC技能自学库未初始化"}
        return self.npc_skill_library.get_stats()

    def get_multi_agent_narrative_stats(self) -> dict:
        """[v10+++] 获取多智能体分工叙事统计（Agents' Room 式）"""
        if not self.multi_agent_narrative:
            return {"error": "多智能体叙事引擎未初始化"}
        return self.multi_agent_narrative.get_stats()

    def get_v10_dashboard(self) -> dict:
        """[v10] 获取所有 v10 新系统的概览面板"""
        return {
            "narrative_review": self.get_narrative_review(),
            "task_board": self.get_task_board(),
            "curator": self.get_curator_stats(),
            "procedural_memory": self.get_npc_procedural_stats(),
            "butterfly_pending": len(self.get_butterfly_approvals()),
            "memory_quality": {
                "working_memory": self.memory.get_working_memory_context(3) if self.memory else "",
                "identity_count": self.memory.get_identity_count() if self.memory else 0,
            },
            # [v10+] 新增
            "foreshadow": self.get_foreshadow_health(),
            "continuity_audit": self.get_continuity_audit(),
            # [v10++] NPC 技能自学库（Voyager/Hermes 式）
            "skill_library": self.get_npc_skill_library_stats(),
            # [v10+++] 多智能体分工叙事（Agents' Room 式）
            "multi_agent_narrative": self.get_multi_agent_narrative_stats(),
        }

    # ── [v10+] 新增 API 方法 ──────────────────────────────

    def get_foreshadow_health(self) -> dict:
        """获取伏笔健康报告"""
        if not self.foreshadow_lifecycle:
            return {"error": "伏笔生命周期管理器未初始化"}
        current_day = self.world_state.current_day if self.world_state else 0
        report = self.foreshadow_lifecycle.get_health_report(current_day)
        report["active_hooks"] = self.foreshadow_lifecycle.get_active_hooks()
        report["reminder_mode"] = self.foreshadow_lifecycle.reminder_mode
        # 静默模式下 hooks_for_prompt 为空
        report["hooks_for_prompt"] = self.foreshadow_lifecycle.get_hooks_for_prompt(5)
        return report

    def get_continuity_audit(self) -> dict:
        """获取连续性审计结果"""
        if not self.continuity_auditor:
            return {"error": "连续性审计器未初始化"}
        return {
            "latest_report": self.continuity_auditor.get_latest_report(),
            "trend": self.continuity_auditor.get_audit_trend(),
        }

    def close(self):
        # [v10.1] 仅调用 save_game("auto")（内部已调用 _save_game_state），避免重复写入
        try:
            self.save_game("auto")
        except Exception as e:
            logger.warning("save_game(auto) failed during close: %s", e)
        # [v10.1] 关闭后台任务队列
        try:
            if self.task_queue and hasattr(self.task_queue, "stop"):
                self.task_queue.stop()
        except Exception as e:
            logger.warning("task_queue stop failed during close: %s", e)
        # [Bug] 关闭 LLM httpx 连接池，防止连接泄漏
        try:
            if self.llm:
                self.llm.close()
        except Exception as e:
            logger.warning("LLM close failed during close: %s", e)
        self.save_manager.close_all()
