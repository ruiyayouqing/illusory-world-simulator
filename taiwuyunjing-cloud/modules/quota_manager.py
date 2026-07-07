"""
配额管理模块 — 太虚幻境云服务版
"""
import logging

logger = logging.getLogger("chronoverse.quota")


class QuotaManager:
    """配额管理器：控制用户每日游戏轮数"""

    def __init__(self, user_manager):
        self.user_manager = user_manager

    def check_quota(self, user_id: str) -> tuple[bool, int, int]:
        """检查用户是否还有配额，返回 (can_play, remaining, limit)"""
        enabled = self.user_manager.get_setting("quota_enabled", "0") == "1"
        if not enabled:
            return True, -1, -1
        limit = int(self.user_manager.get_setting("daily_turn_limit", "3"))
        used = self.user_manager.get_today_usage(user_id)
        remaining = limit - used
        return remaining > 0, remaining, limit

    def record_turn(self, user_id: str):
        """记录一次游戏轮次"""
        self.user_manager.record_turn(user_id)

    def get_usage_info(self, user_id: str) -> dict:
        """获取用户配额使用情况"""
        enabled = self.user_manager.get_setting("quota_enabled", "0") == "1"
        limit = int(self.user_manager.get_setting("daily_turn_limit", "3"))
        used = self.user_manager.get_today_usage(user_id)
        return {
            "enabled": enabled,
            "limit": limit,
            "used": used,
            "remaining": max(0, limit - used) if enabled else -1,
        }
