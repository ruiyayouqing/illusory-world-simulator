"""
[v9] 叙事风格管理器

支持6种预置风格 + 自定义文本 + 自定义文件上传。
风格指令注入到所有叙事prompt中，统一控制LLM的写作风格。

风格分为两级：
1. 全局默认风格（config.json → game.narrative_style）
2. 每世界覆盖风格（world_state.narrative_style，可选）
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("chronoverse.narrative_style")

# ── 预置风格定义 ──────────────────────────────────────────────

BUILTIN_STYLES: dict[str, str] = {
    "章回体": (
        "以章回体小说风格撰写，每段以「话说」「且说」等开头，以「欲知后事如何，且听下回分解」等收尾。"
        "语言半文半白，节奏舒缓，注重铺垫和悬念。风格参考《三言二拍》《水浒传》的白话文。"
    ),
    "半古半文": (
        "文言句式与白话叙事交融，类似《明朝那些事儿》或《琅琊榜》的风格。"
        "句式简练有力，偶用典故，但不晦涩。叙事中带有说书人的口吻。"
    ),
    "大白话": (
        "现代口语化叙事，轻松幽默，像朋友在讲故事。"
        "短句为主，偶尔吐槽，贴近当代网文读者的阅读习惯。不用文言词汇。"
        "禁止使用章回体、说书腔、半文半白套话；结尾不要写「欲知后事如何，且听下回分解」。"
    ),
    "严肃文学": (
        "冷峻克制的文学风格，类似余华、莫言。"
        "注重细节描写和心理刻画，语言凝练，情感内敛。叙事节奏沉稳，不追求爽感。"
    ),
    "网文爽文": (
        "快节奏网文风格，爽点密集，奇遇连连。"
        "主角光环明显，升级打怪，装逼打脸。节奏明快，冲突不断，语言直白有力，每段都有钩子。"
        "（注意：即使是爽文风格，也不允许出现「系统提示」「叮！」「好感度+5」等游戏化文本，"
        "所有金手指元素必须用小说笔法融入叙事。）"
    ),
    "诗化散文": (
        "意境优先的散文风格，类似《额尔古纳右岸》或迟子建的作品。"
        "注重景物描写和氛围营造，语言优美，富有诗意。节奏缓慢，适合沉浸式体验。"
    ),
    "真人作者": (
        "不要堆砌华丽辞藻，叙事以「说清楚事」为第一要务。"
        "对话在小说里占比高，高信息密度 + 强个性化，且承担推进剧情、传递世界观、塑造人物三重功能。"
        "核心角色有鲜明的语言个性标签，配角靠立场和性格反差区分。对话里藏信息，不靠旁白解释。"
        "对话有来有回，有打断、有跑偏、有潜台词，真实对话不是你一句我一句的完美问答，有人会插嘴，有人会跑题。"
        "可以通过大量内心独白展现角色的思维过程，这些独白不仅是心理描写，更是世界观展开和主题深化的载体。"
        "角色在内心独白中会进行逻辑推理、价值判断、自我反省，甚至自嘲，使读者能够深度代入角色的思维节奏。"
        "采用第三人称全知视角作为基本叙事框架，但其运用方式极为灵活。"
        "叙述者并不局限于单一角色的视点，而是在不同角色之间自由切换，甚至在同一场戏中完成「镜头」的转移，形成电影般的多机位叙事效果。"
        "每次视角切换都有明确的信息增量，不是为切换而切换，通过不同视角的信息差制造悬念和戏剧张力。"
        "需要制造信息差：不同角色知道不同的东西，让读者在多视角中自己拼凑全貌。"
        "每个出场角色都要有自己的「目标」和「逻辑」，哪怕是反派。"
        "配角有自己的成长弧和动机，不是纯工具人，但剧情线仍以主角为中心辐射，有台词的配角都给一个记忆点，纯背景角色一笔带过。"
        "句式以长句为主，经常使用多重从句嵌套来构建信息密集的表述。"
        "一个句子中可能同时包含条件状语、转折结构、解释性插入语和总结性后缀，形成层次分明的「信息包」。"
        "幽默是结构性的节奏工具，不是装饰。严肃场景中，可以用反差/自嘲/荒诞反应降压，叙述者偶尔以旁白口吻点评甚至自嘲。"
    ),
}


class NarrativeStyleManager:
    """叙事风格管理器：读取配置、管理自定义风格、生成风格指令"""

    def __init__(self, config_path: Optional[Path] = None):
        self._config_path = config_path
        self._config_cache: dict | None = None
        # [v1.3] 小说角色扮演模式标记：启用后强制使用第三人称
        self.is_novel_roleplay: bool = False

    def _load_config(self) -> dict:
        if self._config_cache is not None:
            return self._config_cache
        if self._config_path and self._config_path.exists():
            try:
                self._config_cache = json.loads(
                    self._config_path.read_text(encoding="utf-8")
                )
            except Exception as e:
                logger.warning("Config load failed, using defaults: %s", e)
                self._config_cache = {}
        else:
            self._config_cache = {}
        return self._config_cache

    def invalidate_cache(self):
        self._config_cache = None

    def get_style_names(self) -> list[str]:
        """返回所有可用风格名称（预置 + 自定义）"""
        config = self._load_config()
        names = list(BUILTIN_STYLES.keys())
        custom_styles = config.get("narrative_styles", {})
        for name in custom_styles:
            if name not in names:
                names.append(name)
        return names

    def get_style_description(self, style_name: str) -> str:
        """获取指定风格的描述文本"""
        if style_name in BUILTIN_STYLES:
            return BUILTIN_STYLES[style_name]
        config = self._load_config()
        custom_styles = config.get("narrative_styles", {})
        return custom_styles.get(style_name, "")

    def get_active_style_name(self, world_style: str = "") -> str:
        """获取当前激活的风格名称（世界覆盖 > 全局默认）"""
        # world_style 只能是明确的写作风格名。旧流程有时会把 world_type
        # （如“历史穿越”“modern”“fantasy”）传进来，不能让它覆盖全局写作风格，
        # 否则会匹配失败并回退到章回体。
        if world_style and (world_style == "自定义" or self.get_style_description(world_style)):
            return world_style
        config = self._load_config()
        return config.get("game", {}).get("narrative_style", "真人作者")

    def get_style_instruction(self, world_style: str = "") -> str:
        """
        生成风格指令文本，注入到prompt中。
        
        Args:
            world_style: 世界级别的风格覆盖（可为空）
        Returns:
            风格指令字符串
        """
        style_name = self.get_active_style_name(world_style)

        # 自定义风格：从config读取自定义文本
        if style_name == "自定义":
            config = self._load_config()
            custom_text = config.get("game", {}).get("narrative_style_custom", "")
            if custom_text:
                return f"【写作风格要求】\n{custom_text}"
            # 自定义但没有文本，回退到网文爽文
            style_name = "网文爽文"

        description = self.get_style_description(style_name)
        if not description:
            # 找不到风格，使用网文爽文作为默认
            description = BUILTIN_STYLES["网文爽文"]
            style_name = "网文爽文"

        result = f"【写作风格：{style_name}】\n{description}"

        # [Bug] 叙事视角：统一人称，避免混用
        # [v1.3] 小说角色扮演模式默认第三人称，可通过 novel_roleplay.narrative_perspective 覆盖
        config = self._load_config()
        if getattr(self, 'is_novel_roleplay', False):
            # 小说模式：默认第三人称，可由专门字段覆盖
            perspective = config.get("novel_roleplay", {}).get("narrative_perspective", "third")
        else:
            perspective = config.get("game", {}).get("narrative_perspective", "third")
        perspective_map = {
            "first": "【叙事视角：第一人称】\n叙事中统一使用「我」来指代玩家，如「我走进了大殿」、「我拔出剑」。禁止使用玩家姓名或「你」。",
            "second": "【叙事视角：第二人称】\n叙事中统一使用「你」来指代玩家，如「你走进了大殿」、「你拔出剑」。禁止使用玩家姓名代替「你」。",
            "third": "【叙事视角：第三人称】\n叙事中统一使用玩家姓名来指代玩家，如「玩家姓名走进了大殿」、「玩家姓名拔出剑」。禁止使用「你」或「我」。",
        }
        perspective_instruction = perspective_map.get(perspective, perspective_map["third"])
        result += f"\n\n{perspective_instruction}"

        return result

    def set_global_style(self, style_name: str, custom_text: str = ""):
        """设置全局默认风格"""
        config = self._load_config()
        config.setdefault("game", {})
        config["game"]["narrative_style"] = style_name
        if custom_text:
            config["game"]["narrative_style_custom"] = custom_text
        self._save_config(config)

    def add_custom_style(self, name: str, description: str):
        """添加自定义风格到config"""
        config = self._load_config()
        config.setdefault("narrative_styles", {})
        config["narrative_styles"][name] = description
        self._save_config(config)
        logger.info("自定义风格已添加: %s", name)

    def delete_custom_style(self, name: str) -> bool:
        """删除自定义风格（不能删除预置风格）"""
        if name in BUILTIN_STYLES:
            return False
        config = self._load_config()
        styles = config.get("narrative_styles", {})
        if name in styles:
            del styles[name]
            self._save_config(config)
            logger.info("自定义风格已删除: %s", name)
            return True
        return False

    def extract_style_keywords(self, text: str, llm=None) -> str:
        """
        从用户上传的写作范本中提取风格特征。
        如果提供了llm，用LLM提炼；否则用简单规则提取。
        """
        if llm:
            try:
                prompt = (
                    "请从以下文本中提取写作风格特征，用3-5条简洁的中文指令描述。"
                    "每条指令应该是一个具体的写作要求。\n\n"
                    f"【文本片段】\n{text[:3000]}\n\n"
                    "【输出格式】\n直接输出风格指令，每条一行，不要编号。"
                )
                return llm.chat(prompt, temperature=0.3, max_tokens=1024)
            except Exception as e:
                logger.warning("LLM风格提炼失败，使用规则提取: %s", e)

        # 规则提取：分析句式、用词、节奏
        keywords = []
        if len(text) > 0:
            avg_sentence_len = len(text) / max(text.count("。") + text.count("！") + text.count("？"), 1)
            if avg_sentence_len < 15:
                keywords.append("使用短句为主，节奏明快")
            elif avg_sentence_len > 40:
                keywords.append("使用长句，注重细节描写")

        # 检测文言特征
        classical_markers = ["之", "乎", "者", "也", "矣", "焉", "兮", "乃"]
        classical_count = sum(text.count(m) for m in classical_markers)
        if classical_count > len(text) / 100:
            keywords.append("带有文言句式，半文半白")
        else:
            keywords.append("使用现代白话文")

        # 检测对话比例
        dialogue_markers = text.count("「") + text.count("」") + text.count(""") + text.count(""")
        if dialogue_markers > len(text) / 200:
            keywords.append("对话丰富，注重人物语言描写")

        if not keywords:
            keywords = ["语言平实自然", "叙事流畅", "适度描写"]

        return "；".join(keywords)

    def _save_config(self, config: dict):
        if self._config_path:
            from .data.safe_io import atomic_write_json
            atomic_write_json(self._config_path, config)
            self._config_cache = config
