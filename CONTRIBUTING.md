# 贡献指南

感谢你对太虚幻境的兴趣！我们欢迎所有形式的贡献，包括但不限于：

- 🐛 提交 Bug 报告
- 💡 提出新功能建议
- 📝 改进文档
- 🔧 提交代码修复
- ✨ 实现新功能

---

## 📋 行为准则

参与本项目时，请遵守以下准则：

- 尊重他人，友善交流
- 接受不同的观点和意见
- 专注于对社区最有利的事情
- 对其他贡献者保持同理心

---

## 🚀 如何开始

### 环境搭建

```bash
# 1. Fork 并克隆仓库
git clone https://github.com/your-username/taixuhuanjing.git
cd taixuhuanjing

# 2. 创建虚拟环境
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# 3. 安装依赖
pip install -r requirements.txt
pip install -r requirements-dev.txt  # 开发依赖

# 4. 复制配置文件
copy config.json.example config.json
# 编辑 config.json，填入你的 API Key

# 5. 启动开发服务器
python server.py
```

访问 http://localhost:8004 验证环境是否正常。

---

## 🐛 提交 Bug 报告

提交 Issue 时，请包含以下信息：

### 必要信息

- **操作系统**：Windows 11 / macOS 14 / Ubuntu 22.04 等
- **Python 版本**：`python --version`
- **问题描述**：清晰简洁地描述问题
- **复现步骤**：如何复现这个问题
- **预期行为**：你期望发生什么
- **实际行为**：实际发生了什么

### 可选信息

- 错误日志 / 截图
- 配置信息（记得隐藏 API Key！）
- 相关的存档文件

---

## 💡 提出功能建议

提出新功能时，请说明：

- 这个功能解决了什么问题？
- 你期望的实现方式是什么？
- 有没有类似的参考项目？

---

## 🔧 提交代码

### 开发流程

1. **Fork 本仓库**到你的账号
2. **创建特性分支**：
   ```bash
   git checkout -b feature/your-feature-name
   # 或
   git checkout -b fix/your-bug-fix
   ```

3. **编写代码**：
   - 遵循现有代码风格
   - 添加必要的注释
   - 确保不引入新的依赖（除非必要）

4. **测试你的改动**：
   ```bash
   python -m pytest tests/
   # 或手动测试相关功能
   ```

5. **提交改动**：
   ```bash
   git add .
   git commit -m "feat: 添加某某功能"
   # 或
   git commit -m "fix: 修复某某问题"
   ```

6. **推送到你的 Fork**：
   ```bash
   git push origin feature/your-feature-name
   ```

7. **创建 Pull Request**

### Commit 规范

请使用以下格式的 commit message：

```
<type>: <subject>

<body>  # 可选
```

**Type 类型：**

| type | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档更新 |
| `style` | 代码格式（不影响功能） |
| `refactor` | 重构 |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `chore` | 构建/工具链相关 |

### Pull Request 规范

PR 标题应清晰描述改动内容。PR 描述应包含：

- **改动说明**：你做了什么改动？
- **相关 Issue**：Fixes #123
- **测试方式**：如何验证你的改动？
- **截图**：如果是 UI 改动，请附上截图

---

## 📁 项目结构

```
taixuhuanjing/
├── modules/        # 核心业务模块
├── routes/         # API 路由
├── plugins/        # 插件系统
├── frontend/       # 前端静态资源
│   ├── js/
│   └── css/
├── tests/          # 测试文件
├── server.py       # 主入口
├── index.html      # 前端首页
└── ...
```

### 模块说明

| 模块 | 说明 |
|------|------|
| `game_engine.py` | 游戏引擎核心，协调所有子系统 |
| `player_agent.py` | 玩家智能体，处理玩家输入和上下文构建 |
| `npc_agent.py` | NPC 智能体，NPC 自主行为 |
| `context_engine.py` | 上下文引擎，分层记忆管理 |
| `lorebook.py` | 世界书系统 |
| `character_card.py` | 角色卡系统 |
| `narrative_engine.py` | 叙事生成引擎 |

---

## 🎨 代码风格

### Python

- 遵循 PEP 8
- 使用 4 空格缩进
- 函数和类应有 docstring
- 类型注解是可选的，但推荐使用

### JavaScript

- 使用 2 空格缩进
- 优先使用 `const` / `let`，避免 `var`
- 函数名使用 camelCase
- 保持与现有代码风格一致

---

## ❓ 常见问题

### 在哪里讨论？

- **Bug 报告 / 功能建议**：GitHub Issues
- **开发讨论**：GitHub Discussions

### 我是新手，可以贡献吗？

当然可以！我们会标记一些适合新手的 issue：

- `good first issue` - 适合新手的任务
- `help wanted` - 需要帮助的任务

---

## 📜 许可证

提交的代码将在 MIT 许可证下发布。

---

再次感谢你的贡献！🎉
