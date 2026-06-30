"""
[v9] 叙事时间感知器（重构版）

核心改进：
1. 所有时间模式必须有明确的"后/过后/以后/之后"后缀才判定为时间跳跃
2. 单次最大跳跃限制为30天（可配置）
3. 移除过于宽松的模式（如"一年"、"第二天"）
4. 排除历史描述（"百年前的历史"不算时间跳跃）
"""
from __future__ import annotations
import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("chronoverse.timekeeper")

# [v9] 不再设置硬编码上限，正则已严格要求"后/过后/以后/之后"后缀，误判风险极低
# 保留警告阈值用于日志提示
WARN_SKIP_DAYS = 36500  # 超过100年时记录警告日志
# [Bug] 单次最大跳跃天数（超过此值视为误判，拦截）
MAX_SKIP_DAYS = 365  # 单次最多跳跃1年


# ========== 时间表达正则库（严格模式） ==========

# 所有模式统一要求"后/过后/前/以后/之后"后缀
_SUFFIX = r'[之以]?(?:后|过后|以后|之后)'

PATTERNS_DAYS = [
    # "10天后"、"十天之后"
    (re.compile(r'(\d+)\s*[日天]' + _SUFFIX), lambda m: int(m.group(1))),
    (re.compile(r'([一二三四五六七八九十两]+)\s*[日天]' + _SUFFIX), lambda m: _cn_num_to_int(m.group(1))),
    # "几天后"、"数日后"
    (re.compile(r'[几数]\s*[日天]' + _SUFFIX), lambda m: 5),
]

PATTERNS_WEEKS = [
    (re.compile(r'(\d+)\s*[周个星期]' + _SUFFIX), lambda m: int(m.group(1)) * 7),
    (re.compile(r'[几数]\s*[周个星期]' + _SUFFIX), lambda m: 21),
]

PATTERNS_MONTHS = [
    (re.compile(r'(\d+)\s*个?\s*月' + _SUFFIX), lambda m: int(m.group(1)) * 30),
    (re.compile(r'([一二三四五六七八九十两]+)\s*个?\s*月' + _SUFFIX), lambda m: _cn_num_to_int(m.group(1)) * 30),
    (re.compile(r'[几数]\s*个?\s*月' + _SUFFIX), lambda m: 90),
    (re.compile(r'半\s*年' + _SUFFIX), lambda m: 182),
    (re.compile(r'半个\s*月' + _SUFFIX), lambda m: 15),
]

PATTERNS_YEARS = [
    (re.compile(r'(\d+)\s*年' + _SUFFIX), lambda m: int(m.group(1)) * 365),
    (re.compile(r'([一二三四五六七八九十两]+)\s*年' + _SUFFIX), lambda m: _cn_num_to_int(m.group(1)) * 365),
    (re.compile(r'[几数]\s*年' + _SUFFIX), lambda m: 1095),
    (re.compile(r'[多许好]\s*年' + _SUFFIX), lambda m: 1825),
    (re.compile(r'十\s*年' + _SUFFIX), lambda m: 3650),
    # [Bug] 数百年/几百年/几千年：需要前缀判断，避免"百\s*年"匹配"数百年后"中的"百年后"子串
    (re.compile(r'[数几多]\s*百\s*年' + _SUFFIX), lambda m: 300 * 365),  # 数百年≈300年
    (re.compile(r'[数几多]\s*千\s*年' + _SUFFIX), lambda m: 3000 * 365),  # 数千年≈3000年
    (re.compile(r'百\s*年' + _SUFFIX), lambda m: 36500),
    (re.compile(r'千\s*年' + _SUFFIX), lambda m: 365000),
    (re.compile(r'万\s*年' + _SUFFIX), lambda m: 3650000),
]

# 季节模式（保持，因为这些明确表示时间流逝）
PATTERNS_SEASONAL = [
    (re.compile(r'春\s*[去过].*秋\s*[来到]|寒\s*[来去].*暑\s*[来往]'), lambda m: 365),
    (re.compile(r'又\s*是\s*一\s*年|新\s*的\s*一\s*年'), lambda m: 365),
    (re.compile(r'四\s*季\s*轮|岁\s*月\s*[流转]'), lambda m: 365),
    (re.compile(r'光\s*阴\s*似\s*箭|时\s*光\s*飞\s*逝|弹\s*指|白\s*驹\s*过\s*隙'), lambda m: 0),
]

ALL_DAY_PATTERNS = PATTERNS_DAYS + PATTERNS_WEEKS + PATTERNS_MONTHS + PATTERNS_YEARS + PATTERNS_SEASONAL


def _cn_num_to_int(cn: str) -> int:
    """中文数字/单位转整数"""
    mapping = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
               '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
               '百': 100, '千': 1000, '万': 10000}
    if cn in mapping:
        return mapping[cn]
    if len(cn) == 2:
        a, b = mapping.get(cn[0], 0), mapping.get(cn[1], 0)
        if a >= 10:
            return a + b
        if b >= 10:
            return a * b
    total = 0
    for ch in cn:
        if ch in mapping:
            val = mapping[ch]
            if val >= 10 and total == 0:
                total = val
            elif val >= 10:
                total *= val
            else:
                total += val
    return total or 1


def parse_time_skip_from_text(text: str, world_type: str = "custom") -> dict:
    """[v9] 从文本中解析时间跳跃，无硬编码上限"""
    if not text:
        return {"days": 0, "matches": [], "is_vague": False}

    # 只取前600字符和最后200字符
    head = text[:600]
    tail = text[-200:] if len(text) > 200 else ""
    search_text = head + "\n" + tail

    # 排除历史描述和身世描述上下文
    # "百年前的历史"不算时间跳跃（含"前"）
    # "来自数百年后的灵魂"不算时间跳跃（身世描述，含"来自...后"）
    search_text_clean = re.sub(r'[\u4e00-\u9fff]*前[\u4e00-\u9fff]*', '', search_text)
    search_text_clean = re.sub(r'来自[^\n]{0,20}后[^\n]{0,10}', '', search_text_clean)
    search_text_clean = re.sub(r'穿越[^\n]{0,20}后[^\n]{0,10}', '', search_text_clean)

    best_days = 0
    all_matches = []
    has_vague = False

    for pattern, extractor in ALL_DAY_PATTERNS:
        for m in pattern.finditer(search_text_clean):
            days = extractor(m)
            match_text = m.group()
            if days == 0:
                has_vague = True
                all_matches.append({"text": match_text, "days": 0, "vague": True})
            else:
                all_matches.append({"text": match_text, "days": days, "vague": False})
                if days > best_days:
                    best_days = days

    # [Bug] 恢复单次跳跃上限，超过MAX_SKIP_DAYS视为误判并拦截
    if best_days > MAX_SKIP_DAYS:
        logger.warning(
            "检测到异常大时间跳跃: %d天（%.1f年），超过上限%d天，已拦截。匹配: %s",
            best_days, best_days / 365, MAX_SKIP_DAYS,
            [m["text"] for m in all_matches if m["days"] == best_days]
        )
        best_days = 0
    elif best_days > WARN_SKIP_DAYS:
        logger.info("检测到大时间跳跃: %d天（%.1f年）", best_days, best_days / 365)

    return {
        "days": best_days,
        "matches": all_matches,
        "is_vague": has_vague and best_days == 0,
    }


@dataclass
class NarrativeTimekeeper:
    """[v9] 跟踪叙事中的时间流逝，自动推进游戏时钟"""

    narrative_day_offset: int = 0
    last_year_evolved_offset: int = 0
    last_game_day: int = 1
    detected_skips: list[dict] = field(default_factory=list)
    pending_vague_skips: list[str] = field(default_factory=list)

    def parse_and_accumulate(self, text: str, player_input: str = "",
                             current_game_day: int = 1,
                             world_type: str = "custom") -> dict:
        """
        [v9] 解析叙事文本，累加时间偏移。
        只从叙事（AI回复）中检测时间跳跃，忽略玩家输入。
        """
        parsed = parse_time_skip_from_text(text or "", world_type=world_type)
        days_advanced = parsed["days"]

        if parsed["is_vague"] and not days_advanced:
            for m in parsed["matches"]:
                if m.get("vague") and m["text"] not in self.pending_vague_skips:
                    self.pending_vague_skips.append(m["text"])

        old_offset = self.narrative_day_offset

        if days_advanced > 0:
            self.narrative_day_offset += days_advanced
            self.detected_skips.append({
                "day": current_game_day,
                "advanced_days": days_advanced,
                "matches": [m["text"] for m in parsed["matches"]],
                "narrative_offset": self.narrative_day_offset,
            })
            if len(self.detected_skips) > 50:
                self.detected_skips = self.detected_skips[-50:]

        self.last_game_day = current_game_day
        year_crossed = (self.narrative_day_offset - self.last_year_evolved_offset) >= 365

        return {
            "days_advanced": days_advanced,
            "new_offset": self.narrative_day_offset,
            "total_offset": self.narrative_day_offset,
            "year_crossed": year_crossed,
            "matches": parsed["matches"],
            "has_vague_pending": len(self.pending_vague_skips) > 0,
            "vague_skips": list(self.pending_vague_skips),
        }

    def mark_year_evolved(self):
        self.last_year_evolved_offset = self.narrative_day_offset

    def get_narrative_year(self, base_day: int = 1) -> int:
        return (base_day + self.narrative_day_offset) // 365

    def get_narrative_date_display(self, world_state) -> str:
        total_days = (world_state.current_day if world_state else 1) + self.narrative_day_offset
        years = total_days // 365
        remaining = total_days % 365
        months = remaining // 30
        days = remaining % 30
        if years > 0:
            return f"叙事时间: 第{years}年{months}个月（累计{total_days}天）"
        elif months > 0:
            return f"叙事时间: {months}个月{days}天（累计{total_days}天）"
        else:
            return f"叙事时间: 第{total_days}天"

    def resolve_vague_skips(self, llm_result: int = 0, world_type: str = "custom") -> int:
        if not self.pending_vague_skips:
            return 0
        self.pending_vague_skips.clear()
        if llm_result > 0:
            # [Bug] resolve_vague_skips 也要执行 MAX_SKIP_DAYS 上限检查
            # 原来只有 parse_time_skip_from_text 有此检查，LLM返回的模糊跳跃可绕过
            if llm_result > MAX_SKIP_DAYS:
                logger.warning(
                    "LLM模糊时间跳跃 %d天（%.1f年）超过上限%d天，已拦截。",
                    llm_result, llm_result / 365, MAX_SKIP_DAYS
                )
                return 0
            if llm_result > WARN_SKIP_DAYS:
                logger.info("模糊时间跳跃: %d天（%.1f年）", llm_result, llm_result / 365)
            self.narrative_day_offset += llm_result
            self.detected_skips.append({
                "day": self.last_game_day,
                "advanced_days": llm_result,
                "matches": ["LLM解析模糊时间"],
                "narrative_offset": self.narrative_day_offset,
            })
            return llm_result
        return 0

    def to_dict(self) -> dict:
        return {
            "narrative_day_offset": self.narrative_day_offset,
            "last_year_evolved_offset": self.last_year_evolved_offset,
            "last_game_day": self.last_game_day,
            "detected_skips": self.detected_skips[-20:],
            "pending_vague_skips": self.pending_vague_skips,
        }

    def from_dict(self, data: dict):
        self.narrative_day_offset = data.get("narrative_day_offset", 0)
        self.last_year_evolved_offset = data.get("last_year_evolved_offset", 0)
        self.last_game_day = data.get("last_game_day", 1)
        self.detected_skips = data.get("detected_skips", [])
        self.pending_vague_skips = data.get("pending_vague_skips", [])
        # [Bug] 合理性检查：如果offset远大于游戏天数，说明旧存档有bug，重置
        if self.narrative_day_offset > self.last_game_day * 2:
            logger.warning(
                "Timekeeper offset异常: %d天（游戏第%d天），重置为0",
                self.narrative_day_offset, self.last_game_day
            )
            self.narrative_day_offset = 0
            self.last_year_evolved_offset = 0
            self.detected_skips = []
        # [Bug] 绝对上限检查：如果游戏天数本身异常大（>10年=3650天），
        # 很可能是旧存档被"百年后"等误判污染，重置到第1天
        if self.last_game_day > 3650:
            logger.warning(
                "Timekeeper last_game_day异常: %d天，超过10年上限，重置为1",
                self.last_game_day
            )
            self.narrative_day_offset = 0
            self.last_year_evolved_offset = 0
            self.last_game_day = 1
            self.detected_skips = []

    def get_time_context_for_prompt(self) -> str:
        if self.narrative_day_offset <= 0:
            return ""
        years = self.narrative_day_offset // 365
        months = (self.narrative_day_offset % 365) // 30
        parts = ["【叙事时间追踪】"]
        if years > 0:
            parts.append(f"从故事开始到现在，叙事中已经过去了约{years}年{months}个月。")
        else:
            parts.append(f"从故事开始到现在，叙事中已经过去了约{self.narrative_day_offset}天。")
        parts.append("请在叙事中体现时间的流逝——角色可能变老、关系可能变化、世界可能不同。")
        if self.detected_skips:
            last_skip = self.detected_skips[-1]
            skip_days = last_skip.get("advanced_days", 0)
            if skip_days >= 365:
                parts.append(f"上次时间跳跃：{skip_days // 365}年（{skip_days}天），请注意角色状态的变化。")
        return "\n".join(parts)
