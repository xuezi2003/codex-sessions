# codex-sessions

一个简单的本地 Codex Session 浏览器。

它直接读取 `~/.codex` 里的真实数据，而不是依赖 `codex resume` 的列表过滤，所以可以查看本机上的全部 session，并在终端里快速恢复某一条会话。

## 功能

- 查看所有本地 session
- 默认按北京时间倒序显示
- 终端表格式浏览：时间、Session ID、标题、最后一句话
- 主界面支持 `/` 搜索，可在标题、Session ID、Provider、CWD 和完整聊天记录中搜索
- 搜索索引基于本地 SQLite FTS5 `trigram`，会自动增量更新
- 回车查看美化后的详情界面
- 按 `e` 直接执行 `codex resume <session_id>`
- 固定使用北京时间显示时间

## 使用

```bash
codex-sessions
```

主界面快捷键：

```text
/        进入搜索
c        清空搜索
Enter    查看详情
e        恢复当前 session
q        退出
```

可选参数：

```bash
codex-sessions --list
codex-sessions --show <SESSION_ID>
codex-sessions --search <QUERY>
```

## 数据来源

- `~/.codex/state_*.sqlite`
- `~/.codex/sessions/**/*.jsonl`
- `~/.codex/archived_sessions/**/*.jsonl`

## 代码结构

```text
codex-sessions              命令入口
codex_sessions/store.py     session 读取、文本清洗、纯文本输出
codex_sessions/search.py    本地全文索引与增量搜索
codex_sessions/tui.py       curses 交互界面
codex_sessions/cli.py       参数解析与启动流程
```
