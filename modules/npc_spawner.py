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
from .mbti_styles import assign_mbti_to_npc, mbti_to_decision_style

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
                    # [Bug] 强制生成模式下返回空，可能是 LLM 异常或缓存问题，
                    # 记录警告但不直接停止，给予少量重试机会
                    current_count = len(eng.npc_states)
                    if current_count < int(self.TARGET_NPC_COUNT * 0.8):
                        empty_retry = getattr(self, "_empty_retry_count", 0) + 1
                        self._empty_retry_count = empty_retry
                        if empty_retry <= 2:
                            logger.warning(
                                "[NpcSpawner] 数量仅 %d（目标 %d），LLM 返回空（重试 %d/2）",
                                current_count, self.TARGET_NPC_COUNT, empty_retry,
                            )
                            time.sleep(self.SPAWN_INTERVAL_SEC)
                            continue
                        else:
                            logger.warning(
                                "[NpcSpawner] 连续 %d 次返回空，停止生成（当前 %d 个）",
                                empty_retry, current_count,
                            )
                            break
                    logger.info("[NpcSpawner] LLM 判断世界已足够丰富，停止生成")
                    break

                # 成功生成，重置重试计数
                self._empty_retry_count = 0

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

    def preview_custom_batch(self, count: int = 5, focus: str = "",
                             requirement: str = "") -> dict:
        """[用户需求] 生成 NPC 设定但不加入世界，返回给玩家预览。
        玩家确认后调用 confirm_spawn 才会真正加入。

        Returns:
            {"status": "ok", "designs": [...]} 或 {"status": "error", "error": "..."}
        """
        eng = self.engine
        if self._spawning:
            return {"status": "error", "error": "后台自动生成正在进行中，请稍候再试"}
        if not eng.cheap_llm:
            return {"status": "error", "error": "AI 服务未配置"}
        if not eng.world_state or not eng.player_state:
            return {"status": "error", "error": "世界未初始化"}

        # 限制数量
        count = max(1, min(10, int(count)))

        # 生成 NPC 设定（注入随机种子，避免"重新生成"时缓存命中返回相同结果）
        try:
            import time as _time, random as _random
            regen_seed = f"{int(_time.time()*1000)}-{_random.randint(1000, 9999)}"
            designs = self._generate_custom_npc_batch(count, focus, requirement, regen_seed)
        except Exception as e:
            logger.warning("[NpcSpawner] 预览生成 LLM 调用失败: %s", e)
            return {"status": "error", "error": f"AI 生成失败: {e}"}

        if not designs:
            return {
                "status": "ok",
                "designs": [],
                "message": "AI 未返回有效角色设定",
            }

        # 过滤掉名字重复的（与已有 NPC 重名），但不过滤太多
        existing_names = {npc.name for npc in eng.npc_states.values()}
        valid_designs = []
        for d in designs:
            name = (d.get("name") or "").strip()
            if name and name not in existing_names:
                valid_designs.append(d)

        return {
            "status": "ok",
            "designs": valid_designs,
        }

    def confirm_spawn(self, designs: list) -> dict:
        """[用户需求] 玩家确认后，将选中的 NPC designs 正式加入世界。

        Args:
            designs: 玩家确认的 NPC 设定列表

        Returns:
            {"status": "ok", "spawned": N, "npcs": [...], "skipped": M}
        """
        eng = self.engine
        if not eng.world_state or not eng.player_state:
            return {"status": "error", "error": "世界未初始化"}

        spawned_npcs = []
        skipped = 0
        for design in designs:
            npc = self._create_npc_from_design(design)
            if npc:
                eng.npc_states[npc.agent_id] = npc
                spawned_npcs.append({
                    "name": npc.name,
                    "role": npc.role,
                    "age": npc.age,
                    "personality": npc.personality,
                    "location": npc.current_location,
                })
                # 添加世界事件
                eng.event_log_today.append({
                    "event_id": f"npc_spawn_{npc.agent_id}",
                    "event_type": "npc_appearance",
                    "description": f"新人物出现：{npc.name}（{npc.role}）",
                    "impact_level": 2,
                    "day": eng.world_state.current_day,
                })
                logger.info("[NpcSpawner] 确认创建 NPC: %s (%s)",
                            npc.name, npc.role)
            else:
                skipped += 1

        # 保存
        if spawned_npcs:
            try:
                eng.save_game("auto")
            except Exception as e:
                logger.warning("[NpcSpawner] 保存失败: %s", e)

        return {
            "status": "ok",
            "spawned": len(spawned_npcs),
            "npcs": spawned_npcs,
            "skipped": skipped,
        }

    def _generate_custom_npc_batch(self, count: int, focus: str,
                                   requirement: str, regen_seed: str = "") -> list[dict]:
        """调用 cheap_llm 生成指定数量的 NPC 设定（用户指定参数）。

        regen_seed: 随机种子，注入 prompt 避免缓存命中（用于"重新生成"场景）。
        """
        eng = self.engine
        ws = eng.world_state

        # 构建已有 NPC 摘要（含性格/年龄/关系等，供 AI 参考避免重复并构建关系网）
        existing_npcs = []
        for nid, npc in eng.npc_states.items():
            rel = npc.relation_to_player
            ai_beh = npc.ai_behavior or {}
            existing_npcs.append({
                "name": npc.name,
                "role": npc.role or "无",
                "age": npc.age,
                "location": resolve_location_name(npc.current_location or "", ws),
                "personality": (npc.personality or "")[:40],
                "tags": (npc.tags or [])[:4],
                "relation": rel.relation_type if rel else "陌生",
                "favor": rel.favor if rel else 50,
                "long_term_goal": (ai_beh.get("long_term_goal") or "")[:30],
            })

        # 构建地点列表
        loc_names = []
        for loc_code, loc_data in ws.locations.items():
            if isinstance(loc_data, dict):
                name = loc_data.get("location_name") or loc_data.get("name") or loc_code
            else:
                name = str(loc_data) if loc_data else loc_code
            loc_names.append(name)

        # 构建势力列表
        faction_names = list(ws.factions.keys()) if ws.factions else []

        # 角色类型倾向描述
        focus_desc = {
            "all": "不限定类型，可覆盖各阶层和职业",
            "combat": "偏向战斗型角色（武者、侠客、护卫、剑客等）",
            "social": "偏向社交型角色（商人、官员、名士、艺人等）",
            "merchant": "偏向商业型角色（商人、掌柜、伙计、手艺人等）",
            "martial": "偏向修行/武林角色（修士、宗门弟子、武林高手等）",
            "commoner": "偏向平民百姓角色（农夫、猎户、仆从、小贩等）",
            "antagonist": "偏向反派/敌对角色（黑道、邪修、对头、阴谋家等）",
        }.get(focus, "不限定类型，可覆盖各阶层和职业")

        # 自定义需求
        user_req_section = ""
        if requirement and requirement.strip():
            user_req_section = f"""
【用户特别需求】
{requirement.strip()}
请优先满足用户的上述需求，但仍然要符合世界设定和文化背景。
"""

        prompt = f"""你是一个虚拟世界的 NPC 设计师。请根据以下世界信息，生成 {count} 个新的重要 NPC。

【世界信息】
世界名称：{ws.world_name}
世界类型：{ws.world_type}
描述：{ws.description[:300]}
当前日期：第{ws.current_day}天，{ws.season}，{ws.weather}

【已有地点】
{", ".join(loc_names[:15]) if loc_names else "无"}

【已有势力】
{", ".join(faction_names) if faction_names else "无"}

【已有 NPC（{len(existing_npcs)} 个，请仔细参考以避免重复并构建关系网）】
{json.dumps(existing_npcs[:20], ensure_ascii=False) if existing_npcs else "无"}

【玩家信息】
姓名：{eng.player_state.name}，身份：{eng.player_state.social.position}，位置：{resolve_location_name(eng.player_state.location, ws)}

【本次生成要求】
数量：{count} 个
类型倾向：{focus_desc}
{user_req_section}
【硬性要求】
1. 必须与已有 NPC 不重名、不重复
2. 性格、职业、年龄层不要与已有 NPC 雷同，尽量覆盖空缺的阶层和身份
3. 新 NPC 可以与已有 NPC 存在关系（如师徒、宿敌、亲属、同门、旧识等），在 personality 或 long_term_goal 中体现这种关联，让世界更立体
4. 参考已有 NPC 的 relation/favor 分布，避免新角色全部是陌生人
5. 角色要有鲜明个性，能推动剧情或与玩家产生有趣互动
6. 地点必须从已有地点中选择
7. 覆盖不同阶层和职业，不要全部雷同

【命名规则 - 极其重要】NPC 的名字必须与世界类型和文化背景完全匹配：
- 历史穿越/武侠/修仙：使用中文姓名（如"赵铁心"、"柳三娘"、"沈文"）
- 奇幻冒险：使用中文音译的西方/奇幻风格名字（如"巴克"、"阿尔德里克"、"索菲亚"、"桑尼克"），绝对不能出现英文字母！
- 科幻未来：使用中文音译的现代名字（如"亚历克斯"、"诺瓦"、"凯"），绝对不能出现英文字母！
- 末日生存：使用中文音译的现代简短名字（如"铁锤"、"老猫"、"雷文"），绝对不能出现英文字母！
- 都市异能：使用现代中文名（如"林清"、"周明"）
- 自定义世界：根据世界描述中的文化背景来命名
绝对禁止在任何名字中使用英文字母！所有名字必须用中文汉字书写！

【创意种子】本次生成的创意种子：{regen_seed}（请据此创造与众不同的角色，避免与之前的生成结果雷同）

【返回格式】严格返回 JSON，不要有其他文字：
{{
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
      "tags": ["标签1", "标签2"],
      "long_term_goal": "该NPC的长期人生目标（一句话，10-30字）"
    }}
  ]
}}"""

        try:
            # [Bug] 必须传 schema_hint，否则 chat_json 会追加硬编码的
            # {"narrative":..., "options":...} 格式约束，LLM 就不会返回 npcs 字段
            schema_hint = '{"npcs":[{"name":"姓名","role":"职业","age":25,"location":"地点","personality":"性格","speaking_style":"说话风格","faction":"势力","relation_to_player":"关系","initial_favor":50,"tags":["标签"],"long_term_goal":"长期目标"}]}'
            result = eng.cheap_llm.chat_json(
                prompt, temperature=0.8, max_tokens=4096, schema_hint=schema_hint,
            )
            if not result:
                logger.warning("[NpcSpawner] chat_json 返回空")
                return []

            # 兼容 LLM 可能直接返回列表的情况
            if isinstance(result, list):
                logger.info("[NpcSpawner] LLM 直接返回列表，共 %d 项", len(result))
                return result[:count]

            if isinstance(result, dict):
                # 兼容多种可能的字段名：npcs / characters / new_npcs / data
                npcs = None
                for key in ("npcs", "characters", "new_npcs", "data", "result", "list"):
                    if key in result and isinstance(result[key], list):
                        npcs = result[key]
                        logger.info("[NpcSpawner] 从字段 '%s' 取到 %d 个 NPC", key, len(npcs))
                        break
                if npcs is None:
                    # 整个 dict 可能就是单个 NPC 设定
                    if result.get("name") and result.get("role"):
                        npcs = [result]
                        logger.info("[NpcSpawner] LLM 返回单个 NPC 设定，转为列表")
                    else:
                        logger.warning("[NpcSpawner] LLM 返回 dict 但无 npcs 字段，keys=%s, raw=%.500s",
                                       list(result.keys()), json.dumps(result, ensure_ascii=False)[:500])
                        return []
                if isinstance(npcs, list):
                    return npcs[:count]

            logger.warning("[NpcSpawner] LLM 返回类型无法识别: %s", type(result).__name__)
            return []
        except Exception as e:
            logger.warning("[NpcSpawner] 自定义 LLM 调用失败: %s", e)
            return []

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

        current_count = len(eng.npc_states)
        # [Bug] 当 NPC 数量远低于目标值时，LLM 容易误判"世界已足够丰富"
        # 导致只生成 5-6 个 NPC 就停止。这里在数量不足时强制要求生成，
        # 只有当数量接近目标（>= 80%）时才让 LLM 自行判断是否收尾。
        FORCE_GENERATE_THRESHOLD = int(self.TARGET_NPC_COUNT * 0.8)  # 40 个
        force_generate = current_count < FORCE_GENERATE_THRESHOLD

        if force_generate:
            task_section = f"""【任务】
当前世界仅有 {current_count} 个 NPC，远低于目标数量 {self.TARGET_NPC_COUNT} 个。**必须**生成 {self.BATCH_SIZE} 个新 NPC，不得以"世界已足够丰富"为由返回空数组。
请确保新 NPC 与已有 NPC 不重复，覆盖不同阶层、职业、年龄层和地点，让世界更立体。"""
            sufficient_hint = ""
        else:
            task_section = f"""【任务】
判断这个世界还需要哪些重要 NPC。一个完整的世界应该有 {self.TARGET_NPC_COUNT} 个左右的 NPC，涵盖各阶层和职业。
如果已有 NPC 数量已经足够（接近 {self.TARGET_NPC_COUNT} 个且覆盖各阶层），返回空数组。
否则，生成 {self.BATCH_SIZE} 个新 NPC，确保与已有 NPC 不重复，覆盖不同阶层和地点。"""
            # 注意：这里用单引号字符串避免双引号转义（f-string 表达式不能含反斜杠）
            sufficient_hint = '如果世界已足够丰富，返回：{"need_more": false, "reason": "世界NPC已覆盖各阶层", "npcs": []}'

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

{task_section}

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
      "tags": ["标签1", "标签2"],
      "long_term_goal": "该NPC的长期人生目标（一句话，10-30字，如：成为一代宗师、积累万贯家财、寻得失散的亲人）"
    }}
  ]
}}

{sufficient_hint}"""

        try:
            # [Bug] 必须传 schema_hint，否则 chat_json 会追加硬编码的
            # {"narrative":..., "options":...} 格式约束，LLM 就不会返回 npcs 字段
            schema_hint = '{"need_more":true,"reason":"原因","npcs":[{"name":"姓名","role":"职业","age":25,"location":"地点","personality":"性格","speaking_style":"说话风格","faction":"势力","relation_to_player":"关系","initial_favor":50,"tags":["标签"],"long_term_goal":"长期目标"}]}'
            # [Bug] 注入随机种子避免缓存命中（否则同一世界多次调用会返回相同结果）
            import time as _time, random as _random
            seed_hint = f"{int(_time.time()*1000)}-{_random.randint(1000, 9999)}"
            result = eng.cheap_llm.chat_json(
                prompt + f"\n【创意种子】{seed_hint}",
                temperature=0.9, max_tokens=4096, schema_hint=schema_hint,
            )
            if not result:
                logger.warning("[NpcSpawner] chat_json 返回空")
                return []

            # 兼容 LLM 可能直接返回列表的情况
            if isinstance(result, list):
                logger.info("[NpcSpawner] LLM 直接返回列表，共 %d 项", len(result))
                return result

            if isinstance(result, dict):
                # [Bug] 强制生成模式下，忽略 LLM 的 need_more=false 判断
                # （LLM 可能在数量不足时仍误判为足够丰富）
                if not force_generate and not result.get("need_more", True):
                    return []
                npcs = result.get("npcs", [])
                if not isinstance(npcs, list):
                    # 兼容多种可能的字段名：characters / new_npcs / data
                    for key in ("characters", "new_npcs", "data", "result", "list"):
                        if key in result and isinstance(result[key], list):
                            npcs = result[key]
                            logger.info("[NpcSpawner] 从字段 '%s' 取到 %d 个 NPC", key, len(npcs))
                            break
                if isinstance(npcs, list):
                    if not npcs:
                        logger.warning("[NpcSpawner] LLM 返回的 npcs 为空，result keys=%s, raw=%.500s",
                                       list(result.keys()), json.dumps(result, ensure_ascii=False)[:500])
                    # 过滤掉与已有 NPC 重名的（LLM 偶尔会重复）
                    existing_names = {n.name for n in eng.npc_states.values()}
                    unique = []
                    for d in npcs:
                        nm = (d.get("name") or "").strip() if isinstance(d, dict) else ""
                        if nm and nm not in existing_names:
                            existing_names.add(nm)  # 防止批内重名
                            unique.append(d)
                    return unique
                else:
                    logger.warning("[NpcSpawner] LLM 返回 dict 但无 npcs 字段，keys=%s, raw=%.500s",
                                   list(result.keys()), json.dumps(result, ensure_ascii=False)[:500])

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
        # [G] 从 design 读取 long_term_goal（LLM 在生成 NPC 时给出，可能为空）
        # [Bug] decision_style 原先硬编码为 "normal"，导致所有NPC在修改角色面板中
        #       均显示"普通"。现在根据性格描述和标签分配 MBTI，再由 MBTI 映射到
        #       5种决策风格（normal/cautious/aggressive/passive/cunning）。
        _mbti = design.get("mbti_type", "") or assign_mbti_to_npc(
            design.get("personality", ""),
            design.get("tags", []) or [design.get("role", "")],
        )
        npc.mbti_type = _mbti
        npc.ai_behavior = {
            "personality_traits": design.get("tags", []),
            "current_goal": "",
            "long_term_goal": design.get("long_term_goal", ""),
            "short_term_goals": [],
            "decision_style": mbti_to_decision_style(_mbti),
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
