"""多智能体分工叙事（Agents' Room 式）：情节/角色/对白分岗协作。

参考 Agents' Room（ICLR 2025）的多智能体分工协作叙事思路：
  - 情节架构师（PlotArchitect）：负责情节大纲与节奏
  - 角色一致性审查员（CharacterReviewer）：检查角色行为是否符合设定
  - 对白撰写师（DialogueWriter）：基于大纲和角色备注生成完整叙事

设计原则：
  - 仅用于关键剧情（消耗 3-4 倍 LLM 调用），普通回合走单 LLM 路径
  - 任意环节失败均返回空草稿，由调用方回退到单 LLM 生成
  - 修订次数受限（_max_revisions），避免 LLM 调用爆炸
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("chronoverse.multi_agent_narrative")


@dataclass
class NarrativeDraft:
    """叙事草稿。"""
    plot_outline: str = ""  # 情节大纲
    character_notes: str = ""  # 角色一致性备注
    dialogue: str = ""  # 对白草稿
    final_narrative: str = ""  # 最终融合叙事
    issues_found: list[str] = field(default_factory=list)  # 审查发现的问题
    revisions: int = 0  # 修订次数


class PlotArchitect:
    """情节架构师：负责情节大纲和节奏。"""

    def __init__(self, llm=None):
        self.llm = llm

    def generate_outline(self, context: str, player_input: str, scene_type: str = "") -> str:
        """生成情节大纲。"""
        if not self.llm:
            return ""

        prompt = f"""你是情节架构师。请基于以下上下文和玩家输入，生成一段叙事的情节大纲。

【上下文】
{context}

【玩家输入】
{player_input}

【场景类型】
{scene_type or "未指定"}

【要求】
1. 大纲应包含：开场、发展、转折（如有）、结尾
2. 控制节奏：500-1000字叙事的节奏
3. 确保情节推进有意义，不是流水账
4. 考虑伏笔的插入或回收

只输出情节大纲，不要写完整叙事："""

        try:
            return self.llm.chat(prompt, temperature=0.7, max_tokens=1024) or ""
        except Exception as e:
            logger.warning("PlotArchitect failed: %s", e)
            return ""


class CharacterReviewer:
    """角色一致性审查员：检查角色行为是否符合设定。"""

    def __init__(self, llm=None):
        self.llm = llm

    def review(self, outline: str, character_info: str, character_state: str = "") -> tuple[str, list[str]]:
        """
        审查情节大纲中的角色一致性。
        返回（审查后的角色备注, 发现的问题列表）
        """
        if not self.llm:
            return "", []

        prompt = f"""你是角色一致性审查员。请检查以下情节大纲中角色行为是否符合设定。

【角色设定】
{character_info}

【角色当前状态】
{character_state or "无"}

【情节大纲】
{outline}

【检查要点】
1. 角色性格是否一致（不会突然变性格）
2. 角色能力是否合理（不会做超出能力的事）
3. 角色关系是否正确（好感度/敌对关系）
4. 角色知识边界（不该知道的信息不泄露）
5. 已死角色不能出现

返回 JSON：
{{
    "character_notes": "角色表演备注（如语气、小动作、情绪状态）",
    "issues": ["发现的问题1", "发现的问题2"]
}}"""

        try:
            if hasattr(self.llm, "chat_structured"):
                result = self.llm.chat_structured(prompt, "narrative", temperature=0.3)
            else:
                result = self.llm.chat_json(prompt, temperature=0.3)

            if result and "error" not in result:
                return result.get("character_notes", ""), result.get("issues", [])
        except Exception as e:
            logger.warning("CharacterReviewer failed: %s", e)

        return "", []


class DialogueWriter:
    """对白撰写师：基于大纲和角色备注生成完整叙事。"""

    def __init__(self, llm=None):
        self.llm = llm

    def write_narrative(self, outline: str, character_notes: str, context: str,
                        player_input: str, style: str = "") -> str:
        """基于大纲和角色备注撰写完整叙事。"""
        if not self.llm:
            return ""

        prompt = f"""你是叙事撰写师。请基于情节大纲和角色备注，撰写完整的叙事文本。

【上下文】
{context}

【玩家输入】
{player_input}

【情节大纲】
{outline}

【角色表演备注】
{character_notes or "无特殊备注"}

【叙事风格】
{style or "默认"}

【要求】
1. 500-1000字
2. 对白自然，符合角色性格
3. 描写生动，有画面感
4. 严格遵循大纲的情节走向
5. 融入角色备注中的表演细节

直接输出叙事文本，不要加任何前缀："""

        try:
            return self.llm.chat(prompt, temperature=0.8, max_tokens=1024) or ""
        except Exception as e:
            logger.warning("DialogueWriter failed: %s", e)
            return ""


class MultiAgentNarrativeEngine:
    """
    多智能体叙事引擎。
    协调 PlotArchitect → CharacterReviewer → DialogueWriter 的流水线。
    """

    def __init__(self, llm=None):
        self.llm = llm
        self.architect = PlotArchitect(llm)
        self.reviewer = CharacterReviewer(llm)
        self.writer = DialogueWriter(llm)
        self._max_revisions: int = 1  # 最大修订次数（控制 LLM 调用）
        self._enabled: bool = True
        # 统计：累计调用次数与成功次数，便于面板展示
        self._total_invocations: int = 0
        self._successful_invocations: int = 0

    def is_available(self) -> bool:
        """是否可用。"""
        return self._enabled and self.llm is not None

    def generate(self, context: str, player_input: str, character_info: str = "",
                 character_state: str = "", scene_type: str = "", style: str = "") -> NarrativeDraft:
        """
        多智能体协作生成叙事。
        流程：情节大纲 → 角色审查 → 叙事撰写 → （如有问题）修订
        """
        draft = NarrativeDraft()

        if not self.is_available():
            return draft

        self._total_invocations += 1

        try:
            # 1. 情节架构师生成大纲
            draft.plot_outline = self.architect.generate_outline(context, player_input, scene_type)
            if not draft.plot_outline:
                logger.info("Multi-agent narrative aborted: empty outline")
                return draft

            # 2. 角色审查员审查
            draft.character_notes, draft.issues_found = self.reviewer.review(
                draft.plot_outline, character_info, character_state
            )

            # 3. 如果有问题，让架构师修订大纲
            if draft.issues_found and self._max_revisions > 0:
                revised_outline = self._revise_outline(draft.plot_outline, draft.issues_found)
                if revised_outline:
                    draft.plot_outline = revised_outline
                    draft.revisions = 1

            # 4. 对白撰写师生成最终叙事
            draft.final_narrative = self.writer.write_narrative(
                draft.plot_outline, draft.character_notes, context, player_input, style
            )

            if draft.final_narrative:
                self._successful_invocations += 1

            logger.info("Multi-agent narrative: outline=%d chars, narrative=%d chars, issues=%d, revisions=%d",
                        len(draft.plot_outline), len(draft.final_narrative),
                        len(draft.issues_found), draft.revisions)
        except Exception as e:
            logger.warning("Multi-agent narrative failed: %s", e)

        return draft

    def _revise_outline(self, outline: str, issues: list[str]) -> str:
        """根据审查问题修订大纲。"""
        if not self.llm or not issues:
            return ""

        issues_str = "\n".join(f"- {i}" for i in issues)
        prompt = f"""请修订以下情节大纲，解决指出的问题：

【原大纲】
{outline}

【需要解决的问题】
{issues_str}

修订后的大纲："""

        try:
            return self.llm.chat(prompt, temperature=0.5, max_tokens=1024) or ""
        except Exception as e:
            logger.warning("Outline revision failed: %s", e)
            return ""

    def get_stats(self) -> dict:
        """获取多智能体叙事引擎统计信息。"""
        return {
            "enabled": self._enabled,
            "max_revisions": self._max_revisions,
            "available": self.is_available(),
            "total_invocations": self._total_invocations,
            "successful_invocations": self._successful_invocations,
        }
