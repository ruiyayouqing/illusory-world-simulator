"""
引擎池模块 — LRU 缓存管理多用户 GameEngine 实例
"""
import threading
import time
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Optional

logger = logging.getLogger("chronoverse.pool")


class EnginePool:
    """LRU 引擎池：管理多用户的 GameEngine 实例"""

    def __init__(self, base_dir: Path, server_config: dict,
                 max_engines: int = 10, idle_timeout: int = 1800):
        self.base_dir = base_dir
        self.server_config = server_config
        self.max_engines = max_engines
        self.idle_timeout = idle_timeout
        self._engines: OrderedDict[str, tuple] = OrderedDict()  # user_id -> (engine, last_access)
        self._lock = threading.Lock()

    def get_user_save_dir(self, user_id: str) -> Path:
        user_dir = self.base_dir / "data" / "user_data" / user_id / "saves"
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    def get_or_create(self, user_id: str) -> Optional[object]:
        """获取或创建用户的引擎"""
        with self._lock:
            # 引擎已存在
            if user_id in self._engines:
                engine, _ = self._engines[user_id]
                self._engines[user_id] = (engine, time.time())
                self._engines.move_to_end(user_id)
                return engine

            # 容量已满，驱逐最久未使用的
            if len(self._engines) >= self.max_engines:
                self._evict_oldest()

            # 创建新引擎
            from modules.game_engine import GameEngine

            save_dir = str(self.get_user_save_dir(user_id))
            engine = GameEngine(save_dir)

            # 用服务端配置初始化 LLM
            llm_cfg = self.server_config.get("llm", {})
            engine.init_llm(
                api_key=llm_cfg.get("api_key", ""),
                base_url=llm_cfg.get("base_url", ""),
                model_name=llm_cfg.get("model_name", ""),
            )

            # 应用图片生成配置
            self._apply_image_config(engine)

            self._engines[user_id] = (engine, time.time())
            logger.info("Engine created for user %s, pool: %d/%d",
                        user_id, len(self._engines), self.max_engines)
            return engine

    def _evict_oldest(self):
        """驱逐最久未使用的引擎"""
        if not self._engines:
            return
        user_id, (engine, _) = self._engines.popitem(last=False)
        try:
            if engine and engine.player_state:
                engine.save_game("auto")
            if hasattr(engine, 'close'):
                engine.close()
            logger.info("Engine evicted: %s", user_id)
        except Exception as e:
            logger.warning("Eviction error for %s: %s", user_id, e)

    def remove(self, user_id: str) -> bool:
        """主动移除用户引擎"""
        with self._lock:
            if user_id in self._engines:
                engine, _ = self._engines.pop(user_id)
                try:
                    if engine and engine.player_state:
                        engine.save_game("auto")
                    if hasattr(engine, 'close'):
                        engine.close()
                except Exception as e:
                    logger.warning("Remove error for %s: %s", user_id, e)
                return True
            return False

    def cleanup_idle(self):
        """清理超时的空闲引擎"""
        now = time.time()
        with self._lock:
            expired_uids = [
                uid for uid, (_, last_access) in self._engines.items()
                if now - last_access > self.idle_timeout
            ]
            for uid in expired_uids:
                if uid in self._engines:
                    engine, _ = self._engines.pop(uid)
                    try:
                        if engine and engine.player_state:
                            engine.save_game("auto")
                        if hasattr(engine, 'close'):
                            engine.close()
                    except Exception as e:
                        logger.warning("Idle cleanup error for %s: %s", uid, e)
            if expired_uids:
                logger.info("Cleaned up %d idle engines", len(expired_uids))

    def get_pool_status(self) -> dict:
        with self._lock:
            return {
                "total": len(self._engines),
                "max": self.max_engines,
                "users": list(self._engines.keys()),
            }

    def _apply_image_config(self, engine):
        """应用图片生成配置"""
        img_cfg = self.server_config.get("image", {})
        if img_cfg.get("api_key") and engine.visual_engine:
            engine.visual_engine.set_api_key(img_cfg["api_key"])
        if img_cfg.get("base_url") and engine.visual_engine:
            engine.visual_engine.set_api_url(img_cfg["base_url"])
        if img_cfg.get("model_name") and engine.visual_engine:
            engine.visual_engine.set_model(img_cfg["model_name"])
        if img_cfg.get("image_size") and engine.visual_engine:
            engine.visual_engine.default_image_size = img_cfg["image_size"]
