"""
[v10+++] 异步 NPC 生成器 — 在玩家进入游戏后，后台逐步补充重要 NPC。

设计目标：
  - 启动时只同步创建 5 个核心 NPC，保证启动速度
  - 进入游戏后，用 cheap_llm 在后台逐步生成更多重要 NPC（目标 50 个）
  - AI 根据世界设定和已有 NPC 判断还需要什么类型的角色
  - 玩家无感，世界"生长"

触发：前端在 showGame() 后调用 POST /api/npc/async-create
执行：通过 BackgroundTaskQueue 在后台循环生成
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import TYPE_CHECKING

from .schemas import NPCState, Stats, RelationEntry
from .prompt_utils import resolve_location_name

if TYPE_CHECKING:
    from .game_engine import GameEngine

logger = logging.getLogger("chronoverse")


class NpcSpawner:
    """后台 NPC 生成器，通过 BackgroundTaskQueue 异步执行。"""

    # 目标 NPC 数量上限
    TARGET_NPC_COUNT = 50
    # 单次会话最多生成数量（避免长时间占用）
    MAX_SPAWN_PER_SESSION = 15
    # 每次生成间隔（秒），避免 LLM 速率限制
    SPAWN_INTERVAL_SEC = 8
    # 每次让 LLM 生成的 NPC 数量
    BATCH_SIZE = 3

    def __init__(self, engine: "GameEngine"):
        self.engine = engine
        self._spawning = False  # 防止重复触发

    def is_spawning(self) -> bool:
        """是否正在后台生成中"""
        return self._spawning

    def start_async_spawn(self) -> dict:
        """启动后台 NPC 生成任务。
        返回 {"status": "started"/"skipped", "current_count": N, "target": M}"""
        eng = self.engine
        if self._spawning:
            return {"status": "skipped", "reason": "already_spawning",
                    "current_count": len(eng.npc_states)}
        if not eng.cheap_llm:
            logger.warning("[NpcSpawner] cheap_llm 未配置，无法后台生成 NPC")
            return {"status": "skipped", "reason": "no_cheap_llm",
                    "current_count": len(eng.npc_states)}
        if not eng.world_state or not eng.player_state:
            return {"status": "skipped", "reason": "no_world_state",
                    "current_count": len(eng.npc_states)}

        current_count = len(eng.npc_states)
        if current_count >= self.TARGET_NPC_COUNT:
            return {"status": "skipped", "reason": "already_at_target",
                    "current_count": current_count, "target": self.TARGET_NPC_COUNT}

        self._spawning = True
        # 投递到后台任务队列
        eng.task_queue.post(self._spawn_loop)
        logger.info("[NpcSpawner] 后台 NPC 生成已启动，当前 %d 个，目标 %d 个",
                    current_count, self.TARGET_NPC_COUNT)
        return {
            "status": "started",
            "current_count": current_count,
            "target": self.TARGET_NPC_COUNT,
            "max_spawn_this_session": min(
                self.MAX_SPAWN_PER_SESSION,
                self.TARGET_NPC_COUNT - current_count
            ),
        }

    def _spawn_loop(self):
        """后台生成循环 — 在 BackgroundTaskQueue 的 worker 线程中执行。"""
        eng = self.engine
        spawned = 0
        try:
            while (self._spawning
                   and spawned < self.MAX_SPAWN_PER_SESSION
                   and len(eng.npc_states) < self.TARGET_NPC_COUNT):
                # 检查引擎是否仍可用
                if not eng.world_state or not eng.player_state:
                    logger.info("[NpcSpawner] 引擎状态不可用，停止生成")
                    break

                # 让 LLM 生成一批 NPC 设定
                try:
                    npc_designs = self._generate_npc_batch()
                except Exception as e:
                    logger.warning("[NpcSpawner] LLM 生成 NPC 设定失败: %s", e)
                    break

                if not npc_designs:
                    logger.info("[NpcSpawner] LLM 判断世界已足够丰富，停止生成")
                    break

                # 创建并添加 NPC
                for design in npc_designs:
                    if len(eng.npc_states) >= self.TARGET_NPC_COUNT:
                        break
                    npc = self._create_npc_from_design(design)
                    if npc:
                        eng.npc_states[npc.agent_id] = npc
                        spawned += 1
                        logger.info("[NpcSpawner] 创建 NPC: %s (%s) — 第 %d 个",
                                    npc.name, npc.role, len(eng.npc_states))
                        # 添加世界事件
                        eng.event_log_today.append({
                            "event_id": f"npc_spawn_{npc.agent_id}",
                            "event_type": "npc_appearance",
                            "description": f"新人物出现：{npc.name}（{npc.role}）",
                            "impact_level": 2,
                            "day": eng.world_state.current_day,
                        })

                # 保存进度
                try:
                    eng.save_game("auto")
                except Exception as e:
                    logger.warning("[NpcSpawner] 保存失败: %s", e)

                # 间隔等待，避免 LLM 速率限制
                time.sleep(self.SPAWN_INTERVAL_SEC)

            logger.info("[NpcSpawner] 生成完成，本次新增 %d 个，总计 %d 个",
                        spawned, len(eng.npc_states))
        except Exception as e:
            logger.error("[NpcSpawner] 生成循环异常: %s", e, exc_info=True)
        finally:
            self._spawning = False

    def _generate_npc_batch(self) -> list[dict]:
        """调用 cheap_llm 生成一批 NPC 设定。
        返回 NPC 设计列表，空列表表示 LLM 认为世界已足够丰富。"""
        eng = self.engine
        ws = eng.world_state

        # 构建已有 NPC 摘要（用 display name）
        existing_npcs = []
        for nid, npc in eng.npc_states.items():
            existing_npcs.append({
                "name": npc.name,
                "role": npc.role or "无",
                "location": resolve_location_name(npc.current_location or "", ws),
            })

        # 构建地点列表（用 display name）
        loc_names = []
        for loc_code, loc_data in ws.locations.items():
            if isinstance(loc_data, dict):
                name = loc_data.get("location_name") or loc_data.get("name") or loc_code
            else:
                name = str(loc_data) if loc_data else loc_code
            loc_names.append(name)

        # 构建势力列表
        faction_names = list(ws.factions.keys()) if ws.factions else []

        prompt = f"""你是一个虚拟世界的 NPC 设计师。请根据以下世界信息，判断还需要创建哪些重要 NPC 来丰富这个世界。

【世界信息】
世界名称：{ws.world_name}
世界类型：{ws.world_type}
描述：{ws.description[:200]}
当前日期：第{ws.current_day}天，{ws.season}，{ws.weather}

【已有地点】
{", ".join(loc_names[:15]) if loc_names else "无"}

【已有势力】
{", ".join(faction_names) if faction_names else "无"}

【已有 NPC（{len(existing_npcs)} 个）】
{json.dumps(existing_npcs[:20], ensure_ascii=False) if existing_npcs else "无"}

【玩家信息】
姓名：{eng.player_state.name}，身份：{eng.player_state.social.position}，位置：{resolve_location_name(eng.player_state.location, ws)}

【任务】
判断这个世界还需要哪些重要 NPC。一个完整的世界应该有 {self.TARGET_NPC_COUNT} 个左右的 NPC，涵盖各阶层和职业。
如果已有 NPC 数量已经足够（接近 {self.TARGET_NPC_COUNT} 个且覆盖各阶层），返回空数组。
否则，生成 {self.BATCH_SIZE} 个新 NPC，确保与已有 NPC 不重复，覆盖不同阶层和地点。

【命名规则 - 极其重要】NPC 的名字必须与世界类型和文化背景完全匹配：
- 历史穿越/武侠/修仙：使用中文姓名（如"赵铁心"、"柳三娘"、"沈文"）
- 奇幻冒险：使用中文音译的西方/奇幻风格名字（如"巴克"、"阿尔德里克"、"索菲亚"、"桑尼克"），绝对不能出现英文字母！
- 科幻未来：使用中文音译的现代名字（如"亚历克斯"、"诺瓦"、"凯"），绝对不能出现英文字母！
- 末日生存：使用中文音译的现代简短名字（如"铁锤"、"老猫"、"雷文"），绝对不能出现英文字母！
- 都市异能：使用现代中文名（如"林清"、"周明"）
- 自定义世界：根据世界描述中的文化背景来命名
绝对禁止在任何名字中使用英文字母！所有名字必须用中文汉字书写！

【返回格式】严格返回 JSON，不要有其他文字：
{{
  "need_more": true,
  "reason": "简要说明为什么需要这些 NPC",
  "npcs": [
    {{
      "name": "符合世界文化背景的姓名",
      "role": "职业身份",
      "age": 25,
      "location": "所在地点名称（必须从已有地点中选择）",
      "personality": "性格描述（20-50字）",
      "speaking_style": "说话风格（10-30字）",
      "faction": "所属势力（从已有势力中选择，无则留空）",
      "relation_to_player": "与玩家的初始关系（如：陌生、敬仰、敌视、好奇）",
      "initial_favor": 50,
      "tags": ["标签1", "标签2"]
    }}
  ]
}}

如果世界已足够丰富，返回：{{"need_more": false, "reason": "世界NPC已覆盖各阶层", "npcs": []}}"""

        try:
            result = eng.cheap_llm.chat_json(prompt, temperature=0.8, max_tokens=4096)
            if not result:
                return []

            # 兼容 LLM 可能直接返回列表的情况
            if isinstance(result, list):
                return result

            if isinstance(result, dict):
                if not result.get("need_more", True):
                    return []
                npcs = result.get("npcs", [])
                if isinstance(npcs, list):
                    return npcs

            return []
        except Exception as e:
            logger.warning("[NpcSpawner] LLM 调用失败: %s", e)
            return []

    def _create_npc_from_design(self, design: dict) -> NPCState | None:
        """从 LLM 设计创建 NPCState，包含验证逻辑。"""
        eng = self.engine
        ws = eng.world_state

        name = design.get("name", "").strip()
        if not name or len(name) > 10:
            logger.warning("[NpcSpawner] NPC 名字无效: %s", name)
            return None

        # 去重检查
        for existing in eng.npc_states.values():
            if existing.name == name:
                logger.debug("[NpcSpawner] NPC 已存在，跳过: %s", name)
                return None

        # 验证 location：LLM 返回的是 display name，需要反查 location code
        loc_display = design.get("location", "")
        loc_code = self._resolve_location_code(loc_display)
        if not loc_code:
            # 回退到玩家当前位置
            loc_code = eng.player_state.location or ""

        # 生成 agent_id
        agent_id = f"npc_{uuid.uuid4().hex[:8]}"

        # 解析年龄
        try:
            age = int(design.get("age", 25))
            age = max(10, min(120, age))  # 合理范围
        except (ValueError, TypeError):
            age = 25

        # 解析初始好感度
        try:
            favor = int(design.get("initial_favor", 50))
            favor = max(0, min(100, favor))
        except (ValueError, TypeError):
            favor = 50

        # 解析关系类型
        relation_desc = design.get("relation_to_player", "陌生")
        relation_type = "陌生人"
        if "敌" in relation_desc or "厌" in relation_desc or "仇" in relation_desc:
            relation_type = "敌人"
        elif "爱人" in relation_desc or "恋人" in relation_desc or "夫妻" in relation_desc:
            relation_type = "爱人"
        elif "师" in relation_desc and ("父" in relation_desc or "徒" not in relation_desc):
            relation_type = "师徒"
        elif "下属" in relation_desc or "部下" in relation_desc or "侍从" in relation_desc:
            relation_type = "下属"
        elif "亲人" in relation_desc or "家人" in relation_desc or "父子" in relation_desc or "母子" in relation_desc:
            relation_type = "亲人"
        elif "朋友" in relation_desc or "好友" in relation_desc or "挚友" in relation_desc:
            relation_type = "朋友"
        elif "邻居" in relation_desc:
            relation_type = "邻居"
        elif "同门" in relation_desc or "师兄弟" in relation_desc:
            relation_type = "同门"
        elif "主" in relation_desc and "仆" in relation_desc:
            relation_type = "主仆"
        elif "生意" in relation_desc or "商" in relation_desc:
            relation_type = "生意伙伴"
        elif "恩" in relation_desc:
            relation_type = "恩人"
        elif "青梅" in relation_desc:
            relation_type = "青梅竹马"

        # 创建 NPCState
        npc = NPCState(
            agent_id=agent_id,
            name=name,
            age=age,
            role=design.get("role", ""),
            personality=design.get("personality", ""),
            speaking_style=design.get("speaking_style", ""),
            current_location=loc_code,
            relation_to_player=RelationEntry(
                favor=favor,
                relation_type=relation_type,
                description=relation_desc,
            ),
            tags=design.get("tags", []) or [design.get("role", "")],
        )

        # 设置 ai_behavior
        npc.ai_behavior = {
            "personality_traits": design.get("tags", []),
            "current_goal": "",
            "long_term_goal": "",
            "short_term_goals": [],
            "decision_style": "normal",
        }

        return npc

    def _resolve_location_code(self, loc_display: str) -> str:
        """将地点 display name 反查为 location code。
        如果找不到匹配，返回空字符串。"""
        eng = self.engine
        ws = eng.world_state
        if not ws or not ws.locations or not loc_display:
            return ""

        for loc_code, loc_data in ws.locations.items():
            if isinstance(loc_data, dict):
                name = loc_data.get("location_name") or loc_data.get("name") or ""
                if name == loc_display or loc_code == loc_display:
                    return loc_code
            elif isinstance(loc_data, str):
                if loc_data == loc_display or loc_code == loc_display:
                    return loc_code

        # 模糊匹配：display name 包含在 location name 中
        for loc_code, loc_data in ws.locations.items():
            if isinstance(loc_data, dict):
                name = loc_data.get("location_name") or loc_data.get("name") or ""
                if loc_display in name or name in loc_display:
                    return loc_code

        return ""
