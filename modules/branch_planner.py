"""
分支思维规划器 (Branch-Thinking Planner) — ToT 化升级版

参考 AIvilization 的 BTP 架构，实现多层目标分解：
1. 将总体目标分解为并行分支（生存/社交/职业/探索）
2. 根据当前状态选择最优先分支
3. 将分支目标转化为具体行动序列
4. 执行前模拟，检测约束违反
5. 自适应重规划

[v10+ ToT 化升级] 在 BTP 基础上引入 Tree of Thoughts 式搜索：
- 每个分支生成后用评估器打分（可行性/价值/风险/新颖性）
- 低分分支剪枝，高分分支优先展开
- 支持回溯：当前分支执行失败时回到下一个高分分支
- 评估结果缓存，避免对相同分支重复评估
- 蝴蝶效应审批门作为评估器之一：高影响行为降分
- 评估尽量用规则而非 LLM（性能考虑），仅在必要时用 LLM
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM
    from .butterfly_effect import ButterflyEffect

from .prompt.planner_prompts import (
    DECOMPOSE_GOAL_PROMPT, PRIORITIZE_BRANCH_PROMPT,
    GENERATE_ACTIONS_PROMPT, SIMULATE_EXECUTION_PROMPT,
)
from .prompt_utils import resolve_location_name  # [Bug] location code → display name

logger = logging.getLogger("chronoverse.branch_planner")


@dataclass
class Branch:
    """一个行动分支"""
    branch_type: str         # survival / social / career / exploration
    objective: str           # 具体目标描述
    priority: float = 0.5    # 0.0-1.0 优先级
    sub_tasks: list[str] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)  # 生成的行动序列
    completed: bool = False
    score: float = 0.0       # [ToT] 评估器打分 0.0-1.0


@dataclass
class PlanResult:
    """规划结果"""
    selected_branch: Branch | None = None
    all_branches: list[Branch] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    feasible: bool = True
    issues: list[dict] = field(default_factory=list)
    replan_count: int = 0
    # ── [ToT] 搜索过程元数据 ──────────────────────────────
    score: float = 0.0               # 选中分支的评估分数
    attempts: int = 1                # 本次规划尝试的分支数（回溯计数）
    evaluated_branches: list[dict] = field(default_factory=list)  # [{branch_type, objective, score}]
    pruned_branches: list[str] = field(default_factory=list)      # 被剪枝分支的目标描述
    search_mode: str = "tot"         # "tot" / "fallback" 标记是否走了 ToT 路径


class BranchPlanner:
    """分支思维规划器（ToT 化升级版）"""

    def __init__(self, llm: BaseLLM,
                 butterfly_effect: "ButterflyEffect | None" = None,
                 prune_threshold: float = 0.2,
                 max_search_attempts: int = 3):
        """
        Args:
            llm: 底层 LLM 接口
            butterfly_effect: 蝴蝶效应系统（可选，用于评估分支影响）
            prune_threshold: 剪枝阈值，评估分低于此值的分支被剪枝（0.0-1.0）
            max_search_attempts: ToT 搜索最大尝试分支数（回溯上限）
        """
        self.llm = llm
        self.butterfly_effect = butterfly_effect
        self.prune_threshold = prune_threshold
        self.max_search_attempts = max_search_attempts
        self._plan_cache: dict[str, PlanResult] = {}  # agent_id -> last plan
        # [ToT] 评估结果缓存：key = "{agent_id}_{objective}_{day}" -> score
        self._evaluation_cache: dict[str, float] = {}
        self._evaluation_cache_limit: int = 100

    def set_butterfly_effect(self, butterfly_effect: "ButterflyEffect | None"):
        """延迟注入蝴蝶效应系统（用于解决循环依赖：butterfly 在 planner 之后创建）"""
        self.butterfly_effect = butterfly_effect
        logger.debug("BranchPlanner butterfly_effect injected: %s",
                     "yes" if butterfly_effect else "no")

    def plan(self, npc, world_state, max_replans: int = 2) -> PlanResult:
        """
        [ToT] 树式搜索规划流程：分解 → 评估打分 → 剪枝 → 排序 → 生成行动 → 模拟验证 → 回溯

        相比原 BTP，本方法在分支选择阶段引入评估器打分与剪枝，
        并在执行失败时按分数回溯到下一个高分分支。

        Args:
            npc: NPCState 对象
            world_state: WorldState 对象
            max_replans: 单个分支内部的最大重规划次数

        Returns:
            PlanResult，包含 score / attempts / evaluated_branches / pruned_branches
            等搜索元数据；若 ToT 路径异常则回退到原有逻辑（向后兼容）。
        """
        result = PlanResult()

        # Step 1: 目标分解为并行分支
        branches = self.decompose_goal(npc, world_state)
        result.all_branches = branches
        if not branches:
            logger.warning("目标分解失败，使用默认分支")
            branches = self._default_branches(npc)
            result.all_branches = branches

        # Step 2: ToT 评估所有分支并剪枝
        try:
            scored_branches, pruned = self._score_and_prune(branches, npc, world_state)
        except Exception as e:
            # 评估异常时回退到原有 prioritize 逻辑（向后兼容）
            logger.warning("ToT 评估失败，回退到原 BTP 逻辑: %s", e)
            return self._legacy_plan(npc, world_state, branches, max_replans, result)

        result.pruned_branches = pruned
        result.evaluated_branches = [
            {"branch_type": b.branch_type, "objective": b.objective, "score": round(s, 3)}
            for b, s in scored_branches
        ]

        # 全部被剪枝：回退到原逻辑（用 prioritize 选一个）
        if not scored_branches:
            logger.warning("ToT 全部分支被剪枝，回退到原 BTP 逻辑 (npc=%s)", npc.name)
            return self._legacy_plan(npc, world_state, branches, max_replans, result)

        logger.info("ToT 评估完成 npc=%s: 候选%d 剪枝%d 最高分=%.3f",
                    npc.name, len(scored_branches), len(pruned), scored_branches[0][1])

        # Step 3: 按分数依次尝试（带回溯）
        max_attempts = min(self.max_search_attempts, len(scored_branches))
        for attempt in range(max_attempts):
            branch, score = scored_branches[attempt]
            branch.score = score
            result.attempts = attempt + 1
            result.score = score
            result.selected_branch = branch

            try:
                # 生成行动序列
                actions = self.generate_action_sequence(branch, npc, world_state)
                branch.actions = actions
                result.actions = actions

                # 执行前模拟验证 + 单分支内部重规划
                feasible = self._simulate_and_replan(
                    actions, npc, world_state, branch, result, max_replans
                )

                if feasible:
                    result.feasible = True
                    self._plan_cache[npc.agent_id] = result
                    logger.info("ToT 命中分支 npc=%s attempt=%d type=%s score=%.3f",
                                npc.name, attempt + 1, branch.branch_type, score)
                    return result

                # 当前分支不可行，回溯到下一个分支
                logger.info("ToT 回溯 npc=%s: 分支[%s]不可行，尝试下一个",
                            npc.name, branch.branch_type)
            except Exception as e:
                logger.warning("ToT 分支异常 npc=%s attempt=%d type=%s: %s",
                               npc.name, attempt + 1, branch.branch_type, e)
                continue

        # Step 4: 所有候选分支都失败，兜底休息
        logger.warning("ToT 全部分支尝试失败 npc=%s，返回兜底休息计划", npc.name)
        fallback = self._fallback_rest(npc, world_state)
        fallback.evaluated_branches = result.evaluated_branches
        fallback.pruned_branches = result.pruned_branches
        fallback.attempts = max_attempts
        self._plan_cache[npc.agent_id] = fallback
        return fallback

    # ── [ToT] 评估与剪枝 ────────────────────────────────────

    def _score_and_prune(self, branches: list[Branch], npc,
                         world_state) -> tuple[list[tuple[Branch, float]], list[str]]:
        """
        对所有分支打分并剪枝。

        Returns:
            (scored_branches, pruned_objectives)
            scored_branches 按分数降序排列；pruned_objectives 为被剪枝分支的目标描述列表。
        """
        scored: list[tuple[Branch, float]] = []
        pruned: list[str] = []
        for branch in branches:
            try:
                score = self._evaluate_branch(branch, npc, world_state)
            except Exception as e:
                logger.warning("分支评估异常 [%s]: %s，按 0 分剪枝",
                               branch.objective, e)
                score = 0.0

            if score > self.prune_threshold:
                scored.append((branch, score))
            else:
                pruned.append(branch.objective)
                logger.debug("ToT 剪枝 npc=%s: [%s] score=%.3f <= %.2f",
                             npc.name, branch.objective, score, self.prune_threshold)

        # 按分数降序排列
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored, pruned

    def _simulate_and_replan(self, actions: list[dict], npc, world_state,
                              branch: Branch, result: PlanResult,
                              max_replans: int) -> bool:
        """
        对单分支执行模拟验证，必要时在分支内部重规划。

        Returns:
            True 表示最终可行；False 表示该分支放弃（触发回溯）。
        """
        current_actions = actions
        for _ in range(max_replans + 1):
            feasible, issues, adjusted = self.simulate_execution(
                current_actions, npc, world_state)
            result.feasible = feasible
            result.issues = issues
            result.actions = current_actions
            branch.actions = current_actions

            if feasible:
                return True

            # 分支内部重规划
            result.replan_count += 1
            if adjusted:
                current_actions = adjusted
                continue

            # 无法自动修复 → 该分支放弃
            return False
        return False

    def _legacy_plan(self, npc, world_state, branches: list[Branch],
                     max_replans: int, result: PlanResult) -> PlanResult:
        """
        原 BTP 规划逻辑（向后兼容）。
        当 ToT 评估异常或全部分支被剪枝时调用。
        """
        result.search_mode = "fallback"
        selected = self.prioritize(branches, npc, world_state)
        result.selected_branch = selected

        actions = self.generate_action_sequence(selected, npc, world_state)
        selected.actions = actions
        result.actions = actions

        for _ in range(max_replans + 1):
            feasible, issues, adjusted = self.simulate_execution(
                actions, npc, world_state)
            result.feasible = feasible
            result.issues = issues

            if feasible:
                break

            result.replan_count += 1
            if adjusted:
                actions = adjusted
            else:
                remaining = [b for b in branches if b is not selected]
                if remaining:
                    selected = max(remaining, key=lambda b: b.priority)
                    result.selected_branch = selected
                    actions = self.generate_action_sequence(selected, npc, world_state)
                else:
                    actions = [{"type": "rest", "detail": "无可行计划，选择休息", "energy_cost": 0}]
            result.actions = actions
            selected.actions = actions

        self._plan_cache[npc.agent_id] = result
        return result

    def _evaluate_branch(self, branch: Branch, npc, world_state) -> float:
        """
        [ToT] 评估分支质量，返回 0-1 分数。

        评估维度（加权求和）：
        1. 可行性 0.35 — 资源/约束是否满足（规则）
        2. 价值     0.30 — 对 NPC 目标的贡献度（规则为主）
        3. 风险     0.20 — 失败概率/负面影响（风险越低分越高）
        4. 新颖性   0.15 — 避免重复行为

        评估结果按 (agent_id, objective, day) 缓存，避免重复计算。
        """
        # 缓存命中检查
        cache_key = f"{npc.agent_id}_{branch.objective}_{world_state.current_day}"
        if cache_key in self._evaluation_cache:
            return self._evaluation_cache[cache_key]

        score = 0.0

        # 1. 可行性检查（规则）
        feasibility = self._check_feasibility(branch, npc)
        if feasibility < 0.3:
            # 不可行直接剪枝
            self._cache_evaluation(cache_key, 0.0)
            return 0.0
        score += feasibility * 0.35

        # 2. 价值评估（规则 + 可选 LLM）
        value = self._evaluate_value(branch, npc, world_state)
        score += value * 0.30

        # 3. 风险评估（规则）
        risk = self._evaluate_risk(branch, npc, world_state)
        score += (1.0 - risk) * 0.20  # 风险越低分越高

        # 4. 新颖性（避免重复）
        novelty = self._evaluate_novelty(branch, npc)
        score += novelty * 0.15

        # 5. 蝴蝶效应审批门：高影响行为降分
        if self.butterfly_effect:
            try:
                impact = self._butterfly_impact(branch, npc, world_state)
                impact_score = impact.get("impact_score", 0)
                if isinstance(impact_score, (int, float)) and impact_score > 7.0:
                    score *= 0.5  # 高影响行为降分
                    logger.debug("ToT 蝴蝶降分 npc=%s [%s] impact=%.1f",
                                 npc.name, branch.objective, impact_score)
            except Exception as e:
                logger.debug("蝴蝶效应评估跳过 [%s]: %s", branch.objective, e)

        score = min(1.0, max(0.0, score))
        self._cache_evaluation(cache_key, score)
        return score

    def _cache_evaluation(self, cache_key: str, score: float):
        """写入评估缓存，并在超限时清理最旧的一半。"""
        self._evaluation_cache[cache_key] = score
        if len(self._evaluation_cache) > self._evaluation_cache_limit:
            # 清理最旧的一半（dict 保持插入顺序）
            oldest = list(self._evaluation_cache.keys())[: self._evaluation_cache_limit // 2]
            for k in oldest:
                del self._evaluation_cache[k]

    def _butterfly_impact(self, branch: Branch, npc, world_state) -> dict:
        """
        调用蝴蝶效应系统评估分支行动的影响。
        蝴蝶效应系统面向 PlayerState，这里用 npc 的可用字段构造一个轻量代理对象，
        仅取 impact_score 用于降分判断。
        """
        # 蝴蝶效应 evaluate_impact 需要 PlayerState；这里用 SimpleNamespace 适配必要字段
        from types import SimpleNamespace

        proxy = SimpleNamespace(
            name=npc.name,
            social=SimpleNamespace(
                position=getattr(npc, "role", "") or "NPC",
                reputation=getattr(npc, "reputation", 0),
            ),
            location=resolve_location_name(npc.current_location or "未知", world_state),  # [Bug] location code → display name
            tags=list(npc.tags),
            memory=SimpleNamespace(short_term=[]),
        )
        action_desc = f"[{branch.branch_type}] {branch.objective}"
        return self.butterfly_effect.evaluate_impact(proxy, action_desc, world_state)

    def _check_feasibility(self, branch: Branch, npc) -> float:
        """
        [规则] 可行性评估：资源是否足以支撑该分支类型。
        返回 0-1，低于 0.3 视为不可行。
        """
        energy = npc.stats.energy
        max_energy = npc.stats.max_energy or 100
        health = npc.stats.health
        max_health = npc.stats.max_health or 100
        gold = getattr(npc, "gold", 0)

        score = 1.0

        # 生存分支：低体力/低生命时反而最可行（必须做）
        if branch.branch_type == "survival":
            if health < max_health * 0.3 or energy < max_energy * 0.3:
                score = 1.0  # 危急时生存分支最可行
            elif energy < max_energy * 0.15:
                score = 0.4  # 体力极低，行动受限
            else:
                score = 0.8

        # 职业分支：需要体力，金币越少越需要工作
        elif branch.branch_type == "career":
            if energy < max_energy * 0.2:
                score = 0.2  # 体力不足以工作
            elif gold < 20:
                score = 0.9  # 缺钱，工作很可行
            else:
                score = 0.7

        # 社交分支：体力门槛低
        elif branch.branch_type == "social":
            if energy < max_energy * 0.1:
                score = 0.3
            else:
                score = 0.85

        # 探索分支：需要较多体力
        elif branch.branch_type == "exploration":
            if energy < max_energy * 0.25:
                score = 0.25
            else:
                score = 0.7

        return min(1.0, max(0.0, score))

    def _evaluate_value(self, branch: Branch, npc, world_state) -> float:
        """
        [规则] 价值评估：分支对 NPC 当前/长期目标的贡献度。
        返回 0-1。
        """
        score = 0.5  # 基础分
        ai = npc.ai_behavior or {}
        current_goal = (ai.get("current_goal") or "").lower()
        long_term_goal = (ai.get("long_term_goal") or "").lower()
        objective = (branch.objective or "").lower()

        # 目标关键词匹配
        goal_text = f"{current_goal} {long_term_goal}"
        if goal_text.strip():
            type_keywords = {
                "survival": ["生存", "活", "食物", "恢复", "休息", "治疗", "survive"],
                "social": ["社交", "关系", "朋友", "交谈", "social", "friend"],
                "career": ["工作", "赚钱", "职业", "技能", "地位", "work", "career", "gold"],
                "exploration": ["探索", "发现", "冒险", "地点", "explore", "discover"],
            }
            keywords = type_keywords.get(branch.branch_type, [])
            matched = sum(1 for kw in keywords if kw in goal_text or kw in objective)
            score += min(0.3, matched * 0.15)

        # 危急状态下的生存价值加权
        if branch.branch_type == "survival":
            if npc.stats.health < npc.stats.max_health * 0.4:
                score += 0.2
            if npc.stats.energy < npc.stats.max_energy * 0.3:
                score += 0.15

        # 缺钱时职业价值加权
        if branch.branch_type == "career":
            if getattr(npc, "gold", 0) < 20:
                score += 0.2

        # 危机等级高时，生存/探索价值变化
        crisis = getattr(world_state, "crisis_level", 5)
        if crisis >= 7:
            if branch.branch_type == "survival":
                score += 0.15
            elif branch.branch_type == "exploration":
                score -= 0.1  # 危机时探索风险大、价值低

        # 原始 priority 作为先验
        score += branch.priority * 0.1

        return min(1.0, max(0.0, score))

    def _evaluate_risk(self, branch: Branch, npc, world_state) -> float:
        """
        [规则] 风险评估：返回 0-1 的风险值（越高越危险）。
        调用方会用 (1 - risk) 计入分数。
        """
        risk = 0.2  # 基础风险
        energy = npc.stats.energy
        max_energy = npc.stats.max_energy or 100
        health = npc.stats.health
        max_health = npc.stats.max_health or 100

        # 体力越低，非休息分支风险越高
        energy_ratio = energy / max_energy if max_energy else 0
        if branch.branch_type != "survival" and energy_ratio < 0.3:
            risk += 0.3
        elif energy_ratio < 0.5:
            risk += 0.1

        # 生命值低时所有分支风险上升
        health_ratio = health / max_health if max_health else 0
        if health_ratio < 0.3:
            risk += 0.25

        # 探索分支固有风险较高
        if branch.branch_type == "exploration":
            risk += 0.15

        # 危机等级提升风险
        crisis = getattr(world_state, "crisis_level", 5)
        risk += (crisis / 10.0) * 0.15

        # 状态效果中的负面 buff
        negative_keywords = ["受伤", "中毒", "生病", "疲惫", "诅咒", "injured", "poisoned", "sick"]
        status_effects = getattr(npc, "status_effects", []) or []
        for eff in status_effects:
            if any(kw in str(eff).lower() for kw in negative_keywords):
                risk += 0.1
                break

        return min(1.0, max(0.0, risk))

    def _evaluate_novelty(self, branch: Branch, npc) -> float:
        """
        [规则] 新颖性评估：避免 NPC 重复相同行为。
        返回 0-1，越新颖分越高。
        """
        recent = getattr(npc, "recent_actions", []) or []
        if not recent:
            return 1.0  # 无历史，全部新颖

        # 统计近期同类型行为占比
        same_type_count = sum(
            1 for a in recent
            if isinstance(a, dict) and a.get("action") == branch.branch_type
        )
        repeat_ratio = same_type_count / len(recent)

        # 重复率越高，新颖性越低
        novelty = 1.0 - repeat_ratio

        # 目标文本重复检测
        objective_lower = (branch.objective or "").lower()
        if objective_lower and len(objective_lower) > 4:
            obj_repeat = sum(
                1 for a in recent
                if isinstance(a, dict) and objective_lower[:8] in str(a.get("detail", "")).lower()
            )
            novelty -= obj_repeat * 0.15

        return min(1.0, max(0.0, novelty))

    def _fallback_rest(self, npc, world_state) -> PlanResult:
        """[ToT] 所有分支失败时的兜底休息计划。"""
        rest_branch = Branch(
            branch_type="survival",
            objective="体力耗尽，原地休息恢复",
            priority=1.0,
            score=0.0,
        )
        actions = [{"type": "rest", "detail": "所有规划失败，选择休息恢复体力", "energy_cost": 0}]
        return PlanResult(
            selected_branch=rest_branch,
            all_branches=[rest_branch],
            actions=actions,
            feasible=True,
            issues=[],
            replan_count=0,
            score=0.0,
            attempts=0,
            search_mode="fallback",
        )

    def decompose_goal(self, npc, world_state) -> list[Branch]:
        """将NPC的总体目标分解为4个并行分支"""
        try:
            # 构建关系文本
            rel = npc.relation_to_player
            if hasattr(rel, 'favor'):
                relations_text = f"与玩家: {rel.relation_type}(好感{rel.favor})"
            else:
                relations_text = "无"
            # 也包含 NPC 自己的 relations 字典
            if hasattr(npc, 'relations') and npc.relations:
                extra = [f"{k}({v.relation_type if hasattr(v,'relation_type') else v})"
                         for k, v in list(npc.relations.items())[:3]]
                if extra:
                    relations_text += "; " + "; ".join(extra)

            prompt = DECOMPOSE_GOAL_PROMPT.format(
                npc_name=npc.name,
                npc_age=npc.age,
                personality=npc.personality or "普通",
                current_goal=npc.ai_behavior.get("current_goal", "过日子"),
                long_term_goal=npc.ai_behavior.get("long_term_goal", ""),
                location=resolve_location_name(npc.current_location or "未知", world_state),  # [Bug] location code → display name
                health=npc.stats.health,
                max_health=npc.stats.max_health,
                energy=npc.stats.energy,
                max_energy=npc.stats.max_energy,
                gold=getattr(npc, 'gold', 0),
                reputation=getattr(npc, 'reputation', 0),
                tags=", ".join(npc.tags[:5]),
                relations=relations_text,
                day=world_state.current_day,
                time=world_state.current_time,
                season=world_state.season,
                weather=world_state.weather,
                crisis_level=world_state.crisis_level,
            )
            result = self.llm.chat_json(prompt, temperature=0.5, max_tokens=0)
            branches = []
            for b in result.get("branches", []):
                branches.append(Branch(
                    branch_type=b.get("type", "survival"),
                    objective=b.get("objective", ""),
                    priority=float(b.get("priority", 0.5)),
                    sub_tasks=b.get("sub_tasks", []),
                ))
            return branches
        except Exception as e:
            logger.warning("目标分解失败: %s", e)
            return self._default_branches(npc)

    def prioritize(self, branches: list[Branch], npc,
                   world_state) -> Branch:
        """根据当前状态选择最优先分支"""
        # 规则优先：生命/体力危急时强制选生存
        if npc.stats.health < 30 or npc.stats.energy < 20:
            survival = next((b for b in branches if b.branch_type == "survival"), None)
            if survival:
                survival.priority = 1.0
                return survival

        # 使用 LLM 评估
        try:
            branches_text = "\n".join([
                f"- {b.branch_type}: {b.objective} (优先级: {b.priority:.2f}, 子任务: {', '.join(b.sub_tasks[:3])})"
                for b in branches
            ])
            prompt = PRIORITIZE_BRANCH_PROMPT.format(
                health=npc.stats.health,
                max_health=npc.stats.max_health,
                energy=npc.stats.energy,
                max_energy=npc.stats.max_energy,
                gold=getattr(npc, 'gold', 0),
                location=resolve_location_name(npc.current_location or "未知", world_state),  # [Bug] location code → display name
                status_effects=", ".join(npc.status_effects[:3]) or "无",
                branches_text=branches_text,
            )
            result = self.llm.chat_json(prompt, temperature=0.3, max_tokens=0)
            selected_type = result.get("selected_branch", "")
            matched = next((b for b in branches if b.branch_type == selected_type), None)
            if matched:
                return matched
        except Exception as e:
            logger.warning("Branch prioritization LLM failed, using fallback: %s", e)

        # 回退：选优先级最高的
        return max(branches, key=lambda b: b.priority)

    def generate_action_sequence(self, branch: Branch, npc,
                                  world_state) -> list[dict]:
        """将分支目标转化为具体行动序列"""
        try:
            prompt = GENERATE_ACTIONS_PROMPT.format(
                branch_type=branch.branch_type,
                branch_objective=branch.objective,
                sub_tasks=", ".join(branch.sub_tasks[:4]),
                npc_name=npc.name,
                npc_age=npc.age,
                personality=npc.personality or "普通",
                strength=npc.stats.strength,
                agility=npc.stats.agility,
                intelligence=npc.stats.intelligence,
                location=resolve_location_name(npc.current_location or "未知", world_state),  # [Bug] location code → display name
            )
            result = self.llm.chat_json(prompt, temperature=0.6, max_tokens=0)
            return result.get("actions", [{"type": "rest", "detail": "无行动", "energy_cost": 0}])
        except Exception as e:
            logger.warning("行动序列生成失败: %s", e)
            return [{"type": branch.branch_type, "detail": branch.objective, "energy_cost": 10}]

    def simulate_execution(self, actions: list[dict], npc,
                            world_state) -> tuple[bool, list[dict], list[dict]]:
        """
        执行前模拟，检测约束违反。
        
        Returns:
            (feasible, issues, adjusted_actions)
        """
        # 快速规则检查（不需要 LLM）
        issues = []
        simulated_energy = npc.stats.energy
        simulated_health = npc.stats.health
        simulated_gold = getattr(npc, 'gold', 0)

        for i, action in enumerate(actions):
            energy_cost = action.get("energy_cost", 10)
            if simulated_energy - energy_cost < 0:
                issues.append({
                    "action_index": i,
                    "issue": f"体力不足（当前{simulated_energy}，需要{energy_cost}）",
                    "severity": "critical",
                    "fix": "先休息恢复体力",
                })
                break
            simulated_energy -= energy_cost

            if action.get("type") == "trade" and action.get("gold_cost", 0) > simulated_gold:
                issues.append({
                    "action_index": i,
                    "issue": f"金币不足（当前{simulated_gold}）",
                    "severity": "warning",
                    "fix": "跳过此交易或选择更便宜的选项",
                })

        critical = [iss for iss in issues if iss["severity"] == "critical"]
        if not critical:
            return True, issues, []

        # 有严重问题，尝试用 LLM 修复
        try:
            prompt = SIMULATE_EXECUTION_PROMPT.format(
                health=npc.stats.health,
                max_health=npc.stats.max_health,
                energy=npc.stats.energy,
                max_energy=npc.stats.max_energy,
                gold=getattr(npc, 'gold', 0),
                location=resolve_location_name(npc.current_location or "未知", world_state),  # [Bug] location code → display name
                inventory=", ".join([f"{i.name}x{i.quantity}" for i in getattr(npc, 'inventory', [])]) or "空",
                actions_text="\n".join([f"{i}. {a}" for i, a in enumerate(actions)]),
            )
            result = self.llm.chat_json(prompt, temperature=0.3, max_tokens=0)
            adjusted = result.get("adjusted_actions", [])
            return result.get("feasible", False), result.get("issues", issues), adjusted
        except Exception as e:
            logger.warning("Execution simulation failed: %s", e)
            return False, issues, []

    def replan_from_failure(self, failed_action: dict, npc,
                             world_state) -> list[dict]:
        """从失败的行动中恢复，生成替代行动"""
        # 简化重规划：选择休息或低消耗行动
        fallback = [
            {"type": "rest", "detail": "因行动失败而休息恢复", "energy_cost": 0},
            {"type": "social", "detail": "与附近的人交谈获取信息", "energy_cost": 5},
        ]
        # 根据失败原因调整
        if "体力" in str(failed_action):
            return [{"type": "rest", "detail": "体力不足，必须休息", "energy_cost": 0}]
        if "金币" in str(failed_action):
            return [{"type": "work", "detail": "先赚钱再说", "energy_cost": 15}]
        return fallback

    def _default_branches(self, npc) -> list[Branch]:
        """默认分支（当LLM分解失败时使用）"""
        return [
            Branch("survival", "维持生存", 0.8, ["检查身体状况", "获取食物"]),
            Branch("social", "日常社交", 0.4, ["与附近的人交谈"]),
            Branch("career", "日常工作", 0.5, ["完成日常工作任务"]),
            Branch("exploration", "了解环境", 0.3, ["观察周围环境"]),
        ]

    def get_cached_plan(self, agent_id: str) -> PlanResult | None:
        """获取缓存的最近一次规划结果"""
        return self._plan_cache.get(agent_id)
