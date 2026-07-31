"""
[v1.2] NPC 分层智能（AI LOD）— 核心NPC用LLM，次要NPC用CPU程序化推演。

设计目标（参考用户需求与 AI 方案）：
  - 大幅削减 LLM 调用次数（次要NPC不再调用LLM，token成本骤降）
  - 次要角色行为确定性强，避免人设崩坏
  - 规避大量NPC同时请求LLM造成堵塞

分层规则：
  Tier 1 (core)      → 完整 LLM 思考循环（BranchPlanner / _single_step_plan）
    判定条件（满足任一即为核心NPC）：
      - 与玩家同场景（current_location == player.location）
      - 与玩家关系密切（favor ≥ 60 或 ≤ 20，即盟友或仇敌）
      - 携带关键 tag（"重要"、"剧情"、"主角相关"）
      - 有 original_chapter >= 0（小说扮演模式下的已登场未来角色）
      - 有 long_term_goal 且非空（有明确长期目标的角色）

  Tier 2 (secondary) → CPU 程序化推演（不调用 LLM）
    判定：非 core 的 NPC
    特征：路人、低权重配角、远离玩家、无长期目标

CPU 程序化推演逻辑（procedural_evolve）：
  基于规则的行动选择，完全确定性，复用现有 action_type 词汇：
    1. 状态紧急（体力<30/血量<30）→ 休息/养伤
    2. 有 current_goal → 朝目标推进（按 goal 关键词映射行动）
    3. 在场有熟人 → 社交（拜访/交谈）
    4. 天气恶劣 → 留守（idle）
    5. 默认 → 日常（work/explore/idle 按性格倾向选择）

集成点：
  - NPCAgent.batch_evolve：根据 tier 分流
    core → 走原 offline_evolve（LLM）
    secondary → 走 procedural_evolve（CPU）
  - PerceptionScope.should_skip_thinking 优先于 LOD（sleeping 区直接跳过）
"""
from __future__ import annotations
import logging
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import NPCState, PlayerState, WorldState

logger = logging.getLogger("chronoverse.lod")


# [v1.2] 延迟导入日程模板（避免循环依赖）
def _get_routine_action(npc, world_state):
    try:
        from .daily_routine import get_routine_action as _get
        return _get(npc, world_state)
    except Exception:
        return None


# ===== 分层判定 =====

# 携带这些 tag 的 NPC 一律视为核心角色
CORE_TAGS = {"重要", "剧情", "主角相关", "关键", "主线"}

# 天气恶劣时次要 NPC 倾向留守
BAD_WEATHER = {"暴雨", "大雪", "狂风", "雷暴", "冰雹", "沙尘暴"}


def classify_tier(
    npc: "NPCState",
    player: "PlayerState | None",
    world_state: "WorldState | None",
) -> str:
    """判定 NPC 的 LOD 层级。

    Returns:
        "core"       → 走完整 LLM 思考循环
        "secondary"  → 走 CPU 程序化推演
    """
    # 1. 携带核心 tag → core
    tags = set(npc.tags or [])
    if tags & CORE_TAGS:
        return "core"

    # 2. 有正在推进的短期目标 → core（正在主动做事的角色值得 LLM 思考）
    # 注意：不能用 long_term_goal 判定，因为 NPCSpawner 给所有 NPC 都生成 long_term_goal
    ai_behavior = npc.ai_behavior or {}
    current_goal = ai_behavior.get("current_goal", "")
    if current_goal and current_goal.strip():
        return "core"

    # 3. 小说扮演模式下已登场的未来角色 → core
    if getattr(npc, "original_chapter", -1) >= 0 and not getattr(npc, "is_dormant", False):
        return "core"

    # 4. 与玩家同场景 → core
    if player and world_state:
        if npc.current_location and npc.current_location == player.location:
            return "core"

    # 5. 与玩家关系密切（盟友 favor≥60 或 仇敌 favor≤20）→ core
    if player:
        favor = npc.relation_to_player.favor if npc.relation_to_player else 50
        if favor >= 60 or favor <= 20:
            return "core"

    # 其余 → secondary（路人、无当前目标、远离玩家、关系平淡的 NPC）
    return "secondary"


# ===== CPU 程序化推演 =====

def procedural_evolve(
    npc: "NPCState",
    world_state: "WorldState",
    player: "PlayerState | None" = None,
    all_npcs: dict | None = None,
) -> dict:
    """[v1.2] CPU 程序化推演（不调用 LLM）。

    基于规则的行动选择，返回格式与 _single_step_evolve 一致：
        {
            "npc_id": str, "npc_name": str,
            "action": str, "detail": str,
            "location": str, "mood_change": int,
            "lod_tier": "secondary",
        }

    决策优先级：
      1. 状态紧急 → 休息/养伤
      2. 朝 current_goal 推进
      3. 在场有熟人 → 社交
      4. 天气恶劣 → 留守
      5. 默认 → 日常（按性格倾向）
    """
    day = world_state.current_day
    energy = npc.stats.energy if npc.stats else 50
    health = npc.stats.health if npc.stats else 100
    current_goal = (npc.ai_behavior or {}).get("current_goal", "") or ""
    long_term_goal = (npc.ai_behavior or {}).get("long_term_goal", "") or ""
    personality = (npc.personality or "").lower()
    weather = getattr(world_state, "weather", "") or ""

    action = "idle"
    detail = ""
    energy_cost = 5

    # ── 优先级 0：职业日程模板（时序感）──
    # 命中日程即用，让 NPC 行为符合当前时段的职业惯例
    # 但若 NPC 有 current_goal，目标优先（见优先级 2）
    routine = _get_routine_action(npc, world_state) if not current_goal else None

    # ── 优先级 1：状态紧急 → 休息/养伤 ──
    if energy < 30:
        action = "休息"
        detail = f"{npc.name}体力不支，寻处歇脚。"
        energy_cost = -20  # 恢复体力
    elif health < 30:
        action = "养伤"
        detail = f"{npc.name}伤势未愈，静养调理。"
        energy_cost = -10

    # ── 优先级 2：朝 current_goal 推进 ──
    elif current_goal:
        action, detail, energy_cost = _goal_driven_action(
            npc, current_goal, long_term_goal, world_state,
        )

    # ── 优先级 3：职业日程模板（无目标时按职业惯例）──
    elif routine is not None:
        action, detail, energy_cost = routine

    # ── 优先级 4：在场有熟人 → 社交 ──
    elif all_npcs and _has_acquaintance_nearby(npc, all_npcs):
        action = "交谈"
        acquaintance = _get_acquaintance_nearby(npc, all_npcs)
        detail = f"{npc.name}与{acquaintance}寒暄几句。"
        energy_cost = 5

    # ── 优先级 5：天气恶劣 → 留守 ──
    elif weather in BAD_WEATHER:
        action = "idle"
        detail = f"{npc.name}因{weather}留守室内。"
        energy_cost = 3

    # ── 优先级 6：默认 → 日常（按性格倾向）──
    else:
        action, detail, energy_cost = _routine_action(npc, personality, world_state)

    # 应用状态变化
    if action in ("休息", "养伤"):
        npc.stats.energy = min(100, npc.stats.energy + abs(energy_cost))
        if action == "养伤":
            npc.stats.health = min(100, npc.stats.health + 5)
    else:
        npc.stats.energy = max(0, npc.stats.energy - energy_cost)

    # 记录行动
    npc.recent_actions.append({
        "day": day,
        "action": action,
        "detail": detail,
        "location": npc.current_location,
    })
    if len(npc.recent_actions) > 10:
        npc.recent_actions = npc.recent_actions[-10:]

    return {
        "npc_id": npc.agent_id,
        "npc_name": npc.name,
        "action": action,
        "detail": detail,
        "location": npc.current_location,
        "mood_change": 0,
        "lod_tier": "secondary",
    }


# ===== 辅助：目标驱动行动 =====

def _goal_driven_action(
    npc: "NPCState",
    current_goal: str,
    long_term_goal: str,
    world_state: "WorldState",
) -> tuple[str, str, int]:
    """根据 current_goal 关键词映射行动"""
    goal = current_goal.lower()

    # 修炼/提升类
    if any(k in goal for k in ("修炼", "练功", "突破", "境界", "冥想")):
        return ("修炼", f"{npc.name}潜心修炼，朝「{current_goal}」推进。", 10)

    # 赶路/寻人/寻物类
    if any(k in goal for k in ("前往", "赶往", "寻找", "寻人", "寻物", "打听")):
        return ("赶路", f"{npc.name}动身赶路，意在「{current_goal}」。", 15)

    # 战斗/复仇类
    if any(k in goal for k in ("复仇", "刺杀", "挑战", "比武", "击败")):
        return ("练功", f"{npc.name}勤练武艺，为「{current_goal}」蓄力。", 12)

    # 学习/读书类
    if any(k in goal for k in ("读书", "学习", "参悟", "研究")):
        return ("读书", f"{npc.name}展卷研读，朝「{current_goal}」精进。", 5)

    # 采药/炼丹/制器类
    if any(k in goal for k in ("采药", "炼丹", "制器", "打造")):
        return ("采药", f"{npc.name}外出采办材料，服务于「{current_goal}」。", 8)

    # 赚钱/经商类
    if any(k in goal for k in ("赚钱", "经商", "售卖", "交易")):
        return ("工作", f"{npc.name}忙于生计，朝「{current_goal}」努力。", 8)

    # 通用：朝目标推进
    return ("练功", f"{npc.name}日常修行，朝「{current_goal}」稳步推进。", 8)


# ===== 辅助：熟人检测 =====

def _has_acquaintance_nearby(npc: "NPCState", all_npcs: dict) -> bool:
    """检查同场景是否有非休眠、非已故的 NPC"""
    return _get_acquaintance_nearby(npc, all_npcs) is not None


def _get_acquaintance_nearby(npc: "NPCState", all_npcs: dict) -> str | None:
    """返回同场景的熟人名字（无则 None）"""
    npc_loc = npc.current_location or ""
    if not npc_loc:
        return None
    for other_id, other in all_npcs.items():
        if other_id == npc.agent_id:
            continue
        if getattr(other, "is_dormant", False):
            continue
        if "已故" in (other.tags or []):
            continue
        if other.current_location == npc_loc:
            return other.name
    return None


# ===== 辅助：日常行动 =====

def _routine_action(
    npc: "NPCState",
    personality: str,
    world_state: "WorldState",
) -> tuple[str, str, int]:
    """根据性格倾向选择日常行动"""
    # 性格关键词 → 行动倾向
    if any(k in personality for k in ("好学", "聪慧", "勤勉", "刻苦")):
        return ("读书", f"{npc.name}展卷读书，打发时日。", 5)
    if any(k in personality for k in ("好武", "彪悍", "刚烈", "豪迈")):
        return ("练功", f"{npc.name}舞枪弄棒，活动筋骨。", 10)
    if any(k in personality for k in ("闲散", "慵懒", "随性", "散漫")):
        return ("idle", f"{npc.name}无所事事，晒晒太阳。", 2)
    if any(k in personality for k in ("好奇", "爱游", "活泼")):
        return ("探索", f"{npc.name}四处闲逛，看看新鲜。", 8)
    if any(k in personality for k in ("好客", "热络", "善交际")):
        return ("闲逛", f"{npc.name}出门溜达，寻人说话。", 5)

    # 默认：随机日常
    routines = [
        ("工作", f"{npc.name}忙于日常营生。", 8),
        ("idle", f"{npc.name}度过了平淡的一天。", 3),
        ("探索", f"{npc.name}在附近走走。", 6),
    ]
    return random.choice(routines)


# ===== 统计 =====

def classify_all(
    npcs: list["NPCState"],
    player: "PlayerState | None",
    world_state: "WorldState | None",
) -> dict:
    """批量分类，返回统计信息"""
    tiers = {"core": 0, "secondary": 0}
    for npc in npcs:
        if getattr(npc, "is_dormant", False):
            continue
        tier = classify_tier(npc, player, world_state)
        tiers[tier] += 1
    return {
        "tiers": tiers,
        "total": tiers["core"] + tiers["secondary"],
        "core_ratio": tiers["core"] / max(1, tiers["core"] + tiers["secondary"]),
    }
