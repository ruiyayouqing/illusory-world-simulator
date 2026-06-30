from __future__ import annotations
import json
import os
import uuid
import zipfile
import logging
from pathlib import Path
from datetime import datetime
from .schemas import (
    SaveManifest, SaveMeta, WorldState, PlayerState,
    NPCState, Stats, Social, Inventory, RelationEntry, PlayerMemory,
)
from .db.sqlite_db import WorldDB
from .db.chroma_db import MemoryStore
from .data.safe_io import load_json_safe, atomic_write_json  # [Bug] 存档损坏恢复


class SaveSlot:
    def __init__(self, slot_id: str, name: str, day: int, age: int,
                 location: str, description: str = ""):
        self.slot_id = slot_id
        self.name = name
        self.day = day
        self.age = age
        self.location = location
        self.description = description
        self.created_at = datetime.now().isoformat()


class TimelineManager:
    def __init__(self, world_dir: Path):
        self.world_dir = world_dir
        self.slots_dir = world_dir / "save_slots"
        self.slots_dir.mkdir(parents=True, exist_ok=True)
        self.slots_file = self.slots_dir / "slots.json"
        self.slots: list[dict] = self._load_slots()

    def _load_slots(self) -> list[dict]:
        # [Bug] 使用 load_json_safe 防止 slots.json 损坏时崩溃
        return load_json_safe(self.slots_file, default=[]) or []

    def _save_slots(self):
        # [Bug#26] 使用原子写入，防止进程崩溃时文件损坏
        atomic_write_json(self.slots_file, self.slots)

    def create_slot(self, name: str, meta: SaveMeta, world_state: WorldState,
                    player_state: PlayerState, npc_states: dict[str, NPCState],
                    description: str = "", narrative_history: list[dict] = None) -> str:
        slot_id = f"slot_{uuid.uuid4().hex[:8]}"
        slot_dir = self.slots_dir / slot_id
        slot_dir.mkdir(parents=True)

        state_data = {
            "meta": meta.model_dump(),
            "world_state": world_state.model_dump(),
            "player_state": player_state.model_dump(),
            "npc_states": {k: v.model_dump() for k, v in npc_states.items()},
            # [Bug] 必须把 narrative_history 一起写入 slot，否则加载 slot 后历史记录会丢失
            "narrative_history": list(narrative_history) if narrative_history else [],
        }
        from .data.safe_io import atomic_write_json
        atomic_write_json(slot_dir / "state.json", state_data)

        slot_info = {
            "slot_id": slot_id,
            "name": name,
            "day": world_state.current_day,
            "age": player_state.age,
            "location": player_state.location,
            "description": description,
            "created_at": datetime.now().isoformat(),
            "created_at_display": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        self.slots.append(slot_info)
        self._save_slots()
        return slot_id

    def load_slot(self, slot_id: str) -> dict | None:
        slot_dir = self.slots_dir / slot_id
        state_file = slot_dir / "state.json"
        if not state_file.exists():
            return None
        return json.loads(state_file.read_text(encoding="utf-8"))

    def list_slots(self) -> list[dict]:
        return self.slots

    def delete_slot(self, slot_id: str) -> bool:
        slot_dir = self.slots_dir / slot_id
        if slot_dir.exists():
            import shutil
            shutil.rmtree(slot_dir, ignore_errors=True)
        self.slots = [s for s in self.slots if s["slot_id"] != slot_id]
        self._save_slots()
        return True

    def export_timeline(self, slot_id: str, export_path: str) -> bool:
        slot_dir = self.slots_dir / slot_id
        if not slot_dir.exists():
            return False
        with zipfile.ZipFile(export_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in slot_dir.rglob("*"):
                if file.is_file():
                    arcname = str(file.relative_to(self.slots_dir))
                    zf.write(file, arcname)
        return True

    def import_timeline(self, import_path: str) -> str | None:
        with zipfile.ZipFile(import_path, "r") as zf:
            names = zf.namelist()
            slot_id = None
            for name in names:
                if name.endswith("state.json"):
                    parts = name.split("/")
                    if len(parts) >= 2:
                        slot_id = parts[0]
                        break
            if not slot_id:
                return None
            # [v8安全修复] ZipSlip防护：验证所有路径不逃逸目标目录
            for zip_info in zf.infolist():
                target = (self.slots_dir / zip_info.filename).resolve()
                if not str(target).startswith(str(self.slots_dir.resolve())):
                    logger.warning("ZipSlip attack blocked: %s", zip_info.filename)
                    raise ValueError(f"非法路径: {zip_info.filename}")
            zf.extractall(self.slots_dir)
            state = self.load_slot(slot_id)
            if state:
                ws = state.get("world_state", {})
                ps = state.get("player_state", {})
                slot_info = {
                    "slot_id": slot_id,
                    "name": f"导入-{ps.get('name', '未知')}",
                    "day": ws.get("current_day", 0),
                    "age": ps.get("age", 18),
                    "location": ps.get("location", ""),
                    "description": "从外部导入",
                    "created_at": datetime.now().isoformat(),
                }
                self.slots.append(slot_info)
                self._save_slots()
            return slot_id


logger = logging.getLogger("chronoverse")


SAVE_VERSION = "0.2.0"  # 当前存档格式版本


class SaveManager:
    def __init__(self, base_dir: str = "./saves"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.base_dir / "index.json"
        self.index = self._load_index()
        self._dbs: dict[str, WorldDB] = {}
        self._memories: dict[str, MemoryStore] = {}
        self._timelines: dict[str, TimelineManager] = {}
        # [v10.5] 嵌入函数，由 GameEngine 注入（set_embedding_function）
        self._embedding_function = None

    def set_embedding_function(self, ef) -> None:
        """[v10.5] 注入文本嵌入函数（SiliconFlow bge-m3 等）。
        已创建的 MemoryStore 不会受影响（仅影响新创建的）。"""
        self._embedding_function = ef

    def _load_index(self) -> dict:
        # [Bug] 使用 load_json_safe 防止 index.json 损坏时崩溃
        return load_json_safe(self.index_path, default={"saves": {}}) or {"saves": {}}

    def _save_index(self):
        # [Bug#26] 使用原子写入，防止进程崩溃时文件损坏
        atomic_write_json(self.index_path, self.index)

    def _read_json(self, path: Path) -> dict:
        # [Bug] 使用 load_json_safe 防止存档文件损坏时崩溃
        return load_json_safe(path, default={}) or {}

    def _write_json(self, path: Path, data: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        # [Bug#26] 使用原子写入，防止进程崩溃时文件损坏
        atomic_write_json(path, data)

    def list_saves(self) -> list[dict]:
        result = []
        for world_id, info in self.index.get("saves", {}).items():
            manifest_path = self.base_dir / world_id / "manifest.json"
            if manifest_path.exists():
                result.append(info)
        return result

    def save_exists(self, world_id: str) -> bool:
        return world_id in self.index.get("saves", {})

    def create_world(self, world_def_data: dict, player_data: dict,
                     npc_data_list: list[dict], world_name: str = "新世界") -> str:
        import uuid as _uuid
        world_id = f"{world_def_data.get('world_type', 'custom')}_{_uuid.uuid4().hex[:8]}"
        world_dir = self.base_dir / world_id

        (world_dir / "world_def").mkdir(parents=True)
        (world_dir / "state" / "npcs").mkdir(parents=True)
        (world_dir / "logs").mkdir(parents=True)
        (world_dir / "narrative").mkdir(parents=True)
        (world_dir / "memory").mkdir(parents=True)

        self._write_json(world_dir / "world_def" / "world.json", world_def_data)
        if "locations" in world_def_data:
            self._write_json(world_dir / "world_def" / "locations.json", world_def_data["locations"])
        if "map" in world_def_data:
            map_data = world_def_data["map"]
            if isinstance(map_data, dict):
                locations = list(map_data.keys())
                lines = ["," + ",".join(locations)]
                for loc in locations:
                    row = [str(map_data[loc].get(other, "")) for other in locations]
                    lines.append(loc + "," + ",".join(row))
                csv_text = "\n".join(lines)
            else:
                csv_text = str(map_data)
            (world_dir / "world_def" / "map.csv").write_text(csv_text, encoding="utf-8")

        if "roles" in world_def_data:
            roles_dir = world_dir / "world_def" / "roles"
            roles_dir.mkdir(parents=True)
            for role_id, role_info in world_def_data["roles"].items():
                role_dir = roles_dir / role_id
                role_dir.mkdir(parents=True)
                self._write_json(role_dir / "role_info.json", role_info)

        player_state = self._create_player_state(player_data)
        self._write_json(world_dir / "state" / "player.json", player_state.model_dump())

        for npc_data in npc_data_list:
            npc_state = self._create_npc_state(npc_data)
            npc_file = world_dir / "state" / "npcs" / f"{npc_state.agent_id}.json"
            self._write_json(npc_file, npc_state.model_dump())

        world_type = world_def_data.get("world_type", "custom")
        era_map = {
            "historical": ("", 1400),
            "fantasy": ("创世纪元", 3456),
            "xianxia": ("太初纪年", 9987),
            "wuxia": ("大明", 1400),
            "scifi": ("新纪元", 2087),
            "postapocalyptic": ("崩坏纪", 47),
            "modern": ("", 2025),
            "custom": ("创界年", 3456),
        }
        era_name, era_year = era_map.get(world_type, ("创界年", 3456))
        if world_def_data.get("era_name"):
            era_name = world_def_data["era_name"]
        if world_def_data.get("era_year"):
            era_year = world_def_data["era_year"]
        world_state = WorldState(
            world_id=world_id,
            world_type=world_type,
            world_name=world_name,
            description=world_def_data.get("description", ""),
            locations=world_def_data.get("locations", {}),
            era_name=era_name,
            era_year=era_year,
        )
        self._write_json(world_dir / "state" / "world_state.json", world_state.model_dump())

        meta = SaveMeta()
        self._write_json(world_dir / "state" / "meta.json", meta.model_dump())

        manifest = SaveManifest(
            world_id=world_id, world_name=world_name,
            world_type=world_def_data.get("world_type", "historical"),
            description=world_def_data.get("description", ""),
            player_name=player_state.name, player_age=player_state.age,
        )
        self._write_json(world_dir / "manifest.json", manifest.model_dump())

        db = WorldDB(str(world_dir / "logs" / "event_log.db"))
        db.close()

        self.index.setdefault("saves", {})[world_id] = {
            "world_id": world_id, "world_name": world_name,
            "world_type": world_def_data.get("world_type", "historical"),
            "description": world_def_data.get("description", ""),
            "created_at": manifest.created_at,
            "created_at_display": manifest.created_at_display,
            "player_name": player_state.name, "player_age": player_state.age,
        }
        self._save_index()
        return world_id

    def _create_player_state(self, data: dict) -> PlayerState:
        return PlayerState(
            agent_id=data.get("agent_id", "player_01"),
            name=data.get("name", "无名"),
            age=data.get("age", 18),
            birth_year=data.get("birth_year", 1398),
            max_age=data.get("max_age", 80),
            stats=Stats(**data.get("stats", {})) if data.get("stats") else Stats(),
            social=Social(**data.get("social", {})) if data.get("social") else Social(),
            tags=data.get("tags", ["普通人"]),
            inventory=Inventory(**data.get("inventory", {})) if data.get("inventory") else Inventory(),
            location=data.get("location", "village"),
            current_goal=data.get("current_goal", "活下去"),
        )

    def _create_npc_state(self, data: dict) -> NPCState:
        # 推断初始身份 role
        initial_role = data.get("role", "")
        if not initial_role:
            # 从 relation_to_player 推断（如"妻子""下属"）
            rel = data.get("relation_to_player", {})
            if isinstance(rel, dict) and rel.get("relation_type"):
                rt = rel["relation_type"]
                if rt not in ["陌生人", "熟人", "朋友"]:
                    initial_role = rt
        if not initial_role:
            # 从 tags 推断第一个非性格标签
            for tag in data.get("tags", []):
                if tag not in ["善良", "豪爽", "谨慎", "勇敢", "胆小", "聪明", "憨厚",
                              "普通人", "穿越者", "转世者", "前世记忆"]:
                    initial_role = tag
                    break

        npc = NPCState(
            agent_id=data["agent_id"], name=data["name"],
            age=data.get("age", 20), tags=data.get("tags", []),
            personality=data.get("personality", ""),
            speaking_style=data.get("speaking_style", ""),
            current_location=data.get("current_location", ""),
            role=initial_role,
        )
        if "stats" in data: npc.stats = Stats(**data["stats"])
        if "relation_to_player" in data: npc.relation_to_player = RelationEntry(**data["relation_to_player"])
        if "ai_behavior" in data: npc.ai_behavior = data["ai_behavior"]
        return npc

    def save_state(self, world_id: str, meta: SaveMeta, world_state: WorldState,
                   player_state: PlayerState, npc_states: dict[str, NPCState],
                   event_log: list[dict] = None, narrative: dict = None,
                   save_type: str = "auto"):
        world_dir = self.base_dir / world_id
        if not world_dir.exists():
            raise FileNotFoundError(f"存档 {world_id} 不存在")

        meta.save_type = save_type
        self._write_json(world_dir / "state" / "meta.json", meta.model_dump())
        self._write_json(world_dir / "state" / "world_state.json", world_state.model_dump())
        self._write_json(world_dir / "state" / "player.json", player_state.model_dump())

        for npc_id, npc_state in npc_states.items():
            npc_file = world_dir / "state" / "npcs" / f"{npc_id}.json"
            self._write_json(npc_file, npc_state.model_dump())

        # [Bug#27] 清理已删除 NPC 的旧文件，避免加载时复活
        npcs_dir = world_dir / "state" / "npcs"
        if npcs_dir.exists():
            current_npc_ids = set(npc_states.keys())
            for old_file in npcs_dir.glob("*.json"):
                old_id = old_file.stem
                if old_id not in current_npc_ids:
                    try:
                        old_file.unlink()
                    except OSError:
                        pass

        if event_log:
            db = self.get_db(world_id)
            for event in event_log:
                db.log_event(event)

        if narrative:
            chapter_num = len(list((world_dir / "narrative").glob("chapter_*.json"))) + 1
            chapter_file = world_dir / "narrative" / f"chapter_{chapter_num:03d}.json"
            self._write_json(chapter_file, narrative)

        manifest = self._read_json(world_dir / "manifest.json")
        manifest["last_saved_at"] = meta.save_timestamp
        manifest["last_saved_at_display"] = meta.save_time_display
        manifest["total_turns"] = meta.current_turn
        manifest["current_day"] = meta.current_day
        manifest["player_name"] = player_state.name
        manifest["player_age"] = player_state.age
        self._write_json(world_dir / "manifest.json", manifest)

        self.index["saves"][world_id].update({
            "last_saved_at": meta.save_timestamp,
            "last_saved_at_display": meta.save_time_display,
            "total_turns": meta.current_turn,
            "current_day": meta.current_day,
        })
        self._save_index()

    def load_state(self, world_id: str) -> dict:
        world_dir = self.base_dir / world_id
        if not world_dir.exists():
            raise FileNotFoundError(f"存档 {world_id} 不存在")

        # 版本迁移检查
        manifest = self._read_json(world_dir / "manifest.json")
        saved_version = manifest.get("version", "0.1.0")
        if saved_version != SAVE_VERSION:
            logger.info(f"存档 {world_id} 版本 {saved_version} → {SAVE_VERSION}，执行迁移...")
            self._migrate_save(world_id, saved_version, world_dir)

        meta = SaveMeta(**self._read_json(world_dir / "state" / "meta.json"))
        world_state = WorldState(**self._read_json(world_dir / "state" / "world_state.json"))
        player_state = PlayerState(**self._read_json(world_dir / "state" / "player.json"))

        npc_states = {}
        npcs_dir = world_dir / "state" / "npcs"
        if npcs_dir.exists():
            for npc_file in npcs_dir.glob("*.json"):
                npc_data = self._read_json(npc_file)
                if npc_data:
                    npc = NPCState(**npc_data)
                    npc_states[npc.agent_id] = npc

        manifest = SaveManifest(**self._read_json(world_dir / "manifest.json"))
        db = self.get_db(world_id)
        recent_actions = db.get_recent_actions(50)
        memory = self.get_memory(world_id)

        return {
            "meta": meta, "world_state": world_state,
            "player_state": player_state, "npc_states": npc_states,
            "manifest": manifest, "recent_actions": recent_actions,
            "memory": memory,
        }

    def get_timeline(self, world_id: str) -> TimelineManager:
        if world_id not in self._timelines:
            world_dir = self.base_dir / world_id
            self._timelines[world_id] = TimelineManager(world_dir)
        return self._timelines[world_id]

    def get_db(self, world_id: str) -> WorldDB:
        if world_id not in self._dbs:
            db_path = str(self.base_dir / world_id / "logs" / "event_log.db")
            self._dbs[world_id] = WorldDB(db_path)
        return self._dbs[world_id]

    def get_memory(self, world_id: str) -> MemoryStore:
        if world_id not in self._memories:
            mem_dir = str(self.base_dir / world_id / "memory")
            self._memories[world_id] = MemoryStore(
                mem_dir, f"{world_id}_memory",
                embedding_function=self._embedding_function,
            )
        return self._memories[world_id]

    def delete_save(self, world_id: str) -> bool:
        world_dir = self.base_dir / world_id
        if not world_dir.exists():
            return False
        if world_id in self._dbs:
            self._dbs[world_id].close()
            del self._dbs[world_id]
        if world_id in self._memories:
            self._memories[world_id].close()
            del self._memories[world_id]
        self._timelines.pop(world_id, None)
        import gc, time, shutil
        gc.collect()
        time.sleep(0.5)
        # 跨平台兼容：优先用 shutil，失败后用系统命令兜底
        try:
            shutil.rmtree(str(world_dir), ignore_errors=False)
        except Exception as e:
            logger.warning("shutil.rmtree failed, trying cmd fallback: %s", e)
            import subprocess
            subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(world_dir)],
                           capture_output=True, timeout=10)
        if world_dir.exists():
            shutil.rmtree(str(world_dir), ignore_errors=True)
        self.index.get("saves", {}).pop(world_id, None)
        self._save_index()
        return True

    def export_save(self, world_id: str, export_path: str) -> bool:
        world_dir = self.base_dir / world_id
        if not world_dir.exists():
            return False
        with zipfile.ZipFile(export_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in world_dir.rglob("*"):
                if file.is_file():
                    arcname = str(file.relative_to(self.base_dir))
                    zf.write(file, arcname)
        return True

    def _migrate_save(self, world_id: str, from_version: str, world_dir: Path):
        """存档版本迁移：从旧版本升级到新版本"""
        migrations = []

        # 0.1.0 → 0.2.0: 更新 manifest version，NPC schema 增加了 role/role_history/relation_history
        if from_version <= "0.1.0":
            migrations.append("0.1.0→0.2.0: NPC schema 升级 (增加身份追踪字段)")

            # 给现有NPC补充初始role
            npcs_dir = world_dir / "state" / "npcs"
            if npcs_dir.exists():
                for npc_file in npcs_dir.glob("*.json"):
                    npc_data = self._read_json(npc_file)
                    if npc_data and not npc_data.get("role"):
                        # 从 tags 推断
                        for tag in npc_data.get("tags", []):
                            if tag not in ["善良", "豪爽", "谨慎", "勇敢", "胆小",
                                          "聪明", "憨厚", "普通人", "穿越者"]:
                                npc_data["role"] = tag
                                break
                        if not npc_data.get("role"):
                            npc_data["role"] = ""
                        npc_data.setdefault("role_history", [])
                        npc_data.setdefault("relation_history", [])
                        self._write_json(npc_file, npc_data)

            # 更新 manifest 版本号
            manifest_path = world_dir / "manifest.json"
            manifest = self._read_json(manifest_path)
            manifest["version"] = SAVE_VERSION
            self._write_json(manifest_path, manifest)

        # 未来版本迁移在此继续添加...

        for m in migrations:
            logger.info(f"  ✅ {m}")

    def close_all(self):
        for db in self._dbs.values():
            db.close()
        self._dbs.clear()
        for mem in self._memories.values():
            mem.close()
        self._memories.clear()
        self._timelines.clear()
