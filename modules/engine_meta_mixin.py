"""
[v1.4 P1-5] MetaFacadeMixin — 元数据/统计查询 Facade

把 GameEngine 中所有纯查询/统计/审批方法集中到这里，降低主类体积。
GameEngine 通过 Mixin 继承获得这些方法，对外接口完全兼容。
"""
from __future__ import annotations
import logging

logger = logging.getLogger("chronoverse.engine")


class MetaFacadeMixin:
    """[v1.4] 元数据/统计查询 Facade Mixin

    依赖宿主类的以下属性：
      - self.graph_rag
      - self.narrative_reviewer
      - self.world_task_board
      - self.butterfly
      - self.memory_curator
      - self.npc_procedural_memory
      - self.npc_skill_library
      - self.multi_agent_narrative
      - self.foreshadow_lifecycle
      - self.continuity_auditor
      - self.memory
      - self.world_state
      - self.world_agent
      - self.trigger_hook
    """

    # ── GraphRAG 查询 ──────────────────────────────────────

    def query_graph_rag(self, question: str) -> dict:
        """查询知识图谱"""
        if not self.graph_rag:
            return {"results": [], "context": ""}
        results = self.graph_rag.query(question)
        context = self.graph_rag.get_context_for_prompt(question)
        return {"results": results, "context": context}

    def get_graph_visualization(self) -> dict:
        """获取知识图谱可视化数据"""
        if not self.graph_rag:
            return {"nodes": [], "edges": []}
        return self.graph_rag.to_visualization_data()

    def get_faction_graph(self, method: str = "louvain",
                           active_only: bool = True) -> dict:
        """[v1.6 P1-4] 获取势力图可视化数据（含社区检测）"""
        if not self.graph_rag:
            return {
                "elements": {"nodes": [], "edges": []},
                "communities": [], "lone_entities": [],
                "stats": {"total_entities": 0, "total_relations": 0,
                          "community_count": 0, "largest_community_size": 0,
                          "modularity": 0.0},
            }
        return self.graph_rag.get_faction_visualization_data(
            method=method, active_only=active_only,
        )

    def detect_factions(self, method: str = "louvain",
                         active_only: bool = True) -> dict:
        """[v1.6 P1-4] 仅获取势力检测结果（不含图谱元素，给精简调用方使用）"""
        if not self.graph_rag:
            return {"communities": [], "lone_entities": [],
                    "stats": {"total_entities": 0, "total_relations": 0,
                              "community_count": 0, "largest_community_size": 0,
                              "modularity": 0.0}}
        return self.graph_rag.detect_communities(
            method=method, active_only=active_only,
        )

    # ── [v10] 新增 API 方法 ────────────────────────────────

    def get_narrative_review(self) -> dict:
        """获取叙事回顾结果和质量趋势"""
        if not self.narrative_reviewer:
            return {"error": "叙事回顾器未初始化"}
        return {
            "quality_trend": self.narrative_reviewer.get_quality_trend(),
            "lessons_count": len(self.narrative_reviewer.lessons),
            "active_lessons": [
                l.to_dict() for l in sorted(
                    self.narrative_reviewer.lessons,
                    key=lambda x: x.importance, reverse=True
                )[:10]
            ],
        }

    def get_task_board(self) -> dict:
        """获取世界任务板状态"""
        if not self.world_task_board:
            return {"error": "任务板未初始化"}
        return self.world_task_board.get_board_summary()

    def get_butterfly_approvals(self) -> list[dict]:
        """获取待审批的蝴蝶效应"""
        if not self.butterfly:
            return []
        return self.butterfly.get_pending_approvals()

    def approve_butterfly_effect(self, approval_id: str,
                                  decision: str = "approve") -> dict:
        """审批蝴蝶效应后果"""
        if not self.butterfly:
            return {"error": "蝴蝶效应系统未初始化"}
        result = self.butterfly.approve_consequence(approval_id, decision)
        if result.get("approved") and result.get("impact"):
            # 执行已批准的后果
            consequence = self.butterfly.generate_consequence(
                result["impact"], self.world_state
            )
            if consequence:
                if self.world_agent:
                    self.world_agent.update_world_state(self.world_state, consequence)
                result["consequence"] = consequence.model_dump()
                # [v10.5] 使用实例级 trigger_hook 而非全局
                self.trigger_hook("on_butterfly_approval",
                             approval_id=approval_id, consequence=consequence)
        return result

    def get_curator_stats(self) -> dict:
        """获取记忆 Curator 统计"""
        if not self.memory_curator:
            return {"error": "Curator 未初始化"}
        return self.memory_curator.get_curate_stats()

    def get_npc_procedural_stats(self) -> dict:
        """获取 NPC 程序性记忆统计"""
        if not self.npc_procedural_memory:
            return {"error": "NPC程序性记忆未初始化"}
        return self.npc_procedural_memory.get_stats()

    def get_npc_skill_library_stats(self) -> dict:
        """[v10++] 获取 NPC 技能自学库统计（Voyager/Hermes 式）"""
        if not self.npc_skill_library:
            return {"error": "NPC技能自学库未初始化"}
        return self.npc_skill_library.get_stats()

    def get_multi_agent_narrative_stats(self) -> dict:
        """[v10+++] 获取多智能体分工叙事统计（Agents' Room 式）"""
        if not self.multi_agent_narrative:
            return {"error": "多智能体叙事引擎未初始化"}
        return self.multi_agent_narrative.get_stats()

    def get_v10_dashboard(self) -> dict:
        """[v10] 获取所有 v10 新系统的概览面板"""
        return {
            "narrative_review": self.get_narrative_review(),
            "task_board": self.get_task_board(),
            "curator": self.get_curator_stats(),
            "procedural_memory": self.get_npc_procedural_stats(),
            "butterfly_pending": len(self.get_butterfly_approvals()),
            "memory_quality": {
                "working_memory": self.memory.get_working_memory_context(3) if self.memory else "",
                "identity_count": self.memory.get_identity_count() if self.memory else 0,
            },
            # [v10+] 新增
            "foreshadow": self.get_foreshadow_health(),
            "continuity_audit": self.get_continuity_audit(),
            # [v10++] NPC 技能自学库（Voyager/Hermes 式）
            "skill_library": self.get_npc_skill_library_stats(),
            # [v10+++] 多智能体分工叙事（Agents' Room 式）
            "multi_agent_narrative": self.get_multi_agent_narrative_stats(),
        }

    # ── [v10+] 新增 API 方法 ──────────────────────────────

    def get_foreshadow_health(self) -> dict:
        """获取伏笔健康报告"""
        if not self.foreshadow_lifecycle:
            return {"error": "伏笔生命周期管理器未初始化"}
        current_day = self.world_state.current_day if self.world_state else 0
        report = self.foreshadow_lifecycle.get_health_report(current_day)
        report["active_hooks"] = self.foreshadow_lifecycle.get_active_hooks()
        report["reminder_mode"] = self.foreshadow_lifecycle.reminder_mode
        # 静默模式下 hooks_for_prompt 为空
        report["hooks_for_prompt"] = self.foreshadow_lifecycle.get_hooks_for_prompt(5)
        return report

    def get_continuity_audit(self) -> dict:
        """获取连续性审计结果"""
        if not self.continuity_auditor:
            return {"error": "连续性审计器未初始化"}
        return {
            "latest_report": self.continuity_auditor.get_latest_report(),
            "trend": self.continuity_auditor.get_audit_trend(),
        }
