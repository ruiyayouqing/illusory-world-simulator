from __future__ import annotations
import json
from pathlib import Path
from .schemas import PlayerState, WorldState
from .llm.base_llm import BaseLLM


MAX_LIVES = 100


class LifeRecord:
    def __init__(self, life_number: int, player_name: str, start_day: int):
        self.life_number = life_number
        self.player_name = player_name
        self.start_day = start_day
        self.end_day: int | None = None
        self.death_cause: str = ""
        self.death_age: int = 0
        self.achievements: list[str] = []
        self.key_events: list[dict] = []
        self.inherited_knowledge: list[str] = []
        self.tags_gained: list[str] = []
        self.sealed: bool = False

    def to_dict(self) -> dict:
        return {
            "life_number": self.life_number,
            "player_name": self.player_name,
            "start_day": self.start_day,
            "end_day": self.end_day,
            "death_cause": self.death_cause,
            "death_age": self.death_age,
            "achievements": self.achievements,
            "key_events": self.key_events,
            "inherited_knowledge": self.inherited_knowledge,
            "tags_gained": self.tags_gained,
            "sealed": self.sealed,
        }


class HundredLifeBook:
    def __init__(self, llm: BaseLLM, save_dir: str = "./saves"):
        self.llm = llm
        self.save_dir = Path(save_dir)
        self.lives: list[LifeRecord] = []
        self.current_life: int = 0
        self.inherited_tags: list[str] = []
        self.inherited_knowledge: list[str] = []
        self.karma_level: int = 0
        self.observed_by: list[str] = []
        self.failed_saves: list[str] = []
        self.pages_remaining: int = MAX_LIVES

    def start_new_life(self, player_name: str, day: int) -> LifeRecord | None:
        if self.pages_remaining <= 0:
            return None
        self.current_life += 1
        self.pages_remaining -= 1
        life = LifeRecord(self.current_life, player_name, day)
        self.lives.append(life)
        return life

    def seal_current_life(self, player: PlayerState, world_state: WorldState,
                          cause: str) -> dict:
        if not self.lives:
            return {"sealed": False}

        current = self.lives[-1]
        current.end_day = world_state.current_day
        current.death_cause = cause
        current.death_age = player.age
        current.sealed = True

        current.achievements = self._extract_achievements(player)
        current.inherited_knowledge = self._extract_knowledge(player, world_state)
        current.tags_gained = list(player.tags)

        self.inherited_tags.extend(["转世者", f"第{self.current_life}世记忆"])
        self.inherited_knowledge.extend(current.inherited_knowledge)

        self.karma_level = min(100, self.karma_level + 5 + self.current_life * 2)

        self._check_karma_observers(world_state)

        self._save_book()

        return {
            "sealed": True,
            "life_number": self.current_life,
            "pages_remaining": self.pages_remaining,
            "karma_level": self.karma_level,
            "observers": self.observed_by,
        }

    def _check_karma_observers(self, world_state: WorldState):
        world_type = world_state.world_type if world_state else "custom"

        if world_type in ["historical", "modern"]:
            self._check_mortal_karma(world_state)
        elif world_type in ["wuxia", "xianxia"]:
            self._check_cultivation_karma(world_state)
        elif world_type in ["fantasy", "magic"]:
            self._check_magic_karma(world_state)
        elif world_type == "scifi":
            self._check_scifi_karma(world_state)
        elif world_type == "postapocalyptic":
            self._check_apocalyptic_karma(world_state)
        else:
            self._check_mortal_karma(world_state)

    def _check_mortal_karma(self, world_state: WorldState):
        if self.karma_level >= 30 and "乡绅望族" not in self.observed_by:
            self.observed_by.append("乡绅望族")
        if self.karma_level >= 50 and "地方官府" not in self.observed_by:
            self.observed_by.append("地方官府")
        if self.karma_level >= 70 and "朝廷暗探" not in self.observed_by:
            self.observed_by.append("朝廷暗探")
        if self.karma_level >= 90 and "锦衣卫" not in self.observed_by:
            self.observed_by.append("锦衣卫")

    def _check_cultivation_karma(self, world_state: WorldState):
        if self.karma_level >= 20 and "同门师兄弟" not in self.observed_by:
            self.observed_by.append("同门师兄弟")
        if self.karma_level >= 40 and "长老" not in self.observed_by:
            self.observed_by.append("长老")
        if self.karma_level >= 60 and "宗主" not in self.observed_by:
            self.observed_by.append("宗主")
        if self.karma_level >= 80 and "渡劫期老祖" not in self.observed_by:
            self.observed_by.append("渡劫期老祖")
        if self.karma_level >= 95 and "天道意志" not in self.observed_by:
            self.observed_by.append("天道意志")

    def _check_magic_karma(self, world_state: WorldState):
        if self.karma_level >= 25 and "教会审判官" not in self.observed_by:
            self.observed_by.append("教会审判官")
        if self.karma_level >= 50 and "大魔导师" not in self.observed_by:
            self.observed_by.append("大魔导师")
        if self.karma_level >= 75 and "神殿长老" not in self.observed_by:
            self.observed_by.append("神殿长老")
        if self.karma_level >= 90 and "古神" not in self.observed_by:
            self.observed_by.append("古神")

    def _check_scifi_karma(self, world_state: WorldState):
        if self.karma_level >= 20 and "AI监控系统" not in self.observed_by:
            self.observed_by.append("AI监控系统")
        if self.karma_level >= 45 and "企业安全部" not in self.observed_by:
            self.observed_by.append("企业安全部")
        if self.karma_level >= 70 and "政府特工" not in self.observed_by:
            self.observed_by.append("政府特工")
        if self.karma_level >= 90 and "时空管理局" not in self.observed_by:
            self.observed_by.append("时空管理局")

    def _check_apocalyptic_karma(self, world_state: WorldState):
        if self.karma_level >= 25 and "变异兽群" not in self.observed_by:
            self.observed_by.append("变异兽群")
        if self.karma_level >= 50 and "觉醒者联盟" not in self.observed_by:
            self.observed_by.append("觉醒者联盟")
        if self.karma_level >= 75 and "辐射源" not in self.observed_by:
            self.observed_by.append("辐射源")
        if self.karma_level >= 90 and "深渊领主" not in self.observed_by:
            self.observed_by.append("深渊领主")

    def can_revive_others(self) -> bool:
        return False

    def get_revival_restriction(self) -> str:
        return "百世书仅能回溯主角自身的时间线。他人的生死已成定数，哪怕某一世救下某人，下一轮回此人依旧会按原命运死亡。不存在靠轮回救人的可能。"

    def try_save_npc(self, npc_name: str, day: int) -> dict:
        self.failed_saves.append({
            "npc": npc_name,
            "day": day,
            "reason": "百世书无法改变他人命运",
        })
        return {
            "success": False,
            "npc": npc_name,
            "message": f"你试图改变{npc_name}的命运，但百世书的力量无法触及他人的时间线。{npc_name}的命运依旧按照原本的轨迹运转。",
        }

    def get_karma_narrative(self, world_type: str = "custom") -> str:
        if self.karma_level < 20:
            return ""

        if world_type in ["historical", "modern"]:
            return self._mortal_karma_narrative()
        elif world_type in ["wuxia", "xianxia"]:
            return self._cultivation_karma_narrative()
        elif world_type in ["fantasy", "magic"]:
            return self._magic_karma_narrative()
        elif world_type == "scifi":
            return self._scifi_karma_narrative()
        elif world_type == "postapocalyptic":
            return self._apocalyptic_karma_narrative()
        return self._mortal_karma_narrative()

    def _mortal_karma_narrative(self) -> str:
        if self.karma_level < 30:
            return ""
        elif self.karma_level < 50:
            return "你注意到周围的乡绅望族开始频繁打听你的来历，那种若即若离的关注让你不安。"
        elif self.karma_level < 70:
            return "地方官府的人出现在你常去的茶楼，看似闲聊，实则在试探你的底细。你必须更加谨慎。"
        elif self.karma_level < 90:
            return "一群穿着飞鱼服的人在城中出没，锦衣卫的暗探已经注意到了你身上的异常。每一步都如履薄冰。"
        else:
            return "锦衣卫的密探已经盯上了你。你能感觉到，他们已经知道了——你不属于这个时间线。一场无法避免的追捕即将开始。"

    def _cultivation_karma_narrative(self) -> str:
        if self.karma_level < 20:
            return ""
        elif self.karma_level < 40:
            return "同门师兄弟看你的目光变得异样——仿佛你身上有种不该存在的气息。"
        elif self.karma_level < 60:
            return "长老将你叫去问话，那双仿佛能看穿一切的眼睛让你心惊胆战。你必须隐藏实力。"
        elif self.karma_level < 80:
            return "宗主的神识扫过你的身体，你感到一阵心悸。他似乎察觉到了时间的异常。"
        elif self.karma_level < 95:
            return "渡劫期老祖的目光锁定了你。你能感觉到，他已经知道了——你不属于这个时间线。"
        else:
            return "天道的雷劫开始在你头顶聚集，仿佛要将你这个时间线的bug彻底抹除。"

    def _magic_karma_narrative(self) -> str:
        if self.karma_level < 25:
            return ""
        elif self.karma_level < 50:
            return "教会的审判官开始调查你，他们手中的圣典散发着微弱的光芒——那是探测时间异常的圣器。"
        elif self.karma_level < 75:
            return "大魔导师在你面前停下脚步，他浑浊的眼睛突然变得锐利：'你身上的魔力波动...不对。'"
        else:
            return "古神的低语在你耳边响起，它已经注意到了这个时间线的异常波动。"

    def _scifi_karma_narrative(self) -> str:
        if self.karma_level < 20:
            return ""
        elif self.karma_level < 45:
            return "你的生物芯片收到一条异常警报——AI监控系统检测到了你身上的时间戳异常。"
        elif self.karma_level < 70:
            return "企业安全部的人出现在你的公寓门口，他们手里拿着一份标注着你名字的文件。"
        else:
            return "时空管理局的特工已经锁定了你的坐标。他们要来'修正'这个时间线的异常。"

    def _apocalyptic_karma_narrative(self) -> str:
        if self.karma_level < 25:
            return ""
        elif self.karma_level < 50:
            return "附近的变异兽群开始向你的方向聚集，它们似乎能感觉到你身上散发的异常能量。"
        elif self.karma_level < 75:
            return "觉醒者联盟的人找到了你，他们手中的探测器疯狂作响：'你身上的时间辐射超标了。'"
        else:
            return "深渊的裂缝开始在你脚下蔓延，领主的目光穿越了维度，锁定了你这个时间线的异常存在。"

    def _extract_achievements(self, player: PlayerState) -> list[str]:
        achievements = []
        if player.social.gold >= 1000: achievements.append("富甲一方")
        if player.social.reputation >= 80: achievements.append("名扬天下")
        if player.stats.strength >= 20: achievements.append("武艺超群")
        if player.stats.intelligence >= 20: achievements.append("学富五车")
        if player.stats.magic >= 20: achievements.append("法力高强")
        if any(r.favor >= 90 for r in player.relations.values()): achievements.append("收获真爱")
        if player.age >= 60: achievements.append("长寿善终")
        if not achievements: achievements.append("平凡一生")
        return achievements

    def _extract_knowledge(self, player: PlayerState, world_state: WorldState) -> list[str]:
        knowledge = []
        if world_state.world_name: knowledge.append(f"世界: {world_state.world_name}")
        knowledge.append(f"势力: {', '.join(world_state.factions.keys())}")
        for npc_id, rel in player.relations.items():
            if rel.favor >= 60:
                knowledge.append(f"友好NPC: {npc_id} (好感{rel.favor})")
        for tag in player.tags:
            if tag not in ["普通人", "穿越者"]:
                knowledge.append(f"经验标签: {tag}")
        return knowledge[:10]

    def get_life_previews(self) -> list[dict]:
        previews = []
        for life in self.lives:
            preview = {
                "number": life.life_number,
                "name": life.player_name,
                "sealed": life.sealed,
                "death_cause": life.death_cause if life.sealed else "进行中",
                "death_age": life.death_age if life.sealed else None,
                "achievements": life.achievements if life.sealed else [],
                "start_day": life.start_day,
                "end_day": life.end_day,
            }
            previews.append(preview)
        return previews

    def get_sealed_lives(self) -> list[dict]:
        return [l.to_dict() for l in self.lives if l.sealed]

    def generate_book_narrative(self) -> str:
        if not self.lives:
            return "百世书还是一片空白..."

        prompt = f"""为一本"百世书"写一段引言。这本书记录了一个灵魂的无数次轮回。

【轮回记录】
共{len(self.lives)}世（剩余{self.pages_remaining}页）
当前第{self.current_life}世
因果值: {self.karma_level}/100
被观测者: {', '.join(self.observed_by) or '无'}

【各世摘要】
{chr(10).join([
    f"第{l.life_number}世: {l.player_name}, {l.death_age}岁死于{l.death_cause}, 成就: {', '.join(l.achievements)}"
    for l in self.lives
])}

【要求】
1. 用古朴神秘的语气
2. 暗示每一世的意义和代价
3. 提到书页在减少
4. 100-200字

直接输出文本。"""
        return self.llm.chat(prompt, temperature=0.9)

    def generate_life_chapter(self, life: LifeRecord) -> str:
        if not life.sealed:
            return f"第{life.life_number}世: {life.player_name}，仍在世间行走..."

        prompt = f"""将这一世的经历写成百世书中的一章。

【第{life.life_number}世】（第{MAX_LIVES - life.life_number + 1}页/共{MAX_LIVES}页）
姓名: {life.player_name}
存活: 第{life.start_day}天到第{life.end_day}天
死因: {life.death_cause}
死亡年龄: {life.death_age}岁
成就: {', '.join(life.achievements)}
获得标签: {', '.join(life.tags_gained[:5])}
继承知识: {', '.join(life.inherited_knowledge[:3])}

【要求】
1. 用第三人称史书记载风格
2. 150-250字
3. 客观记录，但带有感叹
4. 结尾写"此世已封，书页黯淡"

直接输出文本。"""
        return self.llm.chat(prompt, temperature=0.85)

    def get_inherited_tags(self) -> list[str]:
        return list(set(self.inherited_tags))

    def get_inherited_knowledge(self) -> list[str]:
        return list(set(self.inherited_knowledge))

    def get_total_lives(self) -> int:
        return len(self.lives)

    def _save_book(self):
        book_dir = self.save_dir / "hundred_life_book"
        book_dir.mkdir(parents=True, exist_ok=True)
        book_data = {
            "current_life": self.current_life,
            "pages_remaining": self.pages_remaining,
            "karma_level": self.karma_level,
            "observed_by": self.observed_by,
            "failed_saves": self.failed_saves,
            "inherited_tags": self.inherited_tags,
            "inherited_knowledge": self.inherited_knowledge,
            "lives": [l.to_dict() for l in self.lives],
        }
        from .data.safe_io import atomic_write_json
        atomic_write_json(book_dir / "book.json", book_data)

    def load_book(self):
        book_file = self.save_dir / "hundred_life_book" / "book.json"
        if not book_file.exists(): return
        data = json.loads(book_file.read_text(encoding="utf-8"))
        self.current_life = data.get("current_life", 0)
        self.pages_remaining = data.get("pages_remaining", MAX_LIVES)
        self.karma_level = data.get("karma_level", 0)
        self.observed_by = data.get("observed_by", [])
        self.failed_saves = data.get("failed_saves", [])
        self.inherited_tags = data.get("inherited_tags", [])
        self.inherited_knowledge = data.get("inherited_knowledge", [])
        self.lives = []
        for life_data in data.get("lives", []):
            life = LifeRecord(life_data["life_number"], life_data["player_name"], life_data["start_day"])
            life.end_day = life_data.get("end_day")
            life.death_cause = life_data.get("death_cause", "")
            life.death_age = life_data.get("death_age", 0)
            life.achievements = life_data.get("achievements", [])
            life.key_events = life_data.get("key_events", [])
            life.inherited_knowledge = life_data.get("inherited_knowledge", [])
            life.tags_gained = life_data.get("tags_gained", [])
            life.sealed = life_data.get("sealed", False)
            self.lives.append(life)
