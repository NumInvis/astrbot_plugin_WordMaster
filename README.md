# Wordle + Handle 合并版游戏插件

🎮 移植自 nonebot-plugin-wordle 和 nonebot-plugin-handle 的 AstrBot 游戏插件

## ✨ 功能特性

- 🔤 **Wordle** - 经典的英文猜单词游戏
- 🀄 **Handle** - 汉字版 Wordle，猜成语游戏
- 📚 **多词典支持** - CET4/CET6/GRE/IELTS/TOEFL 等多种英文词典
- 🔒 **严格模式** - Handle 游戏可开启成语验证
- 🎨 **可视化反馈** - 使用 Emoji 颜色块直观显示猜测结果
- 👑 **权限管理** - 支持管理员权限控制

## 🚀 安装

### 方式一：通过 AstrBot 插件市场安装

1. 打开 AstrBot 管理面板
2. 进入「插件」→「插件市场」
3. 搜索 `Wordle` 或 `Handle` 并安装

### 方式二：手动安装

1. 克隆仓库到插件目录：
   ```bash
   cd /path/to/astrbot/data/plugins
   git clone https://github.com/NumInvis/astrbot_plugin_wordle_handle.git
   ```

2. 重启 AstrBot

## ⚙️ 配置

在 AstrBot 插件配置面板中设置：

| 配置项 | 类型 | 说明 | 默认值 |
|--------|------|------|--------|
| `admin_users` | 列表 | 管理员用户ID列表 | `[]` |

## 📖 使用指南

### 命令列表

#### 🎮 游戏命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/wordle [词典] [长度]` | 开始英文猜单词游戏 | `/wordle CET4 5` |
| `/handle [--strict]` | 开始汉字猜成语游戏 | `/handle --strict` |
| `/结束游戏` | 结束当前游戏 | `/结束游戏` |
| `/wordle帮助` | 显示帮助信息 | `/wordle帮助` |

### 使用示例

#### 1️⃣ 开始 Wordle 游戏
```
/wordle CET4 5
```
输出：
```
🎮 Wordle 游戏开始！
📚 词典: CET4
🔤 单词长度: 5
🎯 你有 6 次机会猜出单词

💡 提示:
🟩 绿色 = 字母正确且位置正确
🟨 黄色 = 字母存在但位置错误
⬜ 灰色 = 字母不存在

发送你的猜测（如: apple）或发送 "结束" 结束游戏
```

#### 2️⃣ 猜测单词
发送：`apple`

输出：
```
🟨A 🟩P ⬜P ⬜L ⬜E

📝 第 1/6 次猜测
💭 还剩 5 次机会
```

#### 3️⃣ 开始 Handle 游戏
```
/handle
```

#### 4️⃣ 猜测成语
发送：`一心一意`

输出：
```
🟩一🟩yi🟩i🟩1 ⬜心⬜x⬜in⬜1 ⬜意⬜y⬜i🟩4 ⬜意⬜y⬜i🟩4

📝 第 1/10 次猜测
💭 还剩 9 次机会
```

### 🎮 游戏规则

#### Wordle - 英文猜单词
- 猜一个指定长度的英文单词
- 🟩 **绿色** = 字母正确且位置正确
- 🟨 **黄色** = 字母存在但位置错误
- ⬜ **灰色** = 字母不存在
- 共 **6** 次机会

#### Handle - 汉字猜成语
- 猜一个**四字成语**
- 🟩 **绿色** = 正确
- 🟨 **黄色** = 存在但位置错误
- ⬜ **灰色** = 不存在
- 每个格子显示：**汉字 + 声母 + 韵母 + 声调**
- 共 **10** 次机会

### 💡 游戏中指令

- `结束` / `退出` / `quit` - 结束当前游戏
- `提示` / `hint` - 获取提示

## 📚 可用词典

| 词典 | 说明 |
|------|------|
| CET4 | 大学英语四级词汇 |
| CET6 | 大学英语六级词汇 |
| GRE | GRE词汇 |
| IELTS | 雅思词汇 |
| TOEFL | 托福词汇 |

## 📝 更新日志

### v1.0.0
- 🎉 初始版本发布
- ✨ 支持 Wordle 英文猜单词
- ✨ 支持 Handle 汉字猜成语
- 📚 内置多级别英文词典
- 🀄 内置常用成语库
- 🎨 Emoji 可视化反馈

## 🤝 致谢

- 原项目：[nonebot-plugin-wordle](https://github.com/noneplugin/nonebot-plugin-wordle)
- 原项目：[nonebot-plugin-handle](https://github.com/noneplugin/nonebot-plugin-handle)
- 框架支持：[AstrBot](https://github.com/Soulter/AstrBot)

## 📄 License

MIT License

---

<p align="center">
  Made with ❤️ for word game lovers
</p>
