"""
[v1.2] 目标判定与演化引擎 — 让 NPC 的目标真正"活起来"。

设计要点（参考用户需求）：
  - 短期目标经常变化：复仇→夺秘籍→武林大会夺魁
  - 长期目标到达阶段后才变：筑基→金丹→元婴→化神→...
  - AI 判断目标是否达成 + 派生下一目标
  - 但不能每次行动都调 LLM 判定（成本爆炸）

三层判定流程（混合模式：规则触发 + LLM 派生）：
  Step 1 规则信号检测：用关键词+状态匹配，识别"目标可能已达成"的信号
         （如短期目标含"杀X"且 X 已死；"得到X"且 X 在 tags 中；"到达X"且位置匹配）
  Step 2 LLM 判定：仅对触发信号的目标调 cheap_llm，让它判定 ①是否真完成 ②若完成，派生下一短期目标
  Step 3 长期目标演化：仅在跨年时触发，LLM 判定是否达成 + 派生新长期目标

数据结构：
  NPCState.ai_behavior = {
    "current_goal": str,
    "long_term_goal": str,
    "short_term_goals": list[str],   # 队列
    "decision_style": str,
  }
"""
from __future__ import annotations
import re
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import NPCState, PlayerState, WorldState
    from .llm.base_llm import BaseLLM

logger = logging.getLogger("chronoverse.goal_eval")


# ===== 规则信号检测模板 =====
# 每条规则：(目标关键词正则, 完成信号检测函数)
# 检测函数签名: (goal_text, npc, world_state, all_npcs) -> bool


def _check_kill_goal(goal_text: str, npc: "NPCState",
                      world_state: "WorldState", all_npcs: dict) -> bool:
    """目标含'杀/除/诛/灭/斩 X' → 检查 X 是否在 all_npcs 中且标记已故"""
    m = re.search(r'(?:杀|除|诛|灭|斩|杀死|干掉|了结)\s*([\u4e00-\u9fa5A-Za-z]{2,8})', goal_text)
    if not m:
        return False
    target_name = m.group(1).strip()
    # 在 NPC 列表里找这个人
    for nid, n in all_npcs.items():
        if target_name in n.name and "已故" in (n.tags or []):
            return True
    return False


def _check_acquire_goal(goal_text: str, npc: "NPCState",
                         world_state: "WorldState", all_npcs: dict) -> bool:
    """目标含'得到/获得/取得/寻得 X' → 检查 X 是否在 npc.tags 或 role 中"""
    m = re.search(r'(?:得到|获得|取得|寻得|找到|获赠|继承)\s*([\u4e00-\u9fa5A-Za-z]{2,12})', goal_text)
    if not m:
        return False
    target_item = m.group(1).strip()
    # 简化：检查 tags 是否包含目标物名（粗略，LLM 会做精判）
    tags = npc.tags or []
    role = npc.role or ""
    return target_item in tags or target_item in role


def _check_reach_goal(goal_text: str, npc: "NPCState",
                       world_state: "WorldState", all_npcs: dict) -> bool:
    """目标含'到达/前往/抵达 X' → 检查 NPC 当前位置"""
    m = re.search(r'(?:到达|前往|抵达|去|到)\s*([\u4e00-\u9fa5A-Za-z]{2,12})', goal_text)
    if not m:
        return False
    target_loc = m.group(1).strip()
    current_loc = npc.current_location or ""
    # 粗略匹配（location code 或中文名）
    return target_loc in current_loc or current_loc in target_loc


def _check_realm_goal(goal_text: str, npc: "NPCState",
                       world_state: "WorldState", all_npcs: dict) -> bool:
    """目标含'突破到 X / 修炼到 X 境界' → 检查 tags"""
    m = re.search(r'(?:突破|修炼|晋升|进阶|成就)\s*(?:到|至)?\s*([\u4e00-\u9fa5]{2,8})', goal_text)
    if not m:
        return False
    realm = m.group(1).strip()
    tags = npc.tags or []
    return realm in tags


def _check_meet_goal(goal_text: str, npc: "NPCState",
                      world_state: "WorldState", all_npcs: dict) -> bool:
    """目标含'见到/找到/拜访 X' → 检查 NPC 与 X 是否同地点（即已会面）"""
    m = re.search(r'(?:见到|找到|拜访|会见|寻访)\s*([\u4e00-\u9fa5A-Za-z]{2,8})', goal_text)
    if not m:
        return False
    target_name = m.group(1).strip()
    npc_loc = npc.current_location or ""
    for nid, n in all_npcs.items():
        if target_name in n.name and n.current_location == npc_loc:
            return True
    return False


# 规则集
GOAL_COMPLETION_RULES = [
    (_check_kill_goal,    "kill"),
    (_check_reach_goal,   "reach"),
    (_check_acquire_goal, "acquire"),
    (_check_realm_goal,   "realm"),
    (_check_meet_goal,    "meet"),
]


# ===== LLM Prompt =====

GOAL_EVAL_PROMPT = """你是虚拟世界的目标判定器。请判定 NPC 的目标是否已达成，并派生下一短期目标。

【NPC 信息】
姓名：{npc_name}
年龄：{age}岁
身份：{role}
性格：{personality}
当前标签：{tags}
当前位置：{location}
当前状态：{status_effects}

【NPC 长期目标】
{long_term_goal}

【待判定的短期目标】
{short_term_goal}

【近期行动记录】
{recent_actions}

【NPC 当前感知】（视野隔离：只能基于此信息决策）
{perception_brief}

【世界背景】
{world_context}

【判定规则 - 必须遵守】
1. 只有当 NPC 通过自身行动或外部事件**实际**完成了目标时，才判定为"已达成"
2. 目标含糊（如"修炼提升"）→ 默认判定"未达成"（除非有明确的境界突破记录）
3. 派生的下一短期目标必须：
   - 与长期目标方向一致
   - 具体、可验证（含明确的对象/地点/物品）
   - 难度递进（不能比刚完成的目标更简单）
   - 符合 NPC 当前处境（位置/状态/资源）
4. 【视野隔离】NPC 只能基于「当前感知」中的人物/事件做决策，
   不得引用视野外的人物状态或未听闻的事件

【输出 JSON 格式 - 严格遵循】
{{
  "achieved": true/false,
  "achievement_summary": "（若达成）一句话描述完成方式；若未达成则空",
  "next_short_term_goal": "（若达成）派生的下一个短期目标；若未达成则空",
  "next_goal_reason": "（若达成）派生理由；若未达成则空"
}}"""


LONG_TERM_EVOLUTION_PROMPT = """你是虚拟世界的人生阶段推演器。NPC 的长期目标已稳定执行一年，请判定是否应进入下一阶段。

【NPC 信息】
姓名：{npc_name}
年龄：{age}岁（增长了一岁）
身份：{role}
性格：{personality}
标签：{tags}
立场：{alignment}
所属势力：{faction_id}

【当前长期目标】
{long_term_goal}

【本年完成的所有短期目标】
{completed_goals_this_year}

【本年重大事件】
{major_events_this_year}

【NPC 与主角的关系】
{relation_info}

【判定规则】
1. 仅当 NPC 实力/地位/认知有**显著阶段性跃迁**时，才判定长期目标达成
2. 例：修仙者筑基圆满→长期目标从"筑基"变为"金丹"；商人从小富→中富→大富
3. 新长期目标必须：
   - 比原长期目标更高阶
   - 符合 NPC 性格与世界观
   - 有明确的"完成判定标准"（如"金丹期"、"成为武林盟主"）

【输出 JSON 格式】
{{
  "long_term_achieved": true/false,
  "achievement_summary": "（若达成）一句话描述本阶段成就；若未达成则空",
  "new_long_term_goal": "（若达成）新长期目标；若未达成则保留原长期目标",
  "new_short_term_goal": "（若达成）新长期目标下的第一个短期目标；若未达成则空",
  "reason": "判定理由"
}}"""


class GoalEvaluator:
    """目标判定与演化引擎

    用法：
        evaluator = GoalEvaluator(llm)
        # 短期目标判定（每天调）
        result = evaluator.evaluate_short_term_goal(npc, world_state, all_npcs)
        # 长期目标演化（跨年调）
        result = evaluator.evolve_long_term_goal(npc, world_state, year_events)
    """

    def __init__(self, llm: "BaseLLM | None" = None,
                 perception_scope=None):
        self.llm = llm
        # [v1.2] 视野隔离硬执行器（可选，由 game_engine 注入）
        self.perception_scope = perception_scope
        # 缓存：避免同一目标重复判定（key=npc_id:goal_text, value=最近的判定结果）
        self._eval_cache: dict[str, dict] = {}
        self._cache_ttl_days = 3  # 同一目标 3 天内不重复判定

    def set_perception_scope(self, scope):
        """延迟注入 PerceptionScope（与 LLM 注入同样的延迟模式）"""
        self.perception_scope = scope

    # ===== 短期目标判定 =====

    def evaluate_short_term_goal(
        self,
        npc: "NPCState",
        world_state: "WorldState",
        all_npcs: dict,
        force: bool = False,
    ) -> dict:
        """判定 NPC 当前短期目标是否达成。

        Args:
            npc: 待判定 NPC
            world_state: 世界状态
            all_npcs: 全部 NPC 字典（用于查目标对象状态）
            force: 强制判定（忽略缓存）

        Returns:
            {
                "has_goal": bool,            # 是否有当前短期目标
                "achieved": bool,            # 是否达成
                "achievement_summary": str,  # 达成方式
                "next_short_term_goal": str, # 派生的下一目标
                "next_goal_reason": str,     # 派生理由
                "rule_triggered": str,       # 触发的规则名（kill/reach/...）
                "skipped": bool,             # 是否因缓存/无目标跳过
            }
        """
        ai_behavior = npc.ai_behavior or {}
        current_goal = (ai_behavior.get("current_goal") or "").strip()
        short_term_goals = ai_behavior.get("short_term_goals", []) or []

        # 没有目标
        if not current_goal and not short_term_goals:
            return {"has_goal": False, "skipped": True}

        # 当前要判定的目标（current_goal 优先，否则取队首）
        goal_to_check = current_goal or (short_term_goals[0] if short_term_goals else "")

        # [v1.2] 视野隔离硬执行：sleeping 区 NPC 跳过 LLM 思考（节省成本）
        # 但规则信号检测仍可执行（不调 LLM）
        skip_llm = False
        if self.perception_scope:
            try:
                skip_llm = self.perception_scope.should_skip_thinking(
                    npc, world_state, player=None,
                )
            except Exception as e:
                logger.debug("[GoalEval] perception scope check failed for %s: %s",
                             npc.name, e)

        # 缓存检查
        cache_key = f"{npc.agent_id}:{goal_to_check}"
        if not force and self._is_cached(cache_key, world_state.current_day):
            return {"has_goal": True, "skipped": True, "achieved": False}

        # Step 1: 规则信号检测
        rule_triggered = None
        for rule_fn, rule_name in GOAL_COMPLETION_RULES:
            try:
                if rule_fn(goal_to_check, npc, world_state, all_npcs):
                    rule_triggered = rule_name
                    break
            except Exception as e:
                logger.debug("Goal rule %s failed for %s: %s", rule_name, npc.name, e)

        # 没有规则触发 → 不调 LLM，节省成本
        if not rule_triggered:
            # 更新缓存：今天判过了
            self._eval_cache[cache_key] = {
                "day": world_state.current_day,
                "achieved": False,
                "rule_triggered": None,
            }
            return {
                "has_goal": True,
                "achieved": False,
                "rule_triggered": None,
                "skipped": False,
            }

        # Step 2: LLM 判定（仅在规则触发时）
        # [v1.2] 视野隔离：sleeping 区 NPC 不调 LLM，直接信任规则
        if skip_llm:
            logger.debug("[GoalEval] %s in sleeping zone, skip LLM, use rule only",
                         npc.name)
            return self._rule_only_fallback(npc, goal_to_check, rule_triggered, world_state)

        if not self.llm:
            # 没 LLM 时降级：直接信任规则，机械派生
            return self._rule_only_fallback(npc, goal_to_check, rule_triggered, world_state)

        try:
            prompt = self._build_eval_prompt(npc, goal_to_check, world_state)
            result = self.llm.chat_json(prompt, temperature=0.3, max_tokens=512)
            achieved = bool(result.get("achieved", False))
            next_goal = (result.get("next_short_term_goal") or "").strip()
            summary = (result.get("achievement_summary") or "").strip()
            reason = (result.get("next_goal_reason") or "").strip()

            # 更新缓存
            self._eval_cache[cache_key] = {
                "day": world_state.current_day,
                "achieved": achieved,
                "rule_triggered": rule_triggered,
            }

            # 若 LLM 判定达成，应用变更
            if achieved:
                self._apply_goal_completion(npc, goal_to_check, next_goal, summary, world_state)
                logger.info(
                    "[GoalEval] %s 完成短期目标「%s」（规则:%s）→ 派生「%s」",
                    npc.name, goal_to_check, rule_triggered, next_goal,
                )

            return {
                "has_goal": True,
                "achieved": achieved,
                "achievement_summary": summary,
                "next_short_term_goal": next_goal,
                "next_goal_reason": reason,
                "rule_triggered": rule_triggered,
                "skipped": False,
            }
        except Exception as e:
            logger.warning("Goal LLM eval failed for %s: %s", npc.name, e)
            return self._rule_only_fallback(npc, goal_to_check, rule_triggered, world_state)

    # ===== 长期目标演化（跨年） =====

    def evolve_long_term_goal(
        self,
        npc: "NPCState",
        world_state: "WorldState",
        year_events: list[dict] = None,
    ) -> dict:
        """跨年时判定长期目标是否应进入下一阶段。

        Returns:
            {
                "evolved": bool,
                "old_long_term_goal": str,
                "new_long_term_goal": str,
                "new_short_term_goal": str,
                "reason": str,
            }
        """
        ai_behavior = npc.ai_behavior or {}
        old_long_term = (ai_behavior.get("long_term_goal") or "").strip()

        if not old_long_term:
            return {"evolved": False}

        if not self.llm:
            return {"evolved": False}

        # 收集本年完成的短期目标
        completed_this_year = []
        for action in (npc.recent_actions or []):
            if action.get("action") == "complete_goal":
                if world_state.current_day - action.get("day", 0) <= 365:
                    completed_this_year.append(action.get("detail", ""))

        # 本年重大事件（从 npc.recent_actions 提取关键词事件）
        major_events = []
        recent_actions = (npc.recent_actions or [])[-10:]
        for action in recent_actions:
            if not isinstance(action, dict):
                continue
            detail = action.get("detail") or action.get("action") or ""
            if any(kw in detail for kw in ("婚", "死", "生", "突", "得", "失", "伤", "成")):
                major_events.append(f"第{action.get('day', 0)}天: {detail}")
        major_events_str = "\n".join(major_events[:8]) or "（无重大事件）"

        # 关系信息
        relation_info = "无特殊关系"
        rel = getattr(npc, "relation_to_player", None)
        if rel:
            relation_info = f"好感 {getattr(rel, 'favor', 50)}，信任 {getattr(rel, 'trust', 50)}"

        prompt = LONG_TERM_EVOLUTION_PROMPT.format(
            npc_name=npc.name,
            age=npc.age,
            role=npc.role or "普通居民",
            personality=npc.personality or "普通",
            tags="、".join(npc.tags or []) or "无",
            alignment=getattr(npc, "alignment", "中庸"),
            faction_id=getattr(npc, "faction_id", "") or "无",
            long_term_goal=old_long_term,
            completed_goals_this_year="\n".join(completed_this_year) or "（无明确完成的短期目标）",
            major_events_this_year=major_events_str,
            relation_info=relation_info,
        )

        try:
            result = self.llm.chat_json(prompt, temperature=0.4, max_tokens=640)
            evolved = bool(result.get("long_term_achieved", False))
            new_long_term = (result.get("new_long_term_goal") or "").strip()
            new_short = (result.get("new_short_term_goal") or "").strip()
            reason = (result.get("reason") or "").strip()

            if evolved and new_long_term:
                ai_behavior["long_term_goal"] = new_long_term
                if new_short:
                    ai_behavior["short_term_goals"] = [new_short]
                    ai_behavior["current_goal"] = new_short
                npc.ai_behavior = ai_behavior

                # 记入行动日志
                npc.recent_actions.append({
                    "day": world_state.current_day,
                    "action": "long_term_evolve",
                    "detail": f"长期目标从「{old_long_term}」演化为「{new_long_term}」",
                })
                # 保留最近 10 条
                if len(npc.recent_actions) > 10:
                    npc.recent_actions = npc.recent_actions[-10:]

                logger.info(
                    "[GoalEval] %s 长期目标演化：「%s」→「%s」",
                    npc.name, old_long_term, new_long_term,
                )
                return {
                    "evolved": True,
                    "old_long_term_goal": old_long_term,
                    "new_long_term_goal": new_long_term,
                    "new_short_term_goal": new_short,
                    "reason": reason,
                }
            return {"evolved": False, "reason": reason}
        except Exception as e:
            logger.warning("Long term goal evolution failed for %s: %s", npc.name, e)
            return {"evolved": False, "error": str(e)}

    # ===== 工具方法 =====

    def _is_cached(self, cache_key: str, current_day: int) -> bool:
        """缓存是否有效"""
        cached = self._eval_cache.get(cache_key)
        if not cached:
            return False
        return current_day - cached.get("day", 0) < self._cache_ttl_days

    def _build_eval_prompt(self, npc: "NPCState", goal: str,
                            world_state: "WorldState") -> str:
        recent_actions_str = "\n".join(
            f"  - 第{a.get('day', 0)}天: {a.get('detail') or a.get('action', '')}"
            for a in (npc.recent_actions or [])[-6:]
        ) or "（无近期行动）"

        world_context = (
            f"{getattr(world_state, 'world_name', '太虚幻境')}，"
            f"第{world_state.current_day}天，{world_state.season}，{world_state.weather}"
        )

        # [v1.2] 视野隔离：构造 NPC 当前可感知的环境摘要
        perception_brief = "（感知系统未启用）"
        if self.perception_scope:
            try:
                perception_brief = self.perception_scope.build_perception_brief(
                    npc, world_state=world_state,
                )
            except Exception as e:
                logger.debug("[GoalEval] build perception brief failed for %s: %s",
                             npc.name, e)

        prompt = GOAL_EVAL_PROMPT.format(
            npc_name=npc.name,
            age=npc.age,
            role=npc.role or "普通居民",
            personality=npc.personality or "普通",
            tags="、".join(npc.tags or []) or "无",
            location=npc.current_location or "未知",
            status_effects="、".join(npc.status_effects or []) or "正常",
            long_term_goal=(npc.ai_behavior or {}).get("long_term_goal", "") or "（未设定）",
            short_term_goal=goal,
            recent_actions=recent_actions_str,
            perception_brief=perception_brief,
            world_context=world_context,
        )

        # [v1.2] 知识边界硬过滤：剔除 forbidden_knowledge 关键词
        if self.perception_scope:
            try:
                prompt = self.perception_scope.enforce_knowledge_scope(npc, prompt)
            except Exception:
                pass

        return prompt

    def _rule_only_fallback(self, npc: "NPCState", goal: str,
                             rule_name: str, world_state: "WorldState") -> dict:
        """无 LLM 时的降级：直接信任规则，机械派生下一目标"""
        long_term = (npc.ai_behavior or {}).get("long_term_goal", "")
        next_goal = f"为「{long_term}」迈进一步" if long_term else "继续前行"
        self._apply_goal_completion(npc, goal, next_goal, f"规则触发({rule_name})", world_state)
        return {
            "has_goal": True,
            "achieved": True,
            "achievement_summary": f"规则触发({rule_name})",
            "next_short_term_goal": next_goal,
            "next_goal_reason": "无LLM时降级派生",
            "rule_triggered": rule_name,
            "skipped": False,
        }

    def _apply_goal_completion(self, npc: "NPCState", old_goal: str,
                                new_goal: str, summary: str,
                                world_state: "WorldState"):
        """应用目标完成：从 short_term_goals 移除旧的，写入 recent_actions"""
        ai_behavior = npc.ai_behavior or {}
        short_term_goals = ai_behavior.get("short_term_goals", []) or []

        # 移除已完成的（含 current_goal）
        if old_goal in short_term_goals:
            short_term_goals.remove(old_goal)

        # 派生新目标入队
        if new_goal:
            short_term_goals.insert(0, new_goal)

        # 更新 current_goal
        ai_behavior["current_goal"] = short_term_goals[0] if short_term_goals else new_goal
        ai_behavior["short_term_goals"] = short_term_goals
        npc.ai_behavior = ai_behavior

        # 记入行动日志
        npc.recent_actions.append({
            "day": world_state.current_day,
            "action": "complete_goal",
            "detail": f"完成短期目标「{old_goal}」→ 派生「{new_goal}」({summary})",
        })
        if len(npc.recent_actions) > 10:
            npc.recent_actions = npc.recent_actions[-10:]

    # ===== 动机→目标 转换（与 motivation 模块联动）=====

    MOTIVATION_GOAL_TEMPLATES = {
        "survival": [
            "寻找{target}的庇护", "筹集疗伤药材", "寻找安全的栖身之所",
            "向{name}求助", "摆脱眼前的危局",
        ],
        "social": [
            "拜访{target}", "与{name}叙旧", "赠送{name}礼物",
            "邀请{name}外出", "修复与{name}的关系",
        ],
        "career": [
            "提升{career}技艺", "接一笔生意", "向{name}求教",
            "寻找合作机会", "积累名声",
        ],
        "exploration": [
            "探索{target}", "寻访新地点", "向{name}打听消息",
            "外出冒险", "记录新发现",
        ],
        "legacy": [
            "寻觅传人", "传授毕生所学", "托付后事",
            "与{name}交代身后事", "整理毕生心得",
        ],
        "transcendence": [
            "突破当前境界", "与{name}切磋论道", "寻访名师指点",
            "在{target}闭关", "证道悟法",
        ],
    }

    def generate_goal_from_motivation(
        self, npc: "NPCState", motivation: dict,
        world_state: "WorldState", all_npcs: dict,
    ) -> str | None:
        """根据 NPC 的强动机生成对应的短期目标。

        仅在动机强度 > 50 且 NPC 没有当前目标时调用。

        Returns:
            生成的目标文本，或 None（无法生成）
        """
        mot_type = motivation.get("type", "")
        target = motivation.get("target", "")
        templates = self.MOTIVATION_GOAL_TEMPLATES.get(mot_type)
        if not templates:
            return None

        # 把 target/player 转换为具体 NPC 名字
        target_name = "主角"
        if target and target != "player":
            t_npc = all_npcs.get(target)
            if t_npc:
                target_name = t_npc.name
            else:
                target_name = target

        # 把 career 修仙/魔法相关
        career_kw = "修炼" if any(t in (npc.tags or []) for t in ("修士", "高僧", "真人")) else "本业"

        import random
        template = random.choice(templates)
        return template.format(target=target_name, name=target_name, career=career_kw)


# 全局单例
_global_evaluator: GoalEvaluator | None = None


def get_goal_evaluator() -> GoalEvaluator:
    """获取全局 GoalEvaluator 单例（llm 由 game_engine 注入）"""
    global _global_evaluator
    if _global_evaluator is None:
        _global_evaluator = GoalEvaluator()
    return _global_evaluator


def set_goal_evaluator(evaluator: GoalEvaluator):
    """注入带 LLM 的 evaluator（由 game_engine 启动时调用）"""
    global _global_evaluator
    _global_evaluator = evaluator
