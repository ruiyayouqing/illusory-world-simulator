from __future__ import annotations
import random
import uuid
import logging
from .schemas import WorldState, PlayerState, MacroEvent
from .llm.base_llm import BaseLLM
from .prompt_utils import resolve_location_name, sanitize_player_input  # [Bug] location code → display name | [v1.4 P2-10] Prompt injection 防护

logger = logging.getLogger("chronoverse.butterfly")


class ButterflyEffect:
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.player_actions_history: list[dict] = []
        self.world_impact_score: float = 0.0
        # [v10] 审批门机制
        self.approval_gate_enabled: bool = False  # 是否启用审批门
        self.approval_threshold: float = 7.0      # 触发审批的影响分数阈值
        self.pending_approvals: list[dict] = []    # 待审批的后果
        self.approval_history: list[dict] = []     # 审批历史
        # [NovelRoleplay] 小说扮演偏离度追踪
        # 当偏离度超过 divergence_threshold 时，既定未来事件自动失效（剧情进入自由推演）
        self.novel_divergence_score: float = 0.0
        self.divergence_threshold: float = 30.0  # 偏离度阈值（累积）
        self.divergence_actions: list[dict] = []  # 记录导致偏离的具体行为
        self.invalidated_future_events: list[str] = []  # 已失效的既定未来事件 ID
        self._novel_mode: bool = False  # 是否处于小说扮演模式

    def record_action(self, player: PlayerState, action: str,
                      result: str, day: int):
        entry = {
            "day": day,
            "action": action,
            "result": result[:200],
            "location": player.location,
            "tags": list(player.tags),
        }
        self.player_actions_history.append(entry)
        if len(self.player_actions_history) > 100:
            self.player_actions_history = self.player_actions_history[-100:]

    # ── [NovelRoleplay] 小说扮演偏离度追踪 ──────────────────────

    def enable_novel_mode(self, threshold: float = 30.0):
        """[NovelRoleplay] 启用小说扮演偏离度追踪。
        在 enter_roleplay 时调用，开启后玩家行为会累积偏离度。"""
        self._novel_mode = True
        self.divergence_threshold = threshold
        self.novel_divergence_score = 0.0
        self.divergence_actions = []
        self.invalidated_future_events = []
        logger.info("[NovelRoleplay] 偏离度追踪已启用，阈值=%.1f", threshold)

    def disable_novel_mode(self):
        """退出小说扮演模式"""
        self._novel_mode = False

    def record_novel_divergence(self, action: str, divergence_score: float,
                                  reason: str, day: int,
                                  affected_future_events: list[str] = None):
        """[NovelRoleplay] 记录一次对原著剧情的偏离。
        玩家行为若改变了原著既定走向，调用此方法累积偏离度。
        当累积偏离度超过阈值时，既定未来事件自动失效。

        参数：
            action: 玩家行为描述
            divergence_score: 本次偏离分数（0-10，越大越偏离）
            reason: 偏离原因
            day: 游戏天数
            affected_future_events: 直接受影响的既定未来事件 time_id 列表
        """
        if not self._novel_mode:
            return
        self.novel_divergence_score += divergence_score
        record = {
            "day": day,
            "action": action[:100],
            "divergence_score": divergence_score,
            "reason": reason[:200],
            "affected_future_events": affected_future_events or [],
            "cumulative_score": self.novel_divergence_score,
        }
        self.divergence_actions.append(record)
        if len(self.divergence_actions) > 50:
            self.divergence_actions = self.divergence_actions[-50:]

        # 直接受影响的事件立即失效
        for ev_id in (affected_future_events or []):
            if ev_id not in self.invalidated_future_events:
                self.invalidated_future_events.append(ev_id)

        logger.info("[NovelRoleplay] 偏离度 +%s (累积 %.1f/%.1f): %s",
                    divergence_score, self.novel_divergence_score,
                    self.divergence_threshold, reason[:50])

        # 超过阈值时，标记全部既定未来失效
        if self.novel_divergence_score >= self.divergence_threshold:
            self._invalidate_all_future_events()

    def _invalidate_all_future_events(self):
        """[NovelRoleplay] 偏离度超阈值，既定未来全部失效，剧情进入自由推演。
        此方法只设置标记，实际的伏笔解决由调用方根据 invalidated_future_events 列表处理。"""
        if not self._novel_mode:
            return
        logger.warning(
            "[NovelRoleplay] 偏离度 %.1f 超过阈值 %.1f，既定未来全部失效！"
            "剧情进入自由推演模式。",
            self.novel_divergence_score, self.divergence_threshold
        )
        # 标记：后续检查到此标志时，所有 novel_future 伏笔都应被解决/失效
        # 实际的伏笔失效由 turn_processor 调用 check_and_invalidate_future_foreshadows 完成

    def check_and_invalidate_future_foreshadows(self, foreshadow_lifecycle,
                                                  current_day: int) -> list[str]:
        """[NovelRoleplay] 检查并失效已超阈值的既定未来伏笔。
        在 turn_processor 每回合调用。

        返回：本次失效的伏笔 hook_id 列表
        """
        if not self._novel_mode or not foreshadow_lifecycle:
            return []

        invalidated = []
        # 如果偏离度超阈值，失效所有 novel_future 伏笔
        if self.novel_divergence_score >= self.divergence_threshold:
            for hook_id, hook in list(foreshadow_lifecycle.hooks.items()):
                if (hook.status in ("active", "mentioned")
                    and "novel_future" in (hook.tags or [])):
                    try:
                        foreshadow_lifecycle.resolve(
                            hook_id, current_day,
                            resolution=f"偏离度超阈值({self.novel_divergence_score:.1f})，既定未来失效"
                        )
                        invalidated.append(hook_id)
                    except Exception as e:
                        logger.warning("失效伏笔 %s 失败: %s", hook_id, e)

        # 失效直接被影响的伏笔（通过 time_id 标签匹配）
        for ev_id in self.invalidated_future_events:
            tag_to_match = f"timeline_{ev_id}"
            for hook_id, hook in list(foreshadow_lifecycle.hooks.items()):
                if (hook.status in ("active", "mentioned")
                    and tag_to_match in (hook.tags or [])
                    and hook_id not in invalidated):
                    try:
                        foreshadow_lifecycle.resolve(
                            hook_id, current_day,
                            resolution=f"玩家行为直接影响，既定事件 {ev_id} 失效"
                        )
                        invalidated.append(hook_id)
                    except Exception as e:
                        logger.warning("失效伏笔 %s 失败: %s", hook_id, e)

        if invalidated:
            logger.info("[NovelRoleplay] 本回合失效 %d 条既定未来伏笔", len(invalidated))
        return invalidated

    def get_divergence_summary(self) -> dict:
        """[NovelRoleplay] 获取偏离度摘要"""
        return {
            "novel_mode": self._novel_mode,
            "divergence_score": round(self.novel_divergence_score, 1),
            "threshold": self.divergence_threshold,
            "exceeded": self.novel_divergence_score >= self.divergence_threshold,
            "divergence_count": len(self.divergence_actions),
            "invalidated_events_count": len(self.invalidated_future_events),
            "recent_divergences": self.divergence_actions[-5:],
        }

    def evaluate_impact(self, player: PlayerState, action: str,
                        world_state: WorldState) -> dict:
        recent = self.player_actions_history[-10:] if self.player_actions_history else []
        history_text = "\n".join([
            f"第{h['day']}天: {h['action'][:80]}" for h in recent
        ]) or "无历史记录"

        world_desc = getattr(world_state, 'description', '') or ""
        factions_text = ", ".join([
            f"{k}(实力{v.power})" for k, v in world_state.factions.items()
        ]) if world_state.factions else "无势力信息"
        # [Bug] 使用 display name 而非 location code
        locations_text = ", ".join(resolve_location_name(k, world_state) for k in world_state.locations.keys()) if world_state.locations else "无地点信息"
        memory_text = "; ".join(player.memory.short_term[-5:]) if player.memory and player.memory.short_term else "无记忆"

        # [v1.4 P2-10] Prompt injection 防护
        safe_action = sanitize_player_input(action)
        prompt = f"""评估玩家行为对世界的蝴蝶效应影响。

【玩家行为】
{safe_action}

【玩家信息】
姓名: {player.name}, 身份: {player.social.position}
位置: {resolve_location_name(player.location, world_state)}  # [Bug] location code → display name
标签: {', '.join(player.tags)}
声望: {player.social.reputation}
记忆: {memory_text}

【世界信息】
名称: {world_state.world_name}
背景: {world_desc[:300]}
势力: {factions_text}
地点: {locations_text}
日期: 第{world_state.current_day}天
季节: {world_state.season}
天气: {world_state.weather}
危机等级: {world_state.crisis_level}/10
活跃事件: {len(world_state.active_events)}个

【近期行为历史】
{history_text}

【重要】请基于上面的世界信息准确描述影响，不要编造不存在的势力或地点。

【输出JSON格式】
{{
    "impact_score": 0到10的浮点数,
    "impact_type": "personal/local/regional/global",
    "description": "影响描述",
    "potential_consequences": ["可能的后果1", "可能的后果2"],
    "world_change": "对世界的潜在改变"
}}

只输出JSON。"""
        try:
            # [v10] 优先使用结构化输出（蝴蝶效应 schema），失败回退到 chat_json
            if hasattr(self.llm, "chat_structured"):
                result = self.llm.chat_structured(prompt, "butterfly", temperature=0.6)
            else:
                result = self.llm.chat_json(prompt, temperature=0.6)
        except Exception as e:
            logger.warning("Butterfly evaluate_impact failed: %s", e)
            return {"impact_score": 0, "description": "影响评估失败", "consequences": []}

        # 累加世界影响分数（[v9] 限制上限防止无限增长）
        score = result.get("impact_score", 0)
        if isinstance(score, (int, float)):
            self.world_impact_score = min(10000, self.world_impact_score + score)

        return result

    def generate_consequence(self, impact: dict, world_state: WorldState) -> MacroEvent | None:
        if impact.get("impact_score", 0) < 5:
            return None

        world_desc = getattr(world_state, 'description', '') or ""
        factions_text = ", ".join([
            f"{k}(实力{v.power})" for k, v in world_state.factions.items()
        ]) if world_state.factions else "无"
        # [Bug] 使用 display name 而非 location code
        locations_text = ", ".join(resolve_location_name(k, world_state) for k in world_state.locations.keys()) if world_state.locations else "无"

        consequence_prompt = f"""根据蝴蝶效应评估，生成一个世界事件。

【影响评估】
类型: {impact.get('impact_type', 'personal')}
分数: {impact.get('impact_score', 0)}
描述: {impact.get('description', '')}
潜在后果: {', '.join(impact.get('potential_consequences', []))}

【世界信息】
名称: {world_state.world_name}
背景: {world_desc[:300]}
势力: {factions_text}
地点: {locations_text}
日期: 第{world_state.current_day}天
季节: {world_state.season}
危机等级: {world_state.crisis_level}/10

【重要】事件描述必须基于上面的世界信息，使用正确的势力名称和地点名称，不要编造不存在的内容。

【输出JSON格式】
{{
    "event_id": "evt_butterfly_{world_state.current_day}",
    "event_type": "social/politics/economic",
    "description": "500-1000字的详细事件描述，描写事件的起因、经过和影响",
    "affected_locations": ["地点代码"],
    "impact_level": 1到10
}}

只输出JSON。"""
        try:
            # [v10] 优先使用结构化输出（蝴蝶效应 schema），失败回退到 chat_json
            if hasattr(self.llm, "chat_structured"):
                response = self.llm.chat_structured(consequence_prompt, "butterfly", temperature=0.8)
            else:
                response = self.llm.chat_json(consequence_prompt, temperature=0.8)
        except Exception as e:
            logger.warning("Butterfly generate_consequence failed: %s", e)
            return None

        if "description" in response:
            return MacroEvent(
                event_id=response.get("event_id", f"evt_bf_{world_state.current_day}"),
                event_type=response.get("event_type", "social"),
                description=response["description"],
                affected_locations=response.get("affected_locations", []),
                impact_level=response.get("impact_level", 5),
                start_day=world_state.current_day,
            )
        return None

    def get_world_memory(self) -> str:
        if not self.player_actions_history:
            return "这个世界还没有因为你的行为而改变。"
        significant = [h for h in self.player_actions_history if len(h.get("result", "")) > 20]
        if not significant:
            return "你的行为尚未在世界上留下深刻印记。"
        recent = significant[-5:]
        lines = [f"第{h['day']}天: {h['action'][:30]}..." for h in recent]
        return "你的行为在世界上留下的痕迹:\n" + "\n".join(lines)

    def get_impact_summary(self) -> dict:
        return {
            "total_actions": len(self.player_actions_history),
            "world_impact_score": round(self.world_impact_score, 1),
            "recent_actions": [
                {"day": h["day"], "action": h["action"][:50]}
                for h in self.player_actions_history[-5:]
            ],
        }

    # ── [v10] 蝴蝶效应审批门 ──────────────────────────────

    def evaluate_with_approval(self, player: PlayerState, action: str,
                                world_state: WorldState,
                                narrative: str = "") -> dict:
        """
        [v10] 带审批门的影响评估。

        当影响分数 >= approval_threshold 时，不立即执行后果，
        而是生成预览等待玩家审批。
        """
        impact = self.evaluate_impact(player, action, world_state)
        score = impact.get("impact_score", 0)

        # 记录行为
        self.record_action(player, action, narrative, world_state.current_day)

        # 低影响：直接执行
        if score < self.approval_threshold or not self.approval_gate_enabled:
            consequence = self.generate_consequence(impact, world_state)
            return {
                "impact": impact,
                "consequence": consequence.model_dump() if consequence else None,
                "auto_executed": True,
                "needs_approval": False,
            }

        # 高影响：生成预览，等待审批
        consequence_preview = self._generate_consequence_preview(impact, world_state)

        approval_id = f"approval_{uuid.uuid4().hex[:8]}"
        pending = {
            "approval_id": approval_id,
            "player_action": action[:200],
            "impact": impact,
            "consequence_preview": consequence_preview,
            "status": "pending",
            "turn": len(self.player_actions_history),
            "day": world_state.current_day,
        }
        self.pending_approvals.append(pending)

        logger.info("Butterfly effect approval gate triggered: score=%.1f, id=%s",
                     score, approval_id)

        return {
            "impact": impact,
            "consequence": None,
            "auto_executed": False,
            "needs_approval": True,
            "approval_id": approval_id,
            "preview": consequence_preview,
        }

    def approve_consequence(self, approval_id: str,
                            decision: str = "approve") -> dict:
        """
        [v10] 审批蝴蝶效应后果。

        Args:
            approval_id: 审批ID
            decision: "approve" / "reject" / "modify"

        Returns:
            审批结果
        """
        for pending in self.pending_approvals:
            if pending["approval_id"] == approval_id:
                if decision == "modify":
                    # [Bug] modify 功能尚未完全实现（无独立"提交修改"端点），
                    # 按 reject 处理避免流程卡死，并给出明确提示
                    pending["status"] = "rejected"
                    self.pending_approvals.remove(pending)
                    self.approval_history.append(dict(pending))
                    return {
                        "approved": False,
                        "message": "修改功能暂未完全实现，本次按拒绝处理。世界线保持不变。",
                    }

                pending["status"] = decision
                self.pending_approvals.remove(pending)
                self.approval_history.append(dict(pending))

                if decision == "approve":
                    # 执行后果
                    impact = pending["impact"]
                    # 这里返回 impact，由调用方执行 generate_consequence
                    return {
                        "approved": True,
                        "impact": impact,
                        "message": "后果已批准，将在下一回合生效。",
                    }
                elif decision == "reject":
                    return {
                        "approved": False,
                        "message": "后果已拒绝。世界线保持不变。",
                    }
                else:
                    return {
                        "approved": False,
                        "message": "未知的审批决定。",
                    }

        return {"error": "未找到该审批记录", "approval_id": approval_id}

    def get_pending_approvals(self) -> list[dict]:
        """获取所有待审批的蝴蝶效应"""
        return [p for p in self.pending_approvals if p["status"] == "pending"]

    def _generate_consequence_preview(self, impact: dict,
                                       world_state: WorldState) -> dict:
        """[v10+] 生成后果预览 — 使用 LLM 生成详细的后果描述"""
        score = impact.get("impact_score", 0)
        consequences = impact.get("potential_consequences", [])
        action_desc = impact.get("description", "")

        severity = "温和" if score < 8 else "重大" if score < 9 else "灾难性"

        # 基础预览（无 LLM）
        preview = {
            "severity": severity,
            "impact_score": score,
            "impact_type": impact.get("impact_type", "unknown"),
            "description": action_desc,
            "potential_consequences": consequences,
            "warning": f"这是一个{severity}级别的世界变化，可能永久改变世界格局。",
            "narrative_preview": "",
        }

        # 使用 LLM 生成详细的后果叙述预览
        try:
            consequence_text = "\n".join(f"- {c}" for c in consequences[:5])
            world_info = ""
            if world_state:
                crisis = getattr(world_state, 'crisis_level', 5)
                stability = "稳定" if crisis < 5 else "动荡" if crisis < 8 else "混乱"
                world_info = f"当前世界状态：{stability}，危机等级{crisis}/10"

            prompt = f"""你是一个世界推演引擎的后果预览器。玩家即将执行一个高影响力行为，请用2-3句话描述如果批准这个行为，世界将发生什么变化。

【玩家行为】
{action_desc[:200]}

【影响评估】
影响分数: {score}/10
影响类型: {impact.get('impact_type', 'unknown')}
严重程度: {severity}

【潜在后果】
{consequence_text}

{world_info}

【要求】
- 用第二人称（"你将看到……"、"这个世界将……"）
- 语气严肃但不夸张
- 给出具体的画面感
- 2-3句话，不超过150字

只输出预览文本，不要JSON。"""

            narrative = self.llm.chat(prompt, temperature=0.7, max_tokens=512)
            preview["narrative_preview"] = narrative.strip()
        except Exception as e:
            logger.warning("Consequence preview LLM failed: %s", e)
            preview["narrative_preview"] = ""

        return preview

    def to_dict(self) -> dict:
        # 序列化蝴蝶效应状态（含审批门字段，统一由子系统管理）
        return {
            "actions": self.player_actions_history,
            "impact_score": self.world_impact_score,
            "approval_gate_enabled": self.approval_gate_enabled,
            "approval_threshold": self.approval_threshold,
            "pending_approvals": self.pending_approvals,
            "approval_history": self.approval_history[-20:],
            # [NovelRoleplay] 小说扮演偏离度追踪
            "novel_divergence_score": self.novel_divergence_score,
            "divergence_threshold": self.divergence_threshold,
            "divergence_actions": self.divergence_actions[-20:],
            "invalidated_future_events": self.invalidated_future_events,
            "novel_mode": self._novel_mode,
        }

    def from_dict(self, data: dict):
        self.player_actions_history = data.get("actions", [])
        self.world_impact_score = data.get("impact_score", 0)
        self.approval_gate_enabled = data.get("approval_gate_enabled", False)
        self.approval_threshold = data.get("approval_threshold", 7.0)
        self.pending_approvals = data.get("pending_approvals", [])
        self.approval_history = data.get("approval_history", [])
        # [NovelRoleplay] 恢复小说扮演偏离度追踪
        self.novel_divergence_score = data.get("novel_divergence_score", 0.0)
        self.divergence_threshold = data.get("divergence_threshold", 30.0)
        self.divergence_actions = data.get("divergence_actions", [])
        self.invalidated_future_events = data.get("invalidated_future_events", [])
        self._novel_mode = data.get("novel_mode", False)
