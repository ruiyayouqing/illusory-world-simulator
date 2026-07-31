from __future__ import annotations
import base64
import os
import uuid
import json
import httpx
from pathlib import Path
from .schemas import PlayerState, WorldState
from .llm.base_llm import BaseLLM


SILICONFLOW_API_DEFAULT = "https://api.siliconflow.cn/v1/images/generations"
KOLORS_MODEL = "Kwai-Kolors/Kolors"

CARTOON_STYLE = "anime cartoon style, vibrant colors, cel-shading, detailed character design, clean lines, Studio Ghibli inspired"

SCENE_STYLES = {
    "historical": f"Chinese historical {CARTOON_STYLE}, ancient China, dynasty robes",
    "fantasy": f"epic fantasy {CARTOON_STYLE}, magical atmosphere, glowing effects",
    "scifi": f"cyberpunk {CARTOON_STYLE}, neon lights, futuristic city",
    "postapocalyptic": f"post-apocalyptic {CARTOON_STYLE}, ruins, dramatic sky",
    "wuxia": f"Chinese wuxia {CARTOON_STYLE}, martial arts, swordsman, dynamic pose",
    "xianxia": f"Chinese xianxia {CARTOON_STYLE}, immortal cultivation, mystical energy, ethereal clouds",
    "modern": f"modern urban {CARTOON_STYLE}, city lights",
    "custom": f"cinematic {CARTOON_STYLE}, dramatic composition",
}


class VisualEngine:
    def __init__(self, llm: BaseLLM, output_dir: str = "./static/images/wst"):
        self.llm = llm
        # [v1.3] base_dir 为 wst 根目录，具体世界子目录由 set_world_id 动态切换
        self.base_output_dir = Path(output_dir)
        self.base_output_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir = self.base_output_dir  # 默认（无 world_id 时向后兼容）
        self.current_world_id: str = ""
        self.image_history: list[dict] = []
        self.siliconflow_key: str = ""
        self.siliconflow_model: str = KOLORS_MODEL
        self.image_api_url: str = SILICONFLOW_API_DEFAULT
        self.default_image_size: str = "1024x576"

    def set_world_id(self, world_id: str):
        """[v1.3] 切换当前世界图片输出子目录。
        新图片保存到 static/images/wst/{world_id}/，与旧图片隔离。
        旧图片（在 wst/ 根目录）仍可通过 image_url 访问，向后兼容。"""
        self.current_world_id = world_id or ""
        if world_id:
            self.output_dir = self.base_output_dir / world_id
        else:
            self.output_dir = self.base_output_dir
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def set_api_key(self, key: str):
        self.siliconflow_key = key

    def set_api_url(self, url: str):
        if url:
            self.image_api_url = url

    def set_model(self, model: str):
        if model:
            self.siliconflow_model = model

    def should_generate_image(self, text: str, event_type: str = "") -> bool:
        triggers = ["战斗", "火灾", "爆炸", "地震", "婚礼", "死亡", "重逢",
                     "离别", "告白", "战争", "日出", "日落", "雪景", "月夜",
                     "酒楼", "宫殿", "战场", "悬崖", "大海", "森林", "山峰",
                     "城门", "江湖", "修炼", "渡劫", "金丹", "突破"]
        return any(t in text for t in triggers) or event_type in ["combat", "romance", "tragedy", "levelup"]

    def generate_image_prompt(self, narrative: str, player: PlayerState,
                              location: str, weather: str,
                              world_type: str = "custom",
                              age: int = 18) -> str:
        style = SCENE_STYLES.get(world_type, SCENE_STYLES["custom"])

        scene_prompt = f"""根据以下剧情文本，生成一段用于AI绘画的英文场景描述（100字以内）。

【剧情】
{narrative[:600]}

【主要角色】
年龄: {age}岁

【要求】
- 描述具体的人物动作、表情、环境、光线
- 包含场景中的关键元素（建筑、道具、氛围）
- 用英文输出，逗号分隔关键词
- 不要人名，用角色描述代替
- 必须体现人物年龄特征

只输出英文描述，不要其他文字。"""
        try:
            scene_desc = self.llm.chat(scene_prompt, temperature=0.5, max_tokens=0)
            scene_desc = scene_desc.strip().strip('"').strip("'")
        except Exception as e:
            logger.debug("Scene description LLM failed, using fallback: %s", e)
            scene_desc = narrative[:200].replace('\n', ' ')

        prompt = f"""2d cartoon style, anime cel-shading, vibrant colors, {scene_desc}, masterpiece quality, detailed, 8k resolution."""
        return prompt

    def generate_image(self, prompt: str, size: str = "") -> dict:
        if not self.siliconflow_key:
            return {"generated": False, "error": "未配置SiliconFlow API Key"}

        if not size:
            size = self.default_image_size

        is_openai_compat = "agnes" in self.image_api_url or "openai" in self.image_api_url

        if is_openai_compat:
            payload = {
                "model": self.siliconflow_model,
                "prompt": prompt,
                "n": 1,
                "size": size,
            }
        else:
            payload = {
                "model": self.siliconflow_model,
                "prompt": prompt,
                "image_size": size,
                "batch_size": 1,
                "num_inference_steps": 20,
                "guidance_scale": 7.5,
            }

        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    self.image_api_url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.siliconflow_key}",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    images = data.get("images", []) or data.get("data", [])
                    if images:
                        image_url = images[0].get("url", "")
                        if image_url:
                            image_id = f"img_{uuid.uuid4().hex[:8]}"
                            self._download_image(image_url, image_id)
                            # [v1.3] 图片 URL 包含 world_id 子目录，便于按世界隔离和清理
                            if self.current_world_id:
                                img_url = f"/images/wst/{self.current_world_id}/{image_id}.png"
                            else:
                                img_url = f"/images/wst/{image_id}.png"  # 向后兼容
                            return {
                                "generated": True,
                                "image_id": image_id,
                                "image_url": img_url,
                                "source_url": image_url,
                                "prompt": prompt,
                            }
                    return {"generated": False, "error": f"API返回无图片数据: {data}"}
                else:
                    error_detail = ""
                    try:
                        error_detail = resp.text[:200]
                    except Exception as e:
                        logger.debug("Failed to read error response: %s", e)
                    return {"generated": False, "error": f"API错误: {resp.status_code} {error_detail}"}
        except Exception as e:
            return {"generated": False, "error": str(e)}

    def _download_image(self, url: str, image_id: str):
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    path = self.output_dir / f"{image_id}.png"
                    path.write_bytes(resp.content)
        except Exception as e:
            logger.warning("Image download failed: %s", e)

    def generate_scene_image(self, narrative: str, player: PlayerState,
                             location: str, weather: str,
                             world_type: str = "custom",
                             day: int = 0, time_str: str = "") -> dict:
        if not self.should_generate_image(narrative):
            return {"generated": False, "reason": "场景不触发生成"}

        prompt = self.generate_image_prompt(
            narrative, player, location, weather, world_type, player.age
        )
        result = self.generate_image(prompt)

        if result.get("generated"):
            result["location"] = location
            result["weather"] = weather
            result["day"] = day
            result["time"] = time_str
            self.image_history.append(result)
            # [v1.3] 取消 50 张上限：用户希望加载存档后聊天界面能完整呈现所有历史图片（如看小说）
            # 存档体积由 game_state.json 的原子写入保障，图片 URL 体积可控（每条约 200 字节）

        return result

    def generate_age_portrait(self, player: PlayerState, world_type: str = "custom") -> dict:
        age = player.age
        if age < 20:
            desc = "a young man, 18 years old, innocent face, wearing simple robes"
        elif age < 30:
            desc = "a handsome young man in his mid-20s, confident expression, wearing fine robes"
        elif age < 40:
            desc = "a mature man in his 30s, weathered face, wearing noble robes"
        elif age < 50:
            desc = "a middle-aged man in his 40s, graying temples, wearing official robes"
        else:
            desc = "an elderly man, white hair, wise eyes, wearing scholar robes"

        style = SCENE_STYLES.get(world_type, SCENE_STYLES["custom"])
        prompt = f"""Portrait of {desc}. {style}, detailed face, cinematic lighting, masterpiece quality."""
        return self.generate_image(prompt, "768x1024")

    def generate_character_portrait(self, name: str, age: int,
                                    personality: str, tags: list[str],
                                    world_type: str = "custom") -> dict:
        style = SCENE_STYLES.get(world_type, SCENE_STYLES["custom"])
        prompt = f"""Portrait of a character named {name}, age {age}, personality: {personality}. {style}, detailed face, cinematic lighting, masterpiece quality."""
        return self.generate_image(prompt, "768x1024")

    def get_recent_images(self, n: int = 5) -> list[dict]:
        return self.image_history[-n:]
