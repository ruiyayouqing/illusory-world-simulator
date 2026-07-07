"""
服务注册表 — 集中管理所有子系统的创建和引用。

所有子系统通过此模块按需创建，game_engine 只依赖此模块，
不再直接导入 30+ 个子系统模块，从根本上消除循环导入风险。

v7 新增：插件系统、分支思维规划器、群聊、小说导入、GraphRAG、角色卡。
v8 新增：叙事风格管理器、EventBus、TurnProcessor、WorldManager。
v10 新增：闭环学习、NPC程序性记忆、世界任务板、记忆Curator、蝴蝶效应审批门、自注册模式。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING
import logging
import importlib
from pathlib import Path

logger = logging.getLogger("chronoverse.registry")

if TYPE_CHECKING:
    from .llm.mimo_llm import MimoLLM
    from .player_agent import PlayerAgent
    from .world_agent import WorldAgent
    from .narrative_engine import NarrativeEngine
    from .save_manager import SaveManager
    from .db.chroma_db import MemoryStore
    from .lorebook import Lorebook
    from .age_system import AgeSystem
    from .npc_agent import NPCAgent
    from .economy import EconomySystem
    from .butterfly_effect import ButterflyEffect
    from .option_engine import OptionEngine
    from .streamer import NarrativeStreamer
    from .world_generator import WorldGenerator
    from .level_system import LevelSystem, GodsCodex
    from .brain_whispers import BrainWhispers
    from .destiny_regret import DestinyRegret
    from .player_memoir import PlayerMemoir
    from .faction_wars import FactionWars
    from .faction_war import NpcFavorEvents
    from .death_system import DeathSystem
    from .hundred_life_book import HundredLifeBook
    from .item_skill import ItemSystem, SkillTree
    from .quest_system import QuestSystem
    from .world_template import WorldTemplate
    from .weather_effects import WeatherEffects
    from .reputation import ReputationSystem
    from .llm_cache import LLMCache
    from .npc_autonomous import NpcAutonomous
    from .visual_engine import VisualEngine
    from .npc_perception import NPCPerceptionSystem
    from .influence_network import InfluenceNetwork
    from .rag_historical import RAGHistoricalStore
    from .npc_life_evolution import NpcLifeEvolution
    from .branch_planner import BranchPlanner
    from .group_chat import GroupChatManager
    from .novel_importer import NovelImporter
    from .graph_rag import GraphRAG
    from .character_card import CharacterCard
    # [v10] 新增服务
    from .narrative_reviewer import NarrativeReviewer
    from .npc_procedural_memory import NPCProceduralMemory
    from .world_task_board import WorldTaskBoard
    from .memory_curator import MemoryCurator
    # [v10++] NPC 技能自学库（Voyager/Hermes 式）
    from .npc_skill_library import NPCSkillLibrary
    # [v10+] 新增服务
    from .foreshadow_lifecycle import ForeshadowLifecycle
    from .continuity_auditor import ContinuityAuditor
    from .retrieval.bm25_retriever import BM25Retriever
    from .retrieval.hybrid_retriever import HybridRetriever
    # [v10+] 叙事场景检测器（GraphRAG 动态启停）
    from .narrative_scene_detector import SceneDetector
    # [v10++] 上下文工程
    from .context_engine import ContextEngine
    # [v10++] 角色动态状态管理器（CHIRON 式）
    from .character_state import CharacterStateManager
    # [v10++] NPC 反思机制（Generative Agents 式）
    from .npc_reflection import NPCReflection
    # [v10+] SillyTavern 世界书导入器
    from .world_info_importer import WorldInfoImporter
    # [v10++] Agent 自主记忆管理（MemGPT/Letta 式）
    from .autonomous_memory import AutonomousMemoryManager
    # [v10+++] 多智能体分工叙事（Agents' Room 式）
    from .multi_agent_narrative import MultiAgentNarrativeEngine
    # [v10++] MCP 工具协议层（Model Context Protocol 兼容）
    from .mcp_tools import MCPToolRegistry


@dataclass
class ServiceRegistry:
    """持有所有子系统实例的注册表"""
    llm: Optional["MimoLLM"] = None
    lorebook: Optional["Lorebook"] = None
    narrative: Optional["NarrativeEngine"] = None
    age_system: Optional["AgeSystem"] = None
    npc_agent: Optional["NPCAgent"] = None
    economy_system: Optional["EconomySystem"] = None
    butterfly: Optional["ButterflyEffect"] = None
    option_engine: Optional["OptionEngine"] = None
    streamer: Optional["NarrativeStreamer"] = None
    world_generator: Optional["WorldGenerator"] = None
    brain_whispers: Optional["BrainWhispers"] = None
    memoir: Optional["PlayerMemoir"] = None
    favor_events: Optional["NpcFavorEvents"] = None
    faction_wars: Optional["FactionWars"] = None
    visual_engine: Optional["VisualEngine"] = None
    gods_codex: Optional["GodsCodex"] = None
    destiny_regret: Optional["DestinyRegret"] = None
    death_system: Optional["DeathSystem"] = None
    hundred_life_book: Optional["HundredLifeBook"] = None
    item_system: Optional["ItemSystem"] = None
    skill_tree: Optional["SkillTree"] = None
    quest_system: Optional["QuestSystem"] = None
    world_template: Optional["WorldTemplate"] = None
    weather_effects: Optional["WeatherEffects"] = None
    reputation_system: Optional["ReputationSystem"] = None
    llm_cache: Optional["LLMCache"] = None
    npc_perception: Optional["NPCPerceptionSystem"] = None
    influence_network: Optional["InfluenceNetwork"] = None
    rag_historical: Optional["RAGHistoricalStore"] = None
    npc_life_evolution: Optional["NpcLifeEvolution"] = None
    npc_autonomous: Optional["NpcAutonomous"] = None
    player_agent: Optional["PlayerAgent"] = None
    world_agent: Optional["WorldAgent"] = None
    # v7 新增服务
    branch_planner: Optional["BranchPlanner"] = None
    group_chat: Optional["GroupChatManager"] = None
    novel_importer: Optional["NovelImporter"] = None
    graph_rag: Optional["GraphRAG"] = None
    character_card: Optional["CharacterCard"] = None
    # [v10] 新增服务
    narrative_reviewer: Optional["NarrativeReviewer"] = None
    npc_procedural_memory: Optional["NPCProceduralMemory"] = None
    world_task_board: Optional["WorldTaskBoard"] = None
    memory_curator: Optional["MemoryCurator"] = None
    # [v10++] NPC 技能自学库（Voyager/Hermes 式）
    npc_skill_library: Optional["NPCSkillLibrary"] = None
    # [v10+] 新增服务
    foreshadow_lifecycle: Optional["ForeshadowLifecycle"] = None
    continuity_auditor: Optional["ContinuityAuditor"] = None
    # [v10++] 角色动态状态管理器（CHIRON 式）
    character_state_manager: Optional["CharacterStateManager"] = None
    # [v10++] NPC 反思机制（Generative Agents 式）
    npc_reflection: Optional["NPCReflection"] = None
    # [v10++] 上下文工程（注意力预算 + 提示压缩 + Prompt Caching）
    context_engine: Optional["ContextEngine"] = None
    # [v10+] 混合检索（BM25 + 向量 + GraphRAG）
    bm25_retriever: Optional["BM25Retriever"] = None
    hybrid_retriever: Optional["HybridRetriever"] = None
    # [v10+] 叙事场景检测器（GraphRAG 动态启停）
    scene_detector: Optional["SceneDetector"] = None
    # [v10+] SillyTavern 世界书导入器
    world_info_importer: Optional["WorldInfoImporter"] = None
    # [v10++] Agent 自主记忆管理（MemGPT/Letta 式）
    autonomous_memory: Optional["AutonomousMemoryManager"] = None
    # [v10+++] 多智能体分工叙事（Agents' Room 式）
    multi_agent_narrative: Optional["MultiAgentNarrativeEngine"] = None
    # [v10++] MCP 工具协议层（Model Context Protocol 兼容）
    mcp_registry: Optional["MCPToolRegistry"] = None


def create_services(llm: "MimoLLM", save_manager: "SaveManager",
                    current_world_id: str = "") -> ServiceRegistry:
    """工厂函数：创建所有子系统实例。所有跨模块导入集中在此处。"""
    # 延迟导入 — 只在函数调用时加载，避免模块级循环依赖
    from .lorebook import Lorebook
    from .narrative_engine import NarrativeEngine
    from .age_system import AgeSystem
    from .npc_agent import NPCAgent
    from .economy import EconomySystem
    from .butterfly_effect import ButterflyEffect
    from .option_engine import OptionEngine
    from .streamer import NarrativeStreamer
    from .world_generator import WorldGenerator
    from .brain_whispers import BrainWhispers
    from .player_memoir import PlayerMemoir
    from .faction_wars import FactionWars
    from .faction_war import NpcFavorEvents
    from .death_system import DeathSystem
    from .destiny_regret import DestinyRegret
    from .hundred_life_book import HundredLifeBook
    from .item_skill import ItemSystem, SkillTree
    from .quest_system import QuestSystem
    from .world_template import WorldTemplate
    from .weather_effects import WeatherEffects
    from .reputation import ReputationSystem
    from .llm_cache import LLMCache
    from .npc_autonomous import NpcAutonomous
    from .visual_engine import VisualEngine
    from .npc_perception import NPCPerceptionSystem
    from .influence_network import InfluenceNetwork
    from .rag_historical import RAGHistoricalStore
    from .npc_life_evolution import NpcLifeEvolution
    from .level_system import LevelSystem, GodsCodex
    from .branch_planner import BranchPlanner
    from .group_chat import GroupChatManager
    from .novel_importer import NovelImporter
    from .graph_rag import GraphRAG
    from .character_card import CharacterCard
    from .narrative_style import NarrativeStyleManager
    # [v10] 延迟导入新模块
    from .narrative_reviewer import NarrativeReviewer
    from .npc_procedural_memory import NPCProceduralMemory
    from .world_task_board import WorldTaskBoard
    from .memory_curator import MemoryCurator
    # [v10++] NPC 技能自学库（Voyager/Hermes 式）
    from .npc_skill_library import NPCSkillLibrary
    from .foreshadow_lifecycle import ForeshadowLifecycle
    from .continuity_auditor import ContinuityAuditor
    # [v10++] 角色动态状态管理器（CHIRON 式）
    from .character_state import CharacterStateManager
    # [v10++] NPC 反思机制（Generative Agents 式）
    from .npc_reflection import NPCReflection
    # [v10++] 上下文工程
    from .context_engine import ContextEngine
    # [v10+] 混合检索
    from .retrieval.bm25_retriever import BM25Retriever
    from .retrieval.hybrid_retriever import HybridRetriever
    # [v10+] 叙事场景检测器（GraphRAG 动态启停）
    from .narrative_scene_detector import SceneDetector
    # [v10+] SillyTavern 世界书导入器
    from .world_info_importer import WorldInfoImporter
    # [v10++] Agent 自主记忆管理（MemGPT/Letta 式）
    from .autonomous_memory import AutonomousMemoryManager
    # [v10+++] 多智能体分工叙事（Agents' Room 式）
    from .multi_agent_narrative import MultiAgentNarrativeEngine
    # [v10++] MCP 工具协议层（Model Context Protocol 兼容）
    from .mcp_tools import MCPToolRegistry, register_builtin_tools

    svc = ServiceRegistry(llm=llm)

    # [v10.5+] 三模型分层：根据子系统用途绑定默认 task_type
    #   - dialogue_llm（对话模型）：游戏内叙事/NPC对话/选项生成（玩家直接感知）
    #   - cheap_llm（备用模型）：蝴蝶评估/记忆整理/审计等辅助任务
    #   - main_llm（主力模型）：世界生成、角色卡、多智能体关键剧情等重活（不绑定，默认走 main）
    # 若 llm 是 LLMRouter 则通过 bind_task_type 绑定；否则直接透传（兼容测试场景）
    from .llm.router import LLMRouter as _LLMRouter, TASK_DIALOGUE as _TASK_DIALOGUE, TASK_SIMPLE as _TASK_SIMPLE
    def _dlg_llm():
        """返回绑定 TASK_DIALOGUE 的 LLM 视图（对话模型）"""
        return llm.bind_task_type(_TASK_DIALOGUE) if isinstance(llm, _LLMRouter) else llm

    def _cheap_llm():
        """返回绑定 TASK_SIMPLE 的 LLM 视图（备用模型）"""
        return llm.bind_task_type(_TASK_SIMPLE) if isinstance(llm, _LLMRouter) else llm

    svc.lorebook = Lorebook()
    # [v10+] SillyTavern 世界书导入器（无状态工具，供 API 端点复用）
    svc.world_info_importer = WorldInfoImporter()
    # [v9] 叙事风格管理器注入到NarrativeEngine
    _config_path = Path(__file__).parent.parent / "config.json"
    style_mgr = NarrativeStyleManager(config_path=_config_path)
    # [v10++] 上下文引擎：先创建，再注入到 NarrativeEngine，便于叙事生成时复用
    svc.context_engine = ContextEngine()
    # [v10.5+] NarrativeEngine 生成游戏叙事 → 对话模型
    svc.narrative = NarrativeEngine(
        _dlg_llm(), style_manager=style_mgr, context_engine=svc.context_engine
    )
    svc.age_system = AgeSystem()
    # v7: 创建分支思维规划器，注入到 NPCAgent
    # [v10.5+] BranchPlanner 用于 NPC 行为规划 → 备用模型
    svc.branch_planner = BranchPlanner(_cheap_llm())
    # [v10.5+] NPCAgent 生成 NPC 对话 → 对话模型
    svc.npc_agent = NPCAgent(_dlg_llm(), planner=svc.branch_planner)
    svc.economy_system = EconomySystem()
    # [v10.5+] ButterflyEffect 蝴蝶评估/后果生成 → 备用模型
    svc.butterfly = ButterflyEffect(_cheap_llm())
    # [v10.5+] OptionEngine 选项生成（玩家直接感知）→ 对话模型
    svc.option_engine = OptionEngine(_dlg_llm())
    svc.streamer = NarrativeStreamer()
    # [v10.5] world_generator 和 world_template 改为懒加载：仅世界生成时使用
    svc.world_generator = None  # 懒加载：仅 /api/generate-world 使用
    # [v10.5+] BrainWhispers 内心独白 → 对话模型
    svc.brain_whispers = BrainWhispers(_dlg_llm())
    # [v10.5+] PlayerMemoir 人物传记 → 备用模型（非实时，可容忍稍慢）
    svc.memoir = PlayerMemoir(_cheap_llm())
    svc.favor_events = NpcFavorEvents(_cheap_llm())
    svc.faction_wars = FactionWars(_cheap_llm())
    svc.visual_engine = VisualEngine(llm)  # 图像生成用主力
    svc.gods_codex = GodsCodex()
    svc.destiny_regret = DestinyRegret(_cheap_llm())
    svc.death_system = DeathSystem(_cheap_llm())
    svc.hundred_life_book = HundredLifeBook(_cheap_llm(), str(save_manager.base_dir))
    svc.item_system = ItemSystem(_cheap_llm())
    svc.skill_tree = SkillTree(_cheap_llm())
    svc.quest_system = QuestSystem(_cheap_llm())
    svc.world_template = None  # [v10.5] 懒加载：仅世界生成时使用
    svc.weather_effects = WeatherEffects(_cheap_llm())
    svc.reputation_system = ReputationSystem()
    svc.llm_cache = LLMCache(llm)  # 缓存层用原始 router
    svc.npc_perception = NPCPerceptionSystem()
    svc.influence_network = InfluenceNetwork()
    svc.rag_historical = RAGHistoricalStore(_cheap_llm())
    svc.npc_life_evolution = NpcLifeEvolution()
    # [v10.5+] NpcAutonomous NPC 自主行动 → 对话模型（影响叙事质量）
    svc.npc_autonomous = NpcAutonomous(_dlg_llm())
    # v7 新增服务
    # [v10.5+] GroupChatManager 群聊对话 → 对话模型
    svc.group_chat = GroupChatManager(_dlg_llm())
    # [v10.5] 以下工具型服务改为懒加载（仅在对应 API 端点首次调用时创建）
    # 这些服务无状态、不参与存档/读档，懒加载可减少启动开销
    svc.novel_importer = None  # 懒加载：仅 /api/import-novel 使用
    # [v10.5+] GraphRAG 实体/关系抽取 → 备用模型
    svc.graph_rag = GraphRAG(_cheap_llm())
    svc.character_card = None  # 懒加载：仅 /api/character-card 使用
    # [v10] 新增服务
    # [v10.5+] NarrativeReviewer 叙事回顾 → 备用模型
    svc.narrative_reviewer = NarrativeReviewer(_cheap_llm())
    svc.npc_procedural_memory = NPCProceduralMemory()
    svc.world_task_board = WorldTaskBoard()
    # [v10.5+] MemoryCurator 记忆整理 → 备用模型
    svc.memory_curator = MemoryCurator(_cheap_llm())
    # [v10++] NPC 技能自学库（Voyager/Hermes 式）
    # MemoryStore 在世界加载后由 game_engine 注入（set_memory_store）
    svc.npc_skill_library = NPCSkillLibrary(llm=_cheap_llm(), memory_store=None)
    # [v10+] 新增服务
    svc.foreshadow_lifecycle = ForeshadowLifecycle()
    # [v10.5+] ContinuityAuditor 连续性审计 → 备用模型
    svc.continuity_auditor = ContinuityAuditor(_cheap_llm())
    # [v10++] 角色动态状态管理器（CHIRON 式）
    svc.character_state_manager = CharacterStateManager(_cheap_llm())
    # [v10++] NPC 反思机制（Generative Agents 式）
    # MemoryStore 在世界加载后由 game_engine 注入（set_memory_store）
    svc.npc_reflection = NPCReflection(llm=_cheap_llm(), memory_store=None)
    # [v10+] 混合检索：BM25 + 向量 + GraphRAG
    # vector_store（MemoryStore）在世界加载后由 game_engine 注入
    svc.bm25_retriever = BM25Retriever()
    # [v10+] 叙事场景检测器：按场景类型动态调整检索权重（GraphRAG 动态启停）
    svc.scene_detector = SceneDetector()
    svc.hybrid_retriever = HybridRetriever(
        bm25=svc.bm25_retriever,
        vector_store=None,  # 延迟注入：等待 MemoryStore 创建
        graph_rag=svc.graph_rag,
        scene_detector=svc.scene_detector,
    )
    logger.info("HybridRetriever created (bm25 + graph_rag + scene_detector, vector_store pending)")
    # [v10++] Agent 自主记忆管理（MemGPT/Letta 式）
    # MemoryStore 在世界加载后由 game_engine 注入（set_memory_store）
    svc.autonomous_memory = AutonomousMemoryManager(memory_store=None, llm=_cheap_llm())
    # [v10+++] 多智能体分工叙事（Agents' Room 式）：仅用于关键剧情，普通回合走单 LLM
    # [v10.5+] 关键剧情需要最高质量 → 主力模型（不绑定 task_type）
    svc.multi_agent_narrative = MultiAgentNarrativeEngine(llm=llm)
    # [v10++] MCP 工具协议层：创建注册表实例，供插件注册自定义工具。
    # 内置工具的注册在 GameEngine._init_services 中调用 register_builtin_tools 完成
    # （因为内置工具需要 engine 引用绑定到各子系统）。
    svc.mcp_registry = MCPToolRegistry()
    logger.info("MCPToolRegistry created (builtin tools will be registered by GameEngine)")
    # [v10.5] 插件加载移至 GameEngine._init_services，以便钩子绑定到 engine 实例
    return svc


# ── 插件系统 ──────────────────────────────────────────────
# [v10.5] 全局钩子表保留作为向后兼容，但主流程已改为 GameEngine 实例级钩子。
# 新代码应使用 engine.register_hook / engine.trigger_hook。
_plugin_hooks: dict[str, list] = {
    "on_turn_start": [],
    "on_turn_end": [],
    "on_npc_action": [],
    "on_narrative_generated": [],
    "on_world_event": [],
    "on_player_input": [],
    # [v9] 新增钩子
    "on_dice_roll": [],
    "on_death": [],
    "on_save": [],
    "on_economy_trade": [],
    # [v10] 新增钩子
    "on_narrative_review": [],
    "on_task_completed": [],
    "on_memory_curated": [],
    "on_butterfly_approval": [],
    # [v10+] 新增钩子
    "on_continuity_audit": [],
    "on_foreshadow_stale": [],
}


def register_hook(hook_name: str, callback):
    """[废弃] 注册一个插件钩子到全局表。
    v10.5 起推荐使用 engine.register_hook()，全局表仅用于无 engine 上下文的场景。"""
    if hook_name not in _plugin_hooks:
        _plugin_hooks[hook_name] = []
    _plugin_hooks[hook_name].append(callback)
    logger.info("Plugin hook registered (global): %s -> %s", hook_name, callback.__name__)


def trigger_hook(hook_name: str, **kwargs):
    """[废弃] 触发全局插件钩子。
    v10.5 起推荐使用 engine.trigger_hook()。"""
    for callback in _plugin_hooks.get(hook_name, []):
        try:
            callback(**kwargs)
        except Exception as e:
            logger.warning("Plugin hook '%s' error: %s", hook_name, e)


def _load_plugins(svc: ServiceRegistry, engine=None):
    """从 plugins/ 目录动态加载插件。

    [v10.5] 若传入 engine，插件钩子将注册到 engine 实例（实例级隔离）；
    否则回退到全局 _plugin_hooks（向后兼容）。
    """
    plugin_dir = Path(__file__).parent.parent / "plugins"
    if not plugin_dir.exists():
        return
    # 选择注册函数：优先使用 engine 实例级注册
    register_fn = engine.register_hook if (engine and hasattr(engine, "register_hook")) else register_hook
    for plugin_file in plugin_dir.glob("*.py"):
        if plugin_file.name.startswith("_"):
            continue
        try:
            module_name = f"plugins.{plugin_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                # [v10.5] 注册到 sys.modules 后再 exec_module，
                # 否则 @dataclass 装饰器在解析类时无法通过 sys.modules 查找模块命名空间
                import sys
                sys.modules[module_name] = module
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    # exec 失败则清理 sys.modules，避免残留
                    sys.modules.pop(module_name, None)
                    raise
                if hasattr(module, "register"):
                    # 传入 engine（GameEngine 实例）而非 svc，使插件能访问完整引擎状态
                    target = engine if engine is not None else svc
                    module.register(target, register_fn)
                    logger.info("Plugin loaded: %s", plugin_file.name)
        except Exception as e:
            logger.warning("Plugin load failed: %s - %s", plugin_file.name, e)
