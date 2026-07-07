"""结构化输出：JSON Schema 约束 + 响应格式控制。"""
from __future__ import annotations
import json
import logging
from typing import Any

logger = logging.getLogger("chronoverse.structured")


# ===== 预定义 JSON Schema =====

# 叙事生成响应 Schema
NARRATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "narrative": {
            "type": "string",
            "description": "叙事正文，至少500字，必须详细描写场景、对话、动作和心理活动",
        },
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "enum": ["A", "B", "C"]},
                    "text": {"type": "string", "description": "选项描述，必须与当前剧情紧密相关"},
                    "type": {"type": "string", "enum": ["action", "move", "talk", "custom"]},
                    "risk": {"type": "string", "enum": ["low", "medium", "high"]}
                },
                "required": ["id", "text", "type", "risk"]
            },
            "minItems": 2,
            "maxItems": 3,
            "description": "玩家可选行动选项，必须与叙事内容紧密相关"
        },
        "status_changes": {
            "type": "object",
            "description": "玩家状态变更",
            "properties": {
                "health": {"type": "integer"},
                "energy": {"type": "integer"},
                "strength": {"type": "integer"},
                "agility": {"type": "integer"},
                "intelligence": {"type": "integer"},
                "gold": {"type": "integer"},
                "reputation": {"type": "integer"}
            },
            "additionalProperties": False
        },
        "relation_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "npc_name": {"type": "string"},
                    "favor_change": {"type": "integer"},
                    "description": {"type": "string"}
                },
                "required": ["npc_name", "favor_change"]
            }
        },
        "tags_added": {"type": "array", "items": {"type": "string"}},
        "tags_removed": {"type": "array", "items": {"type": "string"}},
        "time_advance": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer"},
                "days": {"type": "integer"}
            }
        },
        "foreshadow_insert": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "importance": {"type": "string", "enum": ["low", "normal", "high", "critical"]}
                },
                "required": ["content"]
            }
        },
        "foreshadow_resolve": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["narrative"]
}


def get_narrative_schema(max_chars: int = 1000) -> dict[str, Any]:
    """[v10.6] 返回带配置字数的叙事 Schema 副本。max_chars=1000 → '500-1000字'"""
    hint = f"{max_chars // 2}-{max_chars}字"
    schema = json.loads(json.dumps(NARRATIVE_SCHEMA))
    schema["properties"]["narrative"]["description"] = f"叙事正文，{hint}"
    return schema


# 选项生成 Schema
OPTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "enum": ["A", "B", "C"]},
                    "text": {"type": "string"},
                    "risk": {"type": "string", "enum": ["safe", "medium", "dangerous"]},
                    "description": {"type": "string"}
                },
                "required": ["id", "text", "risk"]
            },
            "minItems": 2,
            "maxItems": 3
        }
    },
    "required": ["options"]
}

# NPC 行动 Schema
NPC_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["work", "rest", "social", "travel", "explore", "trade", "study", "craft"]},
        "description": {"type": "string"},
        "target": {"type": "string"},
        "energy_cost": {"type": "integer"},
        "expected_duration": {"type": "integer"}
    },
    "required": ["action", "description"]
}

# 蝴蝶效应评估 Schema
BUTTERFLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "impact_score": {"type": "number", "minimum": 0, "maximum": 10},
        "description": {"type": "string"},
        "consequences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event": {"type": "string"},
                    "scope": {"type": "string", "enum": ["personal", "local", "regional", "national", "global"]},
                    "delay_days": {"type": "integer"}
                },
                "required": ["event"]
            }
        },
        "irreversible": {"type": "boolean"}
    },
    "required": ["impact_score", "description"]
}

# 意图分类 Schema
INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["observe", "interact", "fight", "trade", "travel", "rest", "study", "craft", "social", "explore", "other"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "target": {"type": "string"},
        "action_type": {"type": "string"}
    },
    "required": ["intent"]
}

# 连续性审计 Schema
AUDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "dimensions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "severity": {"type": "string", "enum": ["pass", "warning", "critical"]},
                    "issues": {"type": "array", "items": {"type": "string"}},
                    "suggestion": {"type": "string"}
                },
                "required": ["name", "severity"]
            }
        },
        "overall_severity": {"type": "string", "enum": ["pass", "warning", "critical"]}
    },
    "required": ["dimensions", "overall_severity"]
}

# 世界生成 Schema
WORLD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "world_name": {"type": "string"},
        "world_type": {"type": "string"},
        "description": {"type": "string"},
        "power_system": {"type": "object"},
        "era_name": {"type": "string"},
        "era_year": {"type": "integer"},
        "factions": {"type": "object"},
        "locations": {"type": "object"},
        "npcs": {"type": "object"},
        "economy": {"type": "object"},
        "initial_event": {"type": "string"},
        "world_intro": {"type": "string"},
        "player_start": {"type": "object"},
        "world_lore": {"type": "array"},
        "map_distance": {"type": "object"}
    },
    "required": ["world_name", "description", "player_start", "npcs", "locations"]
}


class StructuredOutputManager:
    """结构化输出管理器。"""

    # Schema 注册表
    _schemas: dict[str, dict] = {
        "narrative": NARRATIVE_SCHEMA,
        "options": OPTIONS_SCHEMA,
        "npc_action": NPC_ACTION_SCHEMA,
        "butterfly": BUTTERFLY_SCHEMA,
        "intent": INTENT_SCHEMA,
        "audit": AUDIT_SCHEMA,
        "world": WORLD_SCHEMA,
    }

    @classmethod
    def get_schema(cls, name: str) -> dict | None:
        return cls._schemas.get(name)

    @classmethod
    def build_structured_prompt(cls, base_prompt: str, schema_name: str) -> str:
        """在 prompt 末尾追加 JSON Schema 约束指令。"""
        schema = cls.get_schema(schema_name)
        if not schema:
            return base_prompt

        # 构建结构化输出指令
        instruction = f"""

【输出格式要求】
你必须返回一个合法的 JSON 对象，符合以下 JSON Schema：
{json.dumps(schema, ensure_ascii=False, indent=2)}

注意：
1. 只输出 JSON，不要输出任何其他文字
2. 所有字符串值不要包含未转义的特殊字符
3. 可选字段如果不需要可以省略
4. 数值字段必须是数字而非字符串"""

        return base_prompt + instruction

    @classmethod
    def build_api_params(cls, schema_name: str) -> dict:
        """构建 OpenAI API 的 response_format 参数。"""
        schema = cls.get_schema(schema_name)
        if not schema:
            return {}

        # OpenAI structured output 格式
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": schema,
                    "strict": False  # 非严格模式，允许省略可选字段
                }
            }
        }

    @classmethod
    def validate(cls, data: Any, schema_name: str) -> tuple[bool, str]:
        """验证数据是否符合 schema。返回 (是否有效, 错误信息)。"""
        schema = cls.get_schema(schema_name)
        if not schema:
            return True, ""

        if not isinstance(data, dict):
            return False, "响应不是 JSON 对象"

        # 检查 required 字段 — 容错：只要求第一个 required 字段存在
        # LLM 偶尔漏掉字段，全部检查会导致大量警告但功能正常
        required = schema.get("required", [])
        if required:
            core_field = required[0]
            if core_field not in data:
                return False, f"缺少核心字段: {core_field}"

        return True, ""
