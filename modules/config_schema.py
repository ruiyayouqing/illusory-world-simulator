"""
[v9] 配置文件 Schema 验证 — 用 Pydantic 验证 config.json 的结构和类型
防止错误配置导致运行时崩溃。
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional


class LLMConfig(BaseModel):
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model_name: str = "deepseek-v4-flash"
    temperature: float = Field(default=0.8, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=256, le=32768)


class ImageConfig(BaseModel):
    api_key: str = ""
    base_url: str = "https://api.siliconflow.cn/v1/images/generations"
    model_name: str = "Kwai-Kolors/Kolors"
    image_size: str = "1024x576"
    auto_generate: bool = True


class EmbeddingConfig(BaseModel):
    api_key: str = ""
    base_url: str = "https://api.siliconflow.cn/v1"
    model_name: str = "BAAI/bge-m3"


class GameConfig(BaseModel):
    auto_save: bool = True
    max_short_term_memory: int = Field(default=20, ge=5, le=100)
    npc_offline_evolution: bool = True
    narrative_style: str = "网文爽文"
    narrative_style_custom: str = ""
    narrative_perspective: str = "third"
    max_context: int = Field(default=16384, ge=2048, le=32768)
    economy_enabled: bool = False
    action_validation_enabled: bool = True
    streaming_enabled: bool = True
    narrative_max_chars: int = Field(default=1000, ge=200, le=5000)


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8004, ge=1024, le=65535)
    allowed_origins: list[str] = [
        "http://localhost:8004",
        "http://127.0.0.1:8004",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


class UIConfig(BaseModel):
    theme: str = "parchment"
    accent_color: str = "#c9a96e"
    font_size: str = "medium"
    narrative_width: str = "55%"
    bg_color: str = "#0a0a0f"
    text_color: str = "#e0d5c1"
    panel_bg: str = "#111120"
    strip_gray_narrative: bool = True


class FixedPromptConfig(BaseModel):
    content: str = ""
    enabled: bool = False


class AppConfig(BaseModel):
    """太虚幻境 完整配置 Schema"""
    llm: LLMConfig = Field(default_factory=LLMConfig)
    # [v10.5+] 对话模型：用于游戏内叙事/NPC对话；未配置时回退到主力 llm
    dialogue_llm: LLMConfig = Field(default_factory=LLMConfig)
    # [v10.5+] 备用模型：用于辅助任务（蝴蝶评估/记忆整理等）；未配置时回退到主力 llm
    cheap_llm: LLMConfig = Field(default_factory=LLMConfig)
    image: ImageConfig = Field(default_factory=ImageConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    llm_profiles: dict[str, LLMConfig] = Field(default_factory=dict)
    dialogue_llm_profiles: dict[str, LLMConfig] = Field(default_factory=dict)
    cheap_llm_profiles: dict[str, LLMConfig] = Field(default_factory=dict)
    image_profiles: dict[str, ImageConfig] = Field(default_factory=dict)
    active_llm_profile: str = ""
    active_dialogue_llm_profile: str = ""
    active_cheap_llm_profile: str = ""
    active_image_profile: str = ""
    game: GameConfig = Field(default_factory=GameConfig)
    narrative_styles: dict[str, str] = Field(default_factory=dict)
    ui: UIConfig = Field(default_factory=UIConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    fixed_prompt: FixedPromptConfig = Field(default_factory=FixedPromptConfig)

    @classmethod
    def load_and_validate(cls, config_path: str) -> "AppConfig":
        """加载并验证配置文件，返回验证后的配置对象"""
        import json
        from pathlib import Path
        path = Path(config_path)
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls.model_validate(raw)
        except Exception as e:
            import logging
            logging.getLogger("chronoverse").warning(
                "Config validation failed, using defaults: %s", e
            )
            return cls()
