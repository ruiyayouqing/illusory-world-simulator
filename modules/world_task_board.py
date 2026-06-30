"""
[v10] 世界任务板 — 轻量 Kanban 系统

核心思路（借鉴 Hermes Agent 的 Kanban 持久化任务板）：
  1. 世界事件自动生成任务（如"守城"、"筹集军需"、"调查阴谋"）
  2. NPC 根据角色/性格/关系认领任务
  3. 任务有状态流转：待办 → 进行中 → 完成/失败
  4. 任务结果反馈到世界状态（影响势力、经济、关系等）
  5. 支持任务依赖（完成A才能开始B）

简化设计：
  - 不需要跨进程，纯内存 + 存档持久化
  - 任务类型：world_event / faction / personal / chain
  - 最多同时存在 20 个活跃任务
"""
from __future__ import annotations
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .schemas import NPCState, WorldState, PlayerState

logger = logging.getLogger("chronoverse.task_board")


class TaskStatus:
    PENDING = "pending"       # 待认领
    READY = "ready"           # 条件满足，可执行
    RUNNING = "running"       # 执行中
    COMPLETED = "completed"   # 完成
    FAILED = "failed"         # 失败
    EXPIRED = "expired"       # 过期


@dataclass
class WorldTask:
    """一个世界任务"""
    task_id: str
    title: str
    description: str
    task_type: str           # "world_event" / "faction" / "personal" / "chain"
    status: str = TaskStatus.PENDING
    priority: int = 5        # 1-10, 越高越重要
    created_day: int = 0
    deadline_day: int = 0    # 0 = 无截止日期
    assigned_to: str = ""    # NPC agent_id
    assigned_name: str = ""  # NPC 名字
    required_role: str = ""  # 需要的角色类型
    required_tags: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)  # 依赖的任务ID
    reward: dict = field(default_factory=dict)           # 完成奖励
    progress: int = 0        # 0-100
    result: str = ""         # 完成/失败的结果描述
    world_effects: dict = field(default_factory=dict)    # 对世界的影响

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "task_type": self.task_type,
            "status": self.status,
            "priority": self.priority,
            "created_day": self.created_day,
            "deadline_day": self.deadline_day,
            "assigned_to": self.assigned_to,
            "assigned_name": self.assigned_name,
            "required_role": self.required_role,
            "required_tags": self.required_tags,
            "depends_on": self.depends_on,
            "reward": self.reward,
            "progress": self.progress,
            "result": self.result,
            "world_effects": self.world_effects,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WorldTask":
        return cls(**d)


class WorldTaskBoard:
    """
    [v10] 世界任务板 — 轻量级多 NPC 协调系统

    功能：
    - 世界事件自动产生任务
    - NPC 自动认领匹配的任务
    - 任务执行与世界状态联动
    - 支持任务依赖链
    """

    MAX_ACTIVE_TASKS = 20

    def __init__(self):
        self.tasks: dict[str, WorldTask] = {}  # task_id -> WorldTask
        self._task_counter = 0
        self._completed_history: list[dict] = []  # 已完成任务的历史

    def create_task(self, title: str, description: str,
                    task_type: str = "world_event",
                    priority: int = 5, created_day: int = 0,
                    deadline_day: int = 0,
                    required_role: str = "",
                    required_tags: list[str] = None,
                    depends_on: list[str] = None,
                    reward: dict = None,
                    world_effects: dict = None) -> WorldTask:
        """创建一个新任务"""
        # 容量检查
        active_count = len([
            t for t in self.tasks.values()
            if t.status in (TaskStatus.PENDING, TaskStatus.READY, TaskStatus.RUNNING)
        ])
        if active_count >= self.MAX_ACTIVE_TASKS:
            # 优先移除最旧的 pending 任务
            oldest_pending = min(
                [t for t in self.tasks.values() if t.status == TaskStatus.PENDING],
                key=lambda t: t.created_day,
                default=None,
            )
            if oldest_pending:
                del self.tasks[oldest_pending.task_id]
            else:
                # 没有 pending 任务可移除，拒绝创建新任务
                logger.warning("Task board full (%d/%d), no pending tasks to evict",
                               active_count, self.MAX_ACTIVE_TASKS)
                return None

        self._task_counter += 1
        task = WorldTask(
            task_id=f"task_{self._task_counter:04d}",
            title=title,
            description=description,
            task_type=task_type,
            priority=priority,
            created_day=created_day,
            deadline_day=deadline_day,
            required_role=required_role,
            required_tags=required_tags or [],
            depends_on=depends_on or [],
            reward=reward or {},
            world_effects=world_effects or {},
        )
        self.tasks[task.task_id] = task
        logger.info("Task created: %s - %s (priority=%d)", task.task_id, title, priority)
        return task

    def auto_assign_tasks(self, npc_states: dict[str, "NPCState"],
                          world_state: "WorldState") -> list[dict]:
        """
        自动为待办任务分配 NPC。

        匹配规则：
        1. 检查任务依赖是否满足
        2. 按优先级排序待办任务
        3. 匹配 NPC 角色/标签
        4. 每个 NPC 最多同时承担 2 个任务
        """
        assignments = []

        # 先检查依赖，将满足条件的 pending 升级为 ready
        self._check_dependencies()

        # 获取所有待认领的 ready 任务
        ready_tasks = [
            t for t in self.tasks.values()
            if t.status == TaskStatus.READY
        ]
        ready_tasks.sort(key=lambda t: t.priority, reverse=True)

        # 统计每个 NPC 的活跃任务数
        npc_task_count: dict[str, int] = {}
        for t in self.tasks.values():
            if t.status == TaskStatus.RUNNING and t.assigned_to:
                npc_task_count[t.assigned_to] = npc_task_count.get(t.assigned_to, 0) + 1

        for task in ready_tasks:
            if task.assigned_to:
                continue  # 已分配

            best_npc = None
            best_score = -1

            for npc_id, npc in npc_states.items():
                # 检查任务数上限
                if npc_task_count.get(npc_id, 0) >= 2:
                    continue

                score = self._calculate_match_score(npc, task)
                if score > best_score:
                    best_score = score
                    best_npc = (npc_id, npc)

            if best_npc and best_score >= 0.3:
                npc_id, npc = best_npc
                task.assigned_to = npc_id
                task.assigned_name = npc.name
                task.status = TaskStatus.RUNNING
                npc_task_count[npc_id] = npc_task_count.get(npc_id, 0) + 1
                assignments.append({
                    "task_id": task.task_id,
                    "title": task.title,
                    "npc_id": npc_id,
                    "npc_name": npc.name,
                    "match_score": round(best_score, 2),
                })

        return assignments

    def advance_tasks(self, npc_states: dict[str, "NPCState"],
                      world_state: "WorldState",
                      current_day: int) -> list[dict]:
        """
        推进所有运行中的任务。
        根据 NPC 的行动和世界状态更新任务进度。
        """
        results = []

        for task in list(self.tasks.values()):
            if task.status != TaskStatus.RUNNING:
                continue

            # 检查截止日期
            if task.deadline_day > 0 and current_day > task.deadline_day:
                task.status = TaskStatus.FAILED
                task.result = "超过截止日期"
                results.append(self._make_result(task, "expired"))
                continue

            # 根据任务类型计算进度
            progress_delta = self._calculate_progress(task, npc_states, world_state)
            task.progress = min(100, task.progress + progress_delta)

            if task.progress >= 100:
                task.status = TaskStatus.COMPLETED
                task.result = "任务完成"
                self._apply_world_effects(task, world_state)
                self._completed_history.append(task.to_dict())
                results.append(self._make_result(task, "completed"))
                logger.info("Task completed: %s - %s", task.task_id, task.title)
                # 触发 on_task_completed 钩子
                try:
                    from .registry import trigger_hook
                    trigger_hook("on_task_completed", task=task.to_dict())
                except Exception:
                    pass
            elif progress_delta > 0:
                results.append(self._make_result(task, "progress"))

        return results

    def generate_tasks_from_event(self, event_description: str,
                                   event_type: str, impact_level: int,
                                   affected_locations: list[str],
                                   current_day: int,
                                   world_state: "WorldState") -> list[WorldTask]:
        """
        从世界事件自动生成任务。
        高影响力事件产生更多/更重要的任务。
        """
        tasks = []
        num_tasks = min(3, max(1, impact_level // 3))

        # 根据事件类型生成不同任务
        if event_type in ("war", "conflict", "invasion"):
            tasks.append(self.create_task(
                title=f"应对{event_description[:20]}",
                description=f"世界发生了{event_description[:100]}，需要有人去处理。",
                task_type="world_event",
                priority=min(10, impact_level + 2),
                created_day=current_day,
                deadline_day=current_day + 7,
                required_role="武将",
                required_tags=["战斗", "武力"],
                reward={"reputation": 10, "gold": 50},
                world_effects={"crisis_delta": -1},
            ))
            if num_tasks >= 2:
                tasks.append(self.create_task(
                    title=f"安抚民心",
                    description=f"由于{event_description[:30]}，百姓人心惶惶。",
                    task_type="world_event",
                    priority=impact_level,
                    created_day=current_day,
                    deadline_day=current_day + 5,
                    required_role="官员",
                    required_tags=["治理", "口才"],
                    reward={"reputation": 5},
                    world_effects={"stability_delta": 1},
                ))

        elif event_type in ("plague", "disaster", "famine"):
            tasks.append(self.create_task(
                title=f"赈灾救援",
                description=f"{event_description[:30]}，需要组织救援。",
                task_type="world_event",
                priority=min(10, impact_level + 1),
                created_day=current_day,
                deadline_day=current_day + 10,
                required_tags=["医术", "治理"],
                reward={"reputation": 15},
                world_effects={"crisis_delta": -2},
            ))

        elif event_type in ("trade", "economic", "festival"):
            tasks.append(self.create_task(
                title=f"参与{event_type}活动",
                description=f"{event_description[:50]}",
                task_type="world_event",
                priority=max(3, impact_level - 1),
                created_day=current_day,
                deadline_day=current_day + 14,
                required_tags=["商业", "社交"],
                reward={"gold": 30},
            ))

        else:
            tasks.append(self.create_task(
                title=f"调查{event_description[:15]}",
                description=f"需要调查{event_description[:80]}的真相。",
                task_type="world_event",
                priority=impact_level,
                created_day=current_day,
                deadline_day=current_day + 10,
                required_tags=["探索", "智慧"],
                reward={"reputation": 5},
            ))

        return tasks

    def get_board_summary(self) -> dict:
        """获取任务板概览"""
        by_status = {}
        for task in self.tasks.values():
            by_status.setdefault(task.status, []).append(task.title)

        return {
            "total": len(self.tasks),
            "by_status": {s: len(ts) for s, ts in by_status.items()},
            "active_tasks": [
                {
                    "task_id": t.task_id,
                    "title": t.title,
                    "status": t.status,
                    "assigned_to": t.assigned_name or "未分配",
                    "progress": t.progress,
                    "priority": t.priority,
                }
                for t in sorted(
                    self.tasks.values(),
                    key=lambda t: t.priority, reverse=True
                )
                if t.status in (TaskStatus.PENDING, TaskStatus.READY, TaskStatus.RUNNING)
            ],
            "completed_count": len(self._completed_history),
        }

    def get_npc_tasks(self, npc_id: str) -> list[dict]:
        """获取某个 NPC 的所有任务"""
        return [
            t.to_dict() for t in self.tasks.values()
            if t.assigned_to == npc_id and t.status == TaskStatus.RUNNING
        ]

    # ── 内部方法 ──────────────────────────────────────────

    def _check_dependencies(self):
        """检查任务依赖，将满足条件的 pending 升级为 ready"""
        completed_ids = {
            t.task_id for t in self.tasks.values()
            if t.status == TaskStatus.COMPLETED
        }

        for task in self.tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            if not task.depends_on:
                task.status = TaskStatus.READY
                continue
            if all(dep_id in completed_ids for dep_id in task.depends_on):
                task.status = TaskStatus.READY

    def _calculate_match_score(self, npc: "NPCState", task: WorldTask) -> float:
        """计算 NPC 与任务的匹配度"""
        from .mbti_styles import get_mbti_profile
        score = 0.0

        # 角色匹配
        if task.required_role:
            if task.required_role in npc.role:
                score += 0.4
            elif any(tag in npc.role for tag in ["武", "官", "商", "医"]):
                score += 0.1

        # 标签匹配
        if task.required_tags:
            matched_tags = sum(
                1 for tag in task.required_tags
                if any(tag in npc_tag for npc_tag in npc.tags)
            )
            tag_ratio = matched_tags / len(task.required_tags)
            score += 0.3 * tag_ratio

        # 性格匹配（冒险型 NPC 更愿意接受任务）
        if npc.mbti_type:
            profile = get_mbti_profile(npc.mbti_type)
            if profile:
                score += 0.1 * profile.risk_tolerance
                score += 0.1 * profile.work_ethic

        # 精力检查
        if npc.stats.energy >= 30:
            score += 0.1
        elif npc.stats.energy < 10:
            score -= 0.3

        return max(0.0, min(1.0, score))

    def _calculate_progress(self, task: WorldTask,
                            npc_states: dict, world_state) -> int:
        """计算任务进度增量"""
        if not task.assigned_to:
            return 0

        npc = npc_states.get(task.assigned_to)
        if not npc:
            return 0

        # 基础进度：每天 15-30 点
        base_progress = 15

        # NPC 能力加成
        if task.required_tags:
            matched = sum(
                1 for tag in task.required_tags
                if any(tag in t for t in npc.tags)
            )
            base_progress += matched * 5

        # 精力衰减
        if npc.stats.energy < 20:
            base_progress = max(5, base_progress // 2)

        return min(50, base_progress)

    def _apply_world_effects(self, task: WorldTask, world_state):
        """将任务完成的世界效果应用到世界状态"""
        if not task.world_effects:
            return

        for key, delta in task.world_effects.items():
            if key == "crisis_delta":
                world_state.crisis_level = max(0, min(10,
                    world_state.crisis_level + delta))
            elif key == "stability_delta":
                # 影响所有势力的稳定度
                for faction in world_state.factions.values():
                    faction.stability = max(0, min(100,
                        faction.stability + delta))

    @staticmethod
    def _make_result(task: WorldTask, result_type: str) -> dict:
        return {
            "task_id": task.task_id,
            "title": task.title,
            "result_type": result_type,
            "assigned_to": task.assigned_name,
            "progress": task.progress,
        }

    def to_dict(self) -> dict:
        """序列化用于存档"""
        return {
            "tasks": {tid: t.to_dict() for tid, t in self.tasks.items()},
            "task_counter": self._task_counter,
            "completed_history": self._completed_history[-50:],
        }

    def from_dict(self, data: dict):
        """从存档恢复"""
        self.tasks = {
            tid: WorldTask.from_dict(t) for tid, t in data.get("tasks", {}).items()
        }
        self._task_counter = data.get("task_counter", 0)
        self._completed_history = data.get("completed_history", [])
