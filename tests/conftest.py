"""[v1.7 P2-1] pytest 公共夹具：统一导入路径 + 测试用样本数据构造。

提供内容：
1. sys.path 自动注入（让 tests 能 import modules.*）
2. tmp_dir fixture：隔离的临时目录
3. fake_llm fixture：可调用的 Mock LLM
4. sample_npc / sample_player_state：标准测试数据
5. memory_store fixture：临时向量库（自动清理）
6. addopts 选项：--runslow 控制慢测试

设计原则：
- 所有 fixture 都不应依赖真实外部资源（LLM API、ChromaDB 持久化目录）
- 慢测试用 @pytest.mark.slow 标记，CI 默认跳过
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ── 1. sys.path 注入 ───────────────────────────────────────
# pytest 启动时，把 app 目录加入 sys.path，让 tests 能 import modules.*
APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


# ── 2. 命令行选项 ───────────────────────────────────────────
def pytest_addoption(parser):
    """添加 --runslow 选项：默认跳过慢测试，开启后跑全部。"""
    parser.addoption(
        "--runslow",
        action="store_true",
        default=False,
        help="运行标记为 slow 的测试（长跑、压测、真实 LLM 调用）",
    )
    parser.addoption(
        "--runllm",
        action="store_true",
        default=False,
        help="运行标记为 requires_llm 的测试（需要真实 LLM 后端）",
    )


def pytest_collection_modifyitems(config, items):
    """根据命令行选项跳过 slow / requires_llm 测试。"""
    skip_slow = pytest.mark.skip(reason="需要 --runslow 选项才会运行")
    skip_llm = pytest.mark.skip(reason="需要 --runllm 选项才会运行")

    for item in items:
        if "slow" in item.keywords and not config.getoption("--runslow"):
            item.add_marker(skip_slow)
        if "requires_llm" in item.keywords and not config.getoption("--runllm"):
            item.add_marker(skip_llm)


# ── 3. 通用 fixtures ─────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    """提供一个临时目录，测试结束自动清理（忽略 Windows 文件锁导致的清理失败）。"""
    d = tempfile.mkdtemp(prefix="chronoverse_test_")
    yield Path(d)
    # chromadb 在 Windows 上经常留下 sqlite3 文件锁，忽略清理错误
    # 真正的清理由系统临时目录定期清理完成
    import shutil
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def fake_llm():
    """Mock LLM：返回可定制的 dict / text 响应。

    用法：
        def test_xxx(fake_llm):
            fake_llm.chat_json.return_value = {"emotion_type": "joy"}
            # 或者 fake_llm.chat_json.side_effect = [...]
    """
    llm = MagicMock()
    llm.chat_json.return_value = {}
    llm.chat.return_value = ""
    llm.chat_stream.return_value = iter(["mock", "response"])
    return llm


@pytest.fixture
def sample_npc_data():
    """标准测试用 NPC 数据 dict（用于构造 NPCState）。"""
    return {
        "agent_id": "npc_test_01",
        "name": "测试NPC",
        "age": 25,
        "tags": ["善良", "温和"],
        "personality": "温和有礼，重视情义",
        "health": 100,
        "energy": 100,
        "favor": 50,
        "alignment": "中庸",
        "personal_reputation": 0,
    }


@pytest.fixture
def sample_player_data():
    """标准测试用玩家数据 dict。"""
    return {
        "agent_id": "player_01",
        "name": "测试玩家",
        "age": 18,
        "tags": ["穿越者"],
        "location": "village",
    }


@pytest.fixture
def sample_world_data():
    """标准测试用世界数据 dict。"""
    return {
        "world_type": "xianxia",
        "description": "测试修仙世界",
        "locations": {
            "village": {"type": "村庄", "name": "青云村"},
            "mountain": {"type": "山脉", "name": "青云山"},
        },
    }


@pytest.fixture
def memory_store(tmp_dir):
    """临时向量库实例，测试结束自动清理。

    使用临时目录隔离，避免污染真实存档数据。
    """
    from modules.db.chroma_db import MemoryStore
    store = MemoryStore(persist_dir=str(tmp_dir / "test_chroma"),
                       collection_name="test_memory")
    yield store
    # 不显式调用 client.reset()：在 Windows 上会破坏 chroma.sqlite3 文件状态
    # tmp_dir fixture 会用 ignore_errors=True 清理，足够安全


# ── 4. 工具 fixtures ─────────────────────────────────────────

@pytest.fixture
def assert_dict_contains():
    """断言 actual 包含 expected 的所有 key/value（不要求完全相等）。"""
    def _check(actual: dict, expected: dict, path: str = ""):
        for k, v in expected.items():
            cur = f"{path}.{k}" if path else k
            assert k in actual, f"缺少字段 {cur}"
            if isinstance(v, dict):
                _check(actual[k], v, cur)
            else:
                assert actual[k] == v, f"字段 {cur} 不匹配：期望 {v!r}，实际 {actual[k]!r}"
    return _check


# ── 5. 测试环境检查 ─────────────────────────────────────────

def pytest_configure(config):
    """启动时打印环境信息，便于排查测试环境问题。"""
    config._chronoverse_env = {
        "app_dir": str(APP_DIR),
        "python_path": sys.executable,
    }


@pytest.fixture(autouse=True)
def _reset_emotion_global_state():
    """自动重置情感系统全局单例，避免测试间状态污染。"""
    yield
    try:
        from modules.memory.emotional_manager import get_emotional_manager
        mgr = get_emotional_manager()
        if mgr is not None:
            mgr._npc_states.clear()
            if mgr._player_state is not None:
                mgr._player_state.vector = {e: 0.0 for e in mgr._player_state.vector}
    except Exception:
        # 情感系统未启用或单例不存在：忽略
        pass
