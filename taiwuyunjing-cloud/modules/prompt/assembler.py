from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("chronoverse.prompt")


@dataclass
class PromptSection:
    """一段Prompt内容"""
    name: str
    content: str
    priority: int = 5
    essential: bool = False
    scene_ratios: dict[str, float] = field(default_factory=dict)

    def token_estimate(self) -> int:
        return len(self.content) // 2 + 50


class PromptAssembler:
    """
    结构化Prompt组装器
    - 按优先级排序段
    - 用ContextBudget动态截断非必要段
    - 防止超上下文
    """

    def __init__(self, max_tokens: int = 8000, scene: str = "default"):
        self.sections: list[PromptSection] = []
        self.max_tokens = max_tokens
        self.scene = scene
        self._system_prefix = ""
        self._user_suffix = ""

    def set_system_prefix(self, prefix: str):
        self._system_prefix = prefix

    def set_user_suffix(self, suffix: str):
        self._user_suffix = suffix

    def add(self, name: str, content: str, priority: int = 5,
            essential: bool = False, scene_ratios: dict[str, float] = None) -> "PromptAssembler":
        if content and content.strip():
            self.sections.append(PromptSection(
                name=name,
                content=content.strip(),
                priority=priority,
                essential=essential,
                scene_ratios=scene_ratios or {}
            ))
        return self

    def remove(self, name: str) -> bool:
        original_len = len(self.sections)
        self.sections = [s for s in self.sections if s.name != name]
        return len(self.sections) < original_len

    def clear(self):
        self.sections.clear()

    def _estimate_total_tokens(self) -> int:
        total = len(self._system_prefix) // 2 + len(self._user_suffix) // 2
        for s in self.sections:
            total += s.token_estimate()
        return total

    def _truncate_to_budget(self) -> list[PromptSection]:
        """按优先级和场景比例截断段，保证不超预算"""
        essential_sections = [s for s in self.sections if s.essential]
        optional_sections = sorted(
            [s for s in self.sections if not s.essential],
            key=lambda s: (-s.priority, -len(s.content))
        )

        used_tokens = len(self._system_prefix) // 2 + len(self._user_suffix) // 2
        for s in essential_sections:
            used_tokens += s.token_estimate()

        available = self.max_tokens - used_tokens - 500
        if available < 0:
            logger.warning("Essential sections exceed token budget by %d tokens", -available)
            available = 2000

        result = list(essential_sections)
        current_tokens = 0

        scene_ratio_budgets = {}
        remaining_ratio = 1.0
        if self.scene:
            for s in optional_sections:
                if self.scene in s.scene_ratios:
                    scene_ratio_budgets[s.name] = s.scene_ratios[self.scene]
            total_ratio = sum(scene_ratio_budgets.values())
            if total_ratio > 1.0:
                for k in scene_ratio_budgets:
                    scene_ratio_budgets[k] /= total_ratio
                remaining_ratio = 0
            else:
                remaining_ratio = 1.0 - total_ratio

        for s in optional_sections:
            if s.name in scene_ratio_budgets:
                budget = int(available * scene_ratio_budgets[s.name])
            else:
                ratio_per_remaining = remaining_ratio / max(1, len([x for x in optional_sections if x.name not in scene_ratio_budgets]))
                budget = int(available * ratio_per_remaining)

            if current_tokens + s.token_estimate() <= budget or not result:
                content = s.content
                est = s.token_estimate()
                if current_tokens + est > available:
                    if len(content) > 200:
                        ratio = max(0.1, (available - current_tokens) / max(1, est))
                        keep_chars = int(len(content) * ratio)
                        content = content[:keep_chars] + "\n...(内容已截断)"
                    else:
                        continue
                result.append(PromptSection(
                    name=s.name,
                    content=content,
                    priority=s.priority,
                    essential=False,
                    scene_ratios=s.scene_ratios
                ))
                current_tokens += len(content) // 2 + 50

            if current_tokens >= available:
                break

        return result

    def assemble(self, max_tokens: int = None, scene: str = None) -> str:
        if max_tokens is not None:
            self.max_tokens = max_tokens
        if scene is not None:
            self.scene = scene

        sections = self._truncate_to_budget()

        parts = []
        if self._system_prefix:
            parts.append(self._system_prefix.strip())

        for s in sections:
            parts.append(s.content)

        if self._user_suffix:
            parts.append(self._user_suffix.strip())

        result = "\n\n".join(p for p in parts if p)
        logger.debug("Prompt assembled: %d sections, ~%d tokens (budget=%d)",
                     len(sections), len(result)//2, self.max_tokens)
        return result

    def assemble_messages(self, system_prompt: str = None, max_tokens: int = None, scene: str = None) -> list[dict]:
        if max_tokens is not None:
            self.max_tokens = max_tokens
        if scene is not None:
            self.scene = scene

        sections = self._truncate_to_budget()
        system_parts = []
        if system_prompt:
            system_parts.append(system_prompt.strip())
        if self._system_prefix:
            system_parts.append(self._system_prefix.strip())

        system_content = "\n\n".join(p for p in system_parts if p)
        user_parts = []
        for s in sections:
            user_parts.append(s.content)
        if self._user_suffix:
            user_parts.append(self._user_suffix.strip())

        user_content = "\n\n".join(p for p in user_parts if p)

        messages = []
        if system_content:
            messages.append({"role": "system", "content": system_content})
        if user_content:
            messages.append({"role": "user", "content": user_content})
        return messages
