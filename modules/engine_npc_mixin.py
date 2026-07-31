"""
[v1.4 P1-5] NpcFacadeMixin — NPC 交互/印象/关系 Facade

把 GameEngine 中 NPC 相关方法集中到这里，降低主类体积。
GameEngine 通过 Mixin 继承获得这些方法，对外接口完全兼容。
"""
from __future__ import annotations
import logging

from .prompt_utils import resolve_location_name, sanitize_player_input  # [v1.4 P2-10] Prompt injection 防护

logger = logging.getLogger("chronoverse.engine")


class NpcFacadeMixin:
    """[v1.4] NPC 交互 Facade Mixin

    依赖宿主类的以下属性/方法：
      - self.llm
      - self.player_state
      - self.npc_states
      - self.world_state
      - self.world_def
      - self.npc_life_evolution
      - self.npc_reflection
      - self.npc_registry
      - self.meta
      - self._maybe_generate_private_facts
    """

    def npc_chat(self, npc_id: str, player_message: str, chat_history: list = None, stream_callback=None) -> dict:
        """
        NPC 聊天接口：让玩家以"上帝视角"与任意 NPC 对话。
        不影响游戏回合、状态或剧情，仅会话内保存聊天记录。

        参数：
            npc_id: NPC 的 agent_id
            player_message: 玩家的消息
            chat_history: 之前的聊天历史，格式为 [{"role": "user/assistant", "content": "..."}]
            stream_callback: 可选，流式回调函数，每收到一个 token 就调用一次

        返回：
            {"success": bool, "message": str, "error": str}
        """
        if not self.llm:
            return {"success": False, "message": "", "error": "LLM 未初始化"}

        chat_history = chat_history or []

        if npc_id == "player":
            npc_name = self.player_state.name if self.player_state else "主角"
            npc_personality = ""
            npc_role = ""
            npc_background = ""
            npc_recent_actions = []
            npc_relation = "玩家自己"
            npc_location = resolve_location_name(self.player_state.location, self.world_state) if self.player_state else ""
        else:
            npc_state = self.npc_states.get(npc_id) if self.npc_states else None
            if not npc_state:
                return {"success": False, "message": "", "error": f"NPC 不存在: {npc_id}"}

            # [v1.3] 私密档案懒加载：普通模式下首次对话时触发
            # （小说模式下 dormant NPC 登场时已生成）
            if not getattr(npc_state, 'private_facts_generated', False):
                self._maybe_generate_private_facts(npc_state)

            npc_name = npc_state.name
            npc_personality = npc_state.personality
            npc_role = npc_state.role
            npc_background = "\n".join([rh.get("description", "") for rh in npc_state.role_history[:3]])
            npc_recent_actions = npc_state.recent_actions[-3:]
            npc_relation = npc_state.relation_to_player.description if npc_state.relation_to_player else "陌生人"
            npc_location = resolve_location_name(npc_state.current_location, self.world_state)

        world_name = self.world_def.get("world_name", "") if self.world_def else ""
        world_type = self.world_state.world_type if self.world_state else "custom"

        recent_actions_str = ""
        if npc_recent_actions:
            recent_actions_str = "\n".join([f"- {a.get('action', '')}" for a in npc_recent_actions])

        system_prompt = f"""
你是角色扮演游戏中的 NPC「{npc_name}」。请以这个角色的身份与玩家对话。

【世界信息】
世界名称：{world_name}
世界类型：{world_type}

【你的身份】
姓名：{npc_name}
身份：{npc_role}
性格：{npc_personality if npc_personality else "温和友善"}
与玩家关系：{npc_relation}
当前位置：{npc_location}

【你的经历】
{npc_background if npc_background else "暂无特殊经历"}

【最近行动】
{recent_actions_str if recent_actions_str else "暂无记录"}

【核心规则】
1. 你只知道自己的经历和世界设定，不知道其他 NPC 的秘密
2. 你不能回答超出你角色知识范围的问题
3. 如果玩家问你不知道的事情，如实回答"我不知道"或"这我不清楚"
4. 不要打破第四面墙，不要提及你是 AI
5. 你的回答要符合你的性格设定
6. 回答要自然、简短，像日常对话一样，不要长篇大论
7. 如果玩家是在与主角聊天（npc_id=player），你就是主角本人，用第一人称回答

【示例】
玩家：你最近在忙什么？
你：最近一直在修炼剑法，希望能早日突破瓶颈。
"""

        messages = [{"role": "system", "content": system_prompt}]
        for msg in chat_history[-10:]:
            messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        # [v1.4 P2-10] Prompt injection 防护：玩家消息 sanitize
        safe_msg = sanitize_player_input(player_message)
        messages.append({"role": "user", "content": safe_msg})

        try:
            if stream_callback and hasattr(self.llm, "chat_stream"):
                prompt_parts = []
                for m in messages:
                    role_name = m["role"]
                    if role_name == "system":
                        role_name = "系统"
                    elif role_name == "user":
                        role_name = "玩家"
                    elif role_name == "assistant":
                        role_name = npc_name
                    prompt_parts.append(f"【{role_name}】\n{m['content']}")
                full_prompt = "\n\n".join(prompt_parts) + f"\n\n【{npc_name}】\n"

                full_message = ""
                token_gen = self.llm.chat_stream(full_prompt, temperature=0.7, max_tokens=500)
                for token in token_gen:
                    if token:
                        full_message += token
                        try:
                            stream_callback(token)
                        except Exception:
                            pass
                return {"success": True, "message": full_message, "error": ""}
            else:
                prompt_parts = []
                for m in messages:
                    role_name = m["role"]
                    if role_name == "system":
                        role_name = "系统"
                    elif role_name == "user":
                        role_name = "玩家"
                    elif role_name == "assistant":
                        role_name = npc_name
                    prompt_parts.append(f"【{role_name}】\n{m['content']}")
                full_prompt = "\n\n".join(prompt_parts) + f"\n\n【{npc_name}】\n"
                result = self.llm.chat(full_prompt, temperature=0.7, max_tokens=500)
                return {"success": True, "message": result or "", "error": ""}
        except Exception as e:
            logger.error("NPC chat failed: %s", e)
            return {"success": False, "message": "", "error": str(e)}

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
        # [v1.4 P2-10] Prompt injection 防护：玩家输入 sanitize（仅截断+控制字符过滤，避免双重 fence 干扰 JSON 解析）
        safe_input = sanitize_player_input(player_input, max_len=200)
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
玩家行为：{safe_input}
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
                from .schemas import RelationEntry
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

    def get_npc_evolution_summary(self, npc_id: str) -> list[dict]:
        """获取某个NPC的完整演化历史"""
        if self.npc_life_evolution:
            return self.npc_life_evolution.get_evolution_summary(npc_id)
        return []
