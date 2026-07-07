# 太虚幻境 — 虚拟世界人生模拟器

> 无限世界文字推演引擎 · 闭环学习 + 多智能体协调 + 撤销重选

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 🎮 程序运行截图

![游戏主界面](https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt=fantasy%20text%20adventure%20game%20interface%2C%20dark%20theme%2C%20medieval%20chinese%20style%2C%20narrative%20text%2C%20character%20status%2C%20dialogue%20options&image_size=landscape_16_9)

![世界生成](https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt=AI%20world%20generation%20loading%20screen%2C%20mystical%20fantasy%20theme%2C%20stars%20and%20nebula%2C%20chinese%20calligraphy%20style&image_size=landscape_16_9)

---

## ✨ v1.1 核心升级：撤销重选 + NPC对话系统

v1.1 在 v10 基础上新增两大核心功能：

| 功能 | 说明 |
|------|------|
| ↩️ **撤销上一轮** | 不满意当前决策？一键撤销回到上一回合重新选择 |
| 💬 **NPC对话系统** | 与任意 NPC 进行深度对话，了解他们的故事和想法 |

---

## ↩️ 撤销重选系统

```
玩家选择"进攻" → 战斗失败 → 点击"撤销" → 回到上回合 → 重新选择"谈判"
```

- 支持多步撤销（默认最近 5 步）
- 撤销后自动恢复世界状态
- 不影响其他玩家/NPC 的行动

## 💬 NPC对话系统

```
玩家："你为什么来到这里？"
NPC（李三娘）："我丈夫被官府抓走了，听说这里能找到救他的办法..."
```

- 与场景中任意 NPC 对话
- NPC 基于记忆和性格回应
- 对话内容影响世界状态

---

## ✨ v10 核心升级：闭环学习 + 多智能体协调

v10 借鉴 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的设计哲学，引入六大核心改进：

| 系统 | 说明 | 灵感来源 |
|------|------|----------|
| 🔄 **闭环学习** | 叙事回顾 → 教训提取 → prompt 注入 → 更好的叙事 | Hermes 后台自我改进审查 |
| 🧠 **分层记忆** | 工作记忆 + 情景记忆(带重要性衰减) + 语义记忆 | Hermes 声明式/情景式/程序式三层记忆 |
| 🤖 **NPC 程序性记忆** | NPC 从经验中学习，高效动作优先 | Hermes 技能即程序性记忆 |
| 📋 **世界任务板** | 世界事件自动生成任务，NPC 自动认领执行 | Hermes Kanban 持久化任务板 |
| 🗄️ **记忆 Curator** | 定期整理冗余/过时记忆，提取行为模式 | Hermes Curator 后台维护 |
| 🛡️ **蝴蝶效应审批门** | 高影响力行为需玩家确认，防止意外崩坏 | Hermes 写入审批门 |

---

## 🔄 闭环学习系统

```
叙事生成 → 回顾分析 → 教训提取 → 注入 prompt → 更好的叙事
    ↑                                              |
    └──────────────── 闭环 ─────────────────────────┘
```

- 每 5 回合自动回顾最近叙事
- 检查一致性、角色深度、节奏感
- 提取玩家偏好和叙事质量教训
- 教训直接注入到下一次叙事生成

## 🧠 分层记忆

**综合评分公式**：
```
score = 向量相似度 × 0.45 + 重要性 × 0.25 + 时间衰减 × 0.15 + 情感权重 × 0.10 + 访问频率 × 0.05
```

| 层级 | 说明 | 特点 |
|------|------|------|
| 工作记忆 | 高重要性核心记忆 | 每次 prompt 注入 |
| 情景记忆 | ChromaDB 向量检索 | 30天半衰期衰减 |
| 语义记忆 | 身份/关系/世界观 | 长期稳定 |

## 🤖 NPC 程序性记忆

```
NPC "在酒馆打听消息" → 成功率 80% → 下次优先
NPC "独自探索荒野"  → 成功率 20% → 下次降级
```

- 记录"动作-上下文-结果"三元组
- 相似记忆自动合并（不膨胀）
- BranchPlanner 规划时参考历史经验

## 📋 世界任务板

```
叛军入侵事件
  → 任务"指挥防御"（需求：武将）  → 武将NPC认领
  → 任务"筹集军需"（需求：商人）  → 商人NPC认领
  → 任务"安抚民心"（需求：官员）  → 官员NPC认领
```

- 世界事件自动生成任务
- NPC 按角色/性格/标签匹配认领
- 任务完成自动影响世界状态

## 🛡️ 蝴蝶效应审批门

```
玩家刺杀皇帝 → 影响分数 9.2 → 触发审批门
  预览：此行为将引发全国性内战
  [批准] [拒绝] [修改]
```

默认关闭，可通过 API 或 config 开启。

---

## 🚀 快速开始

```bash
pip install -r requirements.txt
python server.py
```

访问 http://localhost:8004

---

## 📡 新增 API 端点

### v1.1 端点
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/undo` | POST | 撤销上一轮 |
| `/api/npc/chat` | POST | 与 NPC 对话 |
| `/api/npc/list` | GET | 获取场景 NPC 列表 |

### v10 端点
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v10/dashboard` | GET | v10 全系统概览 |
| `/api/v10/narrative-review` | GET | 叙事回顾 + 质量趋势 |
| `/api/v10/task-board` | GET | 世界任务板状态 |
| `/api/v10/butterfly-approvals` | GET | 待审批蝴蝶效应 |
| `/api/v10/butterfly-approve` | POST | 审批蝴蝶效应 |
| `/api/v10/curator-stats` | GET | 记忆 Curator 统计 |
| `/api/v10/procedural-memory` | GET | NPC 程序性记忆统计 |
| `/api/v10/approval-gate/config` | POST | 配置审批门 |

---

## ⚙️ 配置

```json
{
  "v11": {
    "undo_max_steps": 5,
    "npc_chat_enabled": true
  },
  "v10": {
    "narrative_reviewer": { "enabled": true, "review_interval": 5 },
    "npc_procedural_memory": { "enabled": true, "max_entries_per_npc": 30 },
    "world_task_board": { "enabled": true, "max_active_tasks": 20 },
    "memory_curator": { "enabled": true, "curate_interval": 15 },
    "butterfly_approval_gate": { "enabled": false, "approval_threshold": 7.0 },
    "layered_memory": { "time_decay_half_life_days": 30 }
  }
}
```

---

## 📁 新增/修改文件

### v1.1 修改
| 文件 | 修改 |
|------|------|
| `modules/game_engine.py` | +undo_last_turn, npc_chat |
| `modules/turn_processor_v2.py` | +撤销逻辑支持 |
| `modules/config_schema.py` | +v11配置字段 |
| `modules/llm/mimo_llm.py` | +对话模型优化 |
| `modules/narrative_style.py` | +对话叙事风格 |
| `modules/player_agent.py` | +对话交互支持 |
| `routes/game_routes.py` | +/api/undo, /api/npc/chat, /api/npc/list |
| `routes/config_routes.py` | +配置管理优化 |
| `routes/websocket_routes.py` | +对话消息处理 |

### v10 新增
| 文件 | 说明 |
|------|------|
| `modules/narrative_reviewer.py` | 闭环学习系统 |
| `modules/npc_procedural_memory.py` | NPC 程序性记忆 |
| `modules/world_task_board.py` | 世界任务板 |
| `modules/memory_curator.py` | 记忆 Curator |

### v10 修改
| 文件 | 修改 |
|------|------|
| `modules/schemas.py` | +MemoryEntry, ButterflyApproval, LearningRecord |
| `modules/db/chroma_db.py` | +分层记忆检索, 重要性存储 |
| `modules/butterfly_effect.py` | +审批门机制 |
| `modules/registry.py` | +4个v10服务, +4个钩子 |
| `modules/game_engine.py` | +集成v10模块, +API方法 |
| `modules/turn_processor_v2.py` | +4个pipeline步骤 |
| `modules/agent_base.py` | +ranked search |
| `modules/npc_agent.py` | +程序性记忆集成 |
| `routes/systems_routes.py` | +8个v10端点 |
| `config.json` | +v10/v11配置段 |

---

## 🙏 致谢

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — 闭环学习、多智能体、记忆系统设计灵感
- [Nous Research](https://hermes-agent.nousresearch.com) — 自进化 Agent 架构

---

## 📄 许可证

MIT License
