# codex-sessions

一个简单的本地 Codex Session 浏览器。

它直接读取 `~/.codex` 里的真实数据，而不是依赖 `codex resume` 的列表过滤，所以可以查看本机上的全部 session，并在终端里快速恢复某一条会话。

## 功能

- 查看所有本地 session
- 终端表格式浏览：时间、Session ID、标题、最后一句话
- 回车查看精简对话记录
- 按 `e` 直接执行 `codex resume <session_id>`
- 固定使用北京时间显示时间

## 使用

```bash
codex-sessions
```

可选参数：

```bash
codex-sessions --list
codex-sessions --show <SESSION_ID>
```

## 数据来源

- `~/.codex/state_*.sqlite`
- `~/.codex/sessions/**/*.jsonl`
- `~/.codex/archived_sessions/**/*.jsonl`
