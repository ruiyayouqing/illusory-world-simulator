# 太虚幻境 — 虚拟世界人生模拟器

> 基于 LLM 驱动的无限世界文字推演引擎 · 闭环学习 + 多智能体协调 + 插件化架构

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green.svg)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

---

## ✨ 项目简介

**太虚幻境**是一款基于大语言模型（LLM）驱动的虚拟世界人生模拟器。它通过先进的 AI 技术，构建了一个可以无限推演的文字虚拟世界。玩家可以在任意类型的世界中体验完整的人生经历——从历史穿越到奇幻冒险，从武侠江湖到修仙问道，从现代都市到末日生存，一切皆由你定义。

与传统的文字冒险游戏不同，太虚幻境中的世界是**活的**：NPC 拥有自主意识，世界会自行演化，每一个选择都可能引发蝴蝶效应。

---

## 🎯 核心特性

| 特性 | 说明 |
|------|------|
| 🌍 **无限世界生成** | 支持 8 种预设世界类型 + 自定义世界，AI 自动生成完整世界观 |
| 🧠 **自主 NPC 系统** | 每个 NPC 都有独立人格、记忆、目标，会自主行动和演化 |
| 📖 **动态叙事引擎** | 多智能体协作叙事，叙事审查，伏笔生命周期，连续性审计 |
| 🔄 **闭环学习系统** | 系统从叙事中学习，持续优化叙事质量和玩家体验 |
| 🦋 **蝴蝶效应** | 玩家的每个选择都可能引发连锁反应，高影响行为设有审批门 |
| 🧩 **插件化架构** | 战斗系统、经济系统、天气系统等可插拔扩展 |
| 🎨 **多风格叙事** | 章回体、网文爽文、严肃文学等 6 种叙事风格 |
| 🗺️ **可视化界面** | 世界地图、NPC 关系图谱、名人谱、人生传记 |
| 📚 **世界书支持** | 兼容 SillyTavern World Info 格式，导入即用 |
| 🎭 **角色卡支持** | 兼容 SillyTavern Character Card V2 格式，一键导入 NPC |

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────┐
│                 前端交互层                        │
│  (HTML/JS + Alpine.js + WebSocket)              │
├─────────────────────────────────────────────────┤
│                 API 路由层                       │
│  (FastAPI + REST API + WebSocket)               │
├─────────────────────────────────────────────────┤
│                 游戏引擎层                       │
│  (GameEngine + 事件总线 + 服务注册中心)          │
├─────────────────────────────────────────────────┤
│              核心业务模块层                      │
│  世界生成 │ 叙事引擎 │ NPC 系统 │ 记忆系统      │
│  玩家系统 │ 任务系统 │ 经济系统 │ 战斗系统      │
├─────────────────────────────────────────────────┤
│              基础服务层                          │
│  LLM 路由 │ 向量数据库 │ 关系数据库 │ 缓存     │
└─────────────────────────────────────────────────┘
```

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- 一个可用的 LLM API Key（支持 OpenAI 兼容接口）

### 安装运行

```bash
# 克隆项目
git clone https://github.com/your-username/taixuhuanjing.git
cd taixuhuanjing

# 安装依赖
pip install -r requirements.txt

# 启动服务
python server.py
```

然后访问 http://localhost:8004 即可开始游戏。

### 配置

复制 `config.json.example` 为 `config.json`，填入你的 API Key：

```json
{
  "llm": {
    "api_key": "your-api-key",
    "base_url": "https://api.deepseek.com",
    "model_name": "deepseek-chat"
  }
}
```

或者直接在游戏内的设置页面配置。

---

## 📚 世界书 & 角色卡

### 世界书（Lorebook）

世界书用于定义静态世界观设定，支持关键词触发和常量注入。

**支持格式：**
- 太虚幻境原生 Lorebook 格式
- SillyTavern World Info 格式

**使用方式：**
1. 在首页创建世界时上传世界书
2. 游戏内通过设置页面管理世界书条目

### 角色卡（Character Card）

角色卡用于导入预设 NPC，支持完整的角色属性、性格、说话风格等。

**支持格式：**
- 太虚幻境角色卡格式
- SillyTavern Character Card V2 格式

**使用方式：**
1. 在名人谱页面点击「添加角色」→「选择角色卡文件」
2. 查看角色详情时点击「📤 导出」可导出当前角色卡

---

## 🧠 核心系统详解

### 分层记忆系统

```
score = 向量相似度 × 0.45 + 重要性 × 0.25 + 时间衰减 × 0.15 + 情感权重 × 0.10 + 访问频率 × 0.05
```

| 层级 | 说明 | 特点 |
|------|------|------|
| 工作记忆 | 高重要性核心记忆 | 每次 prompt 注入 |
| 情景记忆 | ChromaDB 向量检索 | 30天半衰期衰减 |
| 语义记忆 | 身份/关系/世界观 | 长期稳定 |
| 世界书 | 静态世界观设定 | 关键词触发 |
| 角色卡 | NPC 设定 | 常驻上下文 |

### 闭环学习系统

```
叙事生成 → 回顾分析 → 教训提取 → 注入 prompt → 更好的叙事
    ↑                                              |
    └──────────────── 闭环 ─────────────────────────┘
```

- 每 5 回合自动回顾最近叙事
- 检查一致性、角色深度、节奏感
- 提取玩家偏好和叙事质量教训
- 教训直接注入到下一次叙事生成

### 蝴蝶效应审批门

```
玩家刺杀皇帝 → 影响分数 9.2 → 触发审批门
  预览：此行为将引发全国性内战
  [批准] [拒绝] [修改]
```

默认关闭，可通过设置开启。

---

## 🧩 插件系统

项目采用插件化架构，可灵活扩展功能。

### 内置插件

| 插件 | 说明 |
|------|------|
| `battle_system.py` | 战斗系统（回合制） |
| `achievements.py` | 成就系统 |
| `weather_enhanced.py` | 增强天气系统 |

### 插件开发

参考 `plugins/PLUGIN_DEV_GUIDE.md` 开发自己的插件。

---

## 📁 项目结构

```
taixuhuanjing/
├── modules/              # 核心模块
│   ├── game_engine.py   # 游戏引擎主类
│   ├── player_agent.py  # 玩家智能体
│   ├── npc_agent.py     # NPC 智能体
│   ├── context_engine.py # 上下文引擎
│   ├── lorebook.py      # 世界书
│   ├── character_card.py # 角色卡
│   └── ...
├── routes/               # API 路由
│   ├── game_routes.py   # 游戏核心 API
│   ├── npc_routes.py    # NPC 相关 API
│   ├── lorebook_routes.py # 世界书 API
│   ├── character_card_routes.py # 角色卡 API
│   └── ...
├── plugins/              # 插件目录
├── frontend/             # 前端静态资源
│   ├── js/
│   └── css/
├── saves/                # 存档目录（运行时生成）
├── config.json           # 配置文件（不入库）
├── config.json.example   # 配置模板
├── requirements.txt      # Python 依赖
├── server.py             # 主程序入口
└── index.html            # 前端首页
```

---

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

### 贡献方式

1. Fork 本仓库
2. 创建你的特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交你的改动 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启一个 Pull Request

详细信息请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 📄 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件。

---

## 🙏 致谢

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — 闭环学习、多智能体、记忆系统设计灵感
- [SillyTavern](https://github.com/SillyTavern/SillyTavern) — 世界书和角色卡格式参考
- [ChromaDB](https://www.trychroma.com/) — 向量数据库
- [FastAPI](https://fastapi.tiangolo.com/) — Web 框架

---

## ⚠️ 免责声明

本项目仅供学习和研究使用。使用时请遵守相关法律法规和 API 服务商的使用条款。
