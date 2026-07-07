"""
[v9] WebSocket 路由 — 线程安全 + 心跳保活 + 连接字典管理
"""
from __future__ import annotations
import asyncio
import json
import logging
from fastapi import WebSocket, WebSocketDisconnect, Query

from .deps import get_engine, active_connections, ws_lock, access_token

logger = logging.getLogger("chronoverse")

HEARTBEAT_INTERVAL = 30  # 秒


async def websocket_endpoint(websocket: WebSocket, client_id: str,
                              token: str = Query("")):
    # [v11] WebSocket 鉴权：验证 token（如果设置了 access_token）
    if access_token and token != access_token:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()

    # [v9] 用锁保护连接字典的写操作
    async with ws_lock:
        active_connections[client_id] = websocket

    engine = get_engine()

    # [v10] 改用 asyncio.Queue 替代 queue.Queue，避免线程池阻塞调用无法中断（H22）
    token_queue: asyncio.Queue = asyncio.Queue()
    consumer_task: asyncio.Task | None = None
    heartbeat_task: asyncio.Task | None = None

    # [v10] 在事件循环线程获取 loop，供工作线程线程安全地投递 token（M9c: 使用 get_running_loop）
    loop = asyncio.get_running_loop()

    def sync_stream_callback(token):
        """同步回调：从 process_player_input 的工作线程调用，线程安全地放入 asyncio.Queue"""
        loop.call_soon_threadsafe(token_queue.put_nowait, token)

    async def stream_consumer():
        """异步消费者：从 asyncio.Queue 读取 token 并推送到前端 WebSocket"""
        while True:
            try:
                # [v10] 120 秒超时即退出，避免消费者永不结束（H22）
                # [Bug#25] 从 30s 增至 120s：LLM 大 prompt 首 token 可能需要 30+ 秒
                token = await asyncio.wait_for(token_queue.get(), timeout=120.0)
            except asyncio.TimeoutError:
                break
            if token is None:
                try:
                    await websocket.send_text(
                        json.dumps({"type": "stream_end"}, ensure_ascii=False)
                    )
                except Exception:
                    pass
                break
            else:
                try:
                    await websocket.send_text(
                        json.dumps({"type": "stream_token", "token": token}, ensure_ascii=False)
                    )
                except Exception:
                    pass

    async def heartbeat():
        """[v9] 心跳保活：每30秒发一次ping，防止长连接断开"""
        while True:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await websocket.send_text(json.dumps({"type": "ping"}))
            except Exception:
                break

    # [v10] 连接建立与回调注册纳入 try/finally，确保异常时也能清理（M3c）
    try:
        if engine and engine.streamer:
            await engine.streamer.connect(client_id, websocket)
        if engine:
            # [v10] 注册按 client_id 绑定的回调，避免多客户端互相覆盖（H21）
            engine.register_stream_callback(client_id, sync_stream_callback)

        # [v9] 启动心跳任务
        heartbeat_task = asyncio.ensure_future(heartbeat())

        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            engine = get_engine()
            if msg.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            elif msg.get("type") == "stream_input" and engine:
                text = msg.get("text", "")
                if text and engine.player_state:
                    await websocket.send_text(json.dumps({"type": "thinking"}, ensure_ascii=False))
                    _drain_queue(token_queue)
                    consumer_task = asyncio.ensure_future(stream_consumer())
                    try:
                        # [v10] 与 HTTP 端点一致，加游戏锁防止竞态（H23）；并激活当前客户端的流式回调（H21）
                        async with engine._game_lock:
                            engine.set_active_stream_client(client_id)
                            result = await asyncio.to_thread(engine.process_player_input, text)
                    except Exception:
                        await token_queue.put(None)
                        raise
                    finally:
                        # [v10] process_player_input 结束后放入 None 哨兵，确保消费者能退出（H22）
                        await token_queue.put(None)
                    try:
                        await asyncio.wait_for(consumer_task, timeout=5)
                    except asyncio.TimeoutError:
                        consumer_task.cancel()
                        logger.warning("stream_consumer did not finish in 5s, cancelled")
                    consumer_task = None
                    state = engine.get_game_state()
                    await websocket.send_text(json.dumps({
                        "type": "result",
                        "result": result,
                        "state": state,
                    }, ensure_ascii=False))
            elif msg.get("type") == "input" and engine:
                text = msg.get("text", "")
                if text and engine.player_state:
                    await websocket.send_text(json.dumps({"type": "thinking"}))
                    # [v10] 与 HTTP 端点一致，加游戏锁防止竞态（H23）
                    async with engine._game_lock:
                        engine.set_active_stream_client(client_id)
                        result = await asyncio.to_thread(engine.process_player_input, text)
                    state = engine.get_game_state()
                    await websocket.send_text(json.dumps({
                        "type": "result",
                        "result": result,
                        "state": state,
                    }, ensure_ascii=False))
            elif msg.get("type") == "stream_narrative" and engine:
                text = msg.get("text", "")
                if text and engine.streamer:
                    await engine.streamer.stream_narrative(client_id, text)
    except WebSocketDisconnect:
        logger.info("WebSocket client %s disconnected", client_id)
    except Exception as e:
        logger.warning("WebSocket error for %s: %s", client_id, e)
    finally:
        # [v9] 统一清理：心跳、消费者、回调、连接字典
        if heartbeat_task and not heartbeat_task.done():
            heartbeat_task.cancel()
        if consumer_task and not consumer_task.done():
            consumer_task.cancel()
        _drain_queue(token_queue)
        _cleanup_engine(engine, client_id)
        async with ws_lock:
            active_connections.pop(client_id, None)
        engine = get_engine()
        if engine and engine.streamer:
            engine.streamer.disconnect(client_id)


def _drain_queue(q: asyncio.Queue):
    """清空 asyncio.Queue 中残留的 token"""
    while not q.empty():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            break


def _cleanup_engine(engine, client_id):
    """断连时清理 engine 中绑定到该 client_id 的流式回调（H21）"""
    if engine:
        try:
            engine.clear_stream_callback(client_id)
        except Exception:
            pass
