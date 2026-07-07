from __future__ import annotations
from .schemas import PlayerState, WorldState
from .llm.base_llm import BaseLLM
from .prompt_utils import resolve_location_name  # [Bug] location code → display name


class PlayerMemoir:
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.entries: list[dict] = []
        self.chapters: list[dict] = []
        self.current_chapter: dict | None = None

    def add_entry(self, day: int, entry_type: str, title: str,
                  content: str, importance: str = "normal",
                  emotion: str = "neutral", npc_involved: list[str] = None):
        self.entries.append({
            "day": day,
            "type": entry_type,
            "title": title,
            "content": content,
            "importance": importance,
            "emotion": emotion,
            "npc_involved": npc_involved or [],
        })

    def record_battle(self, player: PlayerState, enemy: str,
                      result: str, day: int, wounds: str = ""):
        importance = "high" if "死" in result or "重伤" in wounds else "normal"
        self.add_entry(day, "battle", f"与{enemy}交战",
                       f"与{enemy}交手，{result}。{wounds}" if wounds else f"与{enemy}交手，{result}",
                       importance, "紧张", [enemy])

    def record_relationship(self, player: PlayerState, npc_name: str,
                            event: str, day: int, favor_change: int = 0):
        emotion = "开心" if favor_change > 0 else "难过" if favor_change < 0 else "平静"
        importance = "high" if abs(favor_change) >= 20 else "normal"
        self.add_entry(day, "relationship", f"与{npc_name}",
                       event, importance, emotion, [npc_name])

    def record_world_event(self, event_type: str, description: str, day: int):
        self.add_entry(day, "world", f"世界事件",
                       description, "normal", "震撼")

    def record_age_milestone(self, player: PlayerState, milestone: str, day: int):
        self.add_entry(day, "age", milestone,
                       f"第{player.age}岁，{milestone}", "high", "感慨")

    def record_death(self, player: PlayerState, cause: str, day: int, world_state=None):
        self.add_entry(day, "death", "死亡",
                       f"在{resolve_location_name(player.location, world_state)}，{cause}", "high", "悲伤")

    def generate_chapter(self, player: PlayerState, world_state: WorldState,
                         start_day: int, end_day: int) -> str:
        relevant = [e for e in self.entries if start_day <= e["day"] <= end_day]
        if not relevant:
            return ""

        entries_text = "\n".join([
            f"[第{e['day']}天 | {e['type']} | {e.get('emotion', '平静')}] {e['title']}: {e['content'][:150]}"
            for e in relevant
        ])

        prompt = f"""你是一位传记作家。将以下事件编写成一段沉浸式的第一人称回忆录。

【主角信息】
姓名: {player.name}，{player.age}岁
标签: {', '.join(player.tags)}
位置: {resolve_location_name(player.location, world_state)}  # [Bug] location code → display name
金币: {player.social.gold} 声望: {player.social.reputation}

【事件记录】（第{start_day}天到第{end_day}天）
{entries_text}

【写作要求】
1. 第一人称"我"的回忆录风格
2. 融入情感：怀念、遗憾、骄傲、悲伤、释然
3. 用具体细节让读者身临其境（气味、声音、触感、光影）
4. 可以穿插内心独白和感悟
5. 300-500字
6. 结尾可以是总结、展望、或留白

直接输出文本。"""
        return self.llm.chat(prompt, temperature=0.9, max_tokens=1000)

    def generate_full_memoir(self, player: PlayerState, world_state: WorldState) -> str:
        if not self.entries:
            return self._empty_memoir(player)

        chapters = []
        years = {}
        for entry in self.entries:
            year = (entry["day"] // 365) + player.age - (world_state.current_day // 365)
            if year not in years:
                years[year] = []
            years[year].append(entry)

        for year in sorted(years.keys()):
            entries = years[year]
            start_day = entries[0]["day"]
            end_day = entries[-1]["day"]
            chapter = self.generate_chapter(player, world_state, start_day, end_day)
            if chapter:
                age_at_year = year
                chapters.append(f"## 第{age_at_year}岁 · 第{year}年\n\n{chapter}")

        header = f"# {player.name}回忆录\n\n*写于第{world_state.current_day}天，{resolve_location_name(player.location, world_state)}*\n\n"  # [Bug] location code → display name
        return header + "\n\n---\n\n".join(chapters)

    def generate_current_reflection(self, player: PlayerState,
                                    world_state: WorldState) -> str:
        recent = self.entries[-10:] if self.entries else []
        if not recent:
            return f"我来到这个世界已经{world_state.current_day}天了。一切才刚刚开始..."

        entries_text = "\n".join([
            f"- {e['title']}: {e['content'][:80]}"
            for e in recent
        ])

        prompt = f"""用第一人称写一段当前的人生感悟。

【主角】{player.name}，{player.age}岁，{player.social.position}
【近期经历】
{entries_text}
【当前】在{resolve_location_name(player.location, world_state)}，{world_state.season}，{world_state.weather}  # [Bug] location code → display name

【要求】100-200字，第一人称，有感悟有情绪。
直接输出。"""
        return self.llm.chat(prompt, temperature=0.9)

    def _empty_memoir(self, player: PlayerState) -> str:
        return f"""# {player.name}回忆录

我经历的还不够多，还没有什么值得回忆的事情。"""

    def get_stats(self) -> dict:
        return {
            "total_entries": len(self.entries),
            "by_type": {
                t: len([e for e in self.entries if e["type"] == t])
                for t in set(e["type"] for e in self.entries) if self.entries
            },
            "high_importance": len([e for e in self.entries if e["importance"] == "high"]),
            "emotions": {
                emo: len([e for e in self.entries if e.get("emotion") == emo])
                for emo in set(e.get("emotion", "") for e in self.entries) if self.entries
            },
        }

    def to_dict(self) -> dict:
        # 序列化回忆录条目
        return {"entries": self.entries}

    def from_dict(self, data: dict):
        self.entries = data.get("entries", [])
