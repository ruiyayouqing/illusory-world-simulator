"""
小说导入提示词

从小说文本中自动提取角色、世界观、地理、关系等信息。
"""

EXTRACT_CHARACTERS_PROMPT = """你是一个小说分析专家。从以下小说文本中提取主要角色信息。

【小说文本】
{novel_text}

【要求】
1. 提取3-8个最重要的角色（主角和关键配角）
2. 每个角色需要：名字、年龄（估算）、性格、说话风格、身份/职业、标签
3. 如果文本中没有明确信息，根据上下文合理推断

【输出JSON格式】
{{
    "characters": [
        {{
            "name": "角色名",
            "age": 25,
            "personality": "性格描述（20-40字）",
            "speaking_style": "说话风格（10-30字）",
            "role": "身份/职业",
            "tags": ["标签1", "标签2"],
            "background": "简要背景（50-100字）"
        }}
    ]
}}
只输出JSON。"""

EXTRACT_WORLD_PROMPT = """你是一个世界观分析专家。从以下小说文本中提取世界观设定。

【小说文本】
{novel_text}

【要求】
1. 推断世界类型（历史/武侠/修仙/奇幻/科幻/末日/现代/自定义）
2. 推断时代背景和纪年方式
3. 提取世界的核心设定（力量体系、社会结构、主要矛盾）
4. 如果是历史类，推断具体朝代

【输出JSON格式】
{{
    "world_type": "类型ID",
    "world_name": "世界名称",
    "description": "世界描述（100-200字）",
    "era_name": "纪元名称",
    "era_year": 1,
    "power_system": "力量体系描述",
    "social_structure": "社会结构描述",
    "core_conflict": "核心矛盾"
}}
只输出JSON。"""

EXTRACT_LOCATIONS_PROMPT = """你是一个地理分析专家。从以下小说文本中提取地点/场景信息。

【小说文本】
{novel_text}

【要求】
1. 提取5-10个重要的地点
2. 每个地点需要：名称、描述、类型（城镇/乡村/山林/水域/建筑/其他）
3. 如果文本中没有明确描述，根据上下文推断

【输出JSON格式】
{{
    "locations": [
        {{
            "name": "地点名称",
            "code": "location_code（英文小写+下划线）",
            "description": "地点描述（30-80字）",
            "location_type": "类型",
            "special_actions": ["可执行的特殊行动"]
        }}
    ]
}}
只输出JSON。"""

EXTRACT_RELATIONS_PROMPT = """你是一个关系分析专家。根据以下角色列表和小说文本，分析角色之间的关系。

【角色列表】
{characters_text}

【小说文本】
{novel_text}

【要求】
1. 分析每对角色之间的关系类型（亲人/朋友/敌人/师徒/恋人/上下级/陌生人等）
2. 估算好感度（0-100，50为中性）
3. 只分析有明确互动或暗示的关系

【输出JSON格式】
{{
    "relations": [
        {{
            "from": "角色A名字",
            "to": "角色B名字",
            "relation_type": "关系类型",
            "favor": 60,
            "description": "关系描述"
        }}
    ]
}}
只输出JSON。"""
