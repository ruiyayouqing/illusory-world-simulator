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
    # [P4-A-2] 模型价格表（USD/1K tokens），用于成本估算；未配置的模型成本估算为 0
    pricing: dict[str, dict[str, float]] = Field(default_factory=dict)


class LLMBudgetConfig(BaseModel):
    """[P4-A-2] BudgetGuard 预算控制配置。"""
    enabled: bool = True
    daily_budget_usd: float = Field(default=0.0, ge=0.0, description="每日 USD 预算上限，0=不限")
    per_turn_limit: int = Field(default=0, ge=0, description="每回合调用上限，0=不限")
    per_turn_window_sec: float = Field(default=30.0, ge=1.0, le=600.0, description="回合窗口时长（秒）")
    circuit_failure_threshold: int = Field(default=5, ge=1, description="触发熔断的连续失败次数")
    circuit_recovery_sec: float = Field(default=30.0, ge=1.0, le=3600.0, description="熔断恢复时长（秒）")


class LLMRuntimeConfig(BaseModel):
    """[v1.7 P2-4] LLM 运行时参数（超时/重试/Token上限），从 config.json 加载，避免硬编码。"""
    timeout: float = Field(default=60.0, ge=5.0, le=300.0, description="单次 SDK 调用超时")
    max_retries: int = Field(default=0, ge=0, le=5, description="SDK 自动重试次数（关闭以避免叠加）")
    preflight_timeout: float = Field(default=15.0, ge=5.0, le=60.0, description="启动时探测超时")
    retry_sleep_sec: float = Field(default=0.5, ge=0.1, le=5.0, description="项目级重试间隔")
    default_max_tokens: int = Field(default=8192, ge=256, le=32768, description="max_tokens<=0 时的兜底值")
    max_tokens_cap: int = Field(default=32768, ge=4096, le=131072, description="max_tokens 加倍上限")
    stream_default_max_tokens: int = Field(default=16384, ge=1024, le=65536, description="chat_stream 默认 max_tokens")
    world_gen_timeout: float = Field(default=180.0, ge=30.0, le=600.0, description="世界生成流式请求超时")
    structured_timeout: float = Field(default=60.0, ge=10.0, le=300.0, description="chat_structured 超时")


class LLMRouterConfig(BaseModel):
    """[v1.7 P2-4] Router 路由层超时与熔断参数。"""
    total_timeout: float = Field(default=60.0, ge=10.0, le=300.0, description="Router 总超时")
    async_single_timeout: float = Field(default=30.0, ge=5.0, le=120.0, description="单次异步调用超时")


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
    narrative_style: str = "真人作者"
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
    # [P4-A-2] BudgetGuard 预算控制配置（成本/熔断/回合上限）
    llm_budget: LLMBudgetConfig = Field(default_factory=LLMBudgetConfig)
    # [v1.7 P2-4] LLM 运行时参数（超时/重试/Token上限）
    llm_runtime: LLMRuntimeConfig = Field(default_factory=LLMRuntimeConfig)
    # [v1.7 P2-4] Router 路由层参数
    llm_router: LLMRouterConfig = Field(default_factory=LLMRouterConfig)
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
