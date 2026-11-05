# AgentProof

**给 AI 编程任务留下可核对的证据：本地快照、真实检查结果、交付报告。**

[English](README.md) · [示例报告](examples/report.md)

AI 说“完成了”，但它改了什么？测试真的跑过吗？测试通过之后代码又变了吗？
AgentProof 先解决这些具体问题，不需要模型、API Key 或云服务。

## 安装

需要 Python 3.10+、Git、macOS 或 Linux。第一版不支持 Windows，尚未发布到 PyPI。

```bash
git clone https://github.com/bzwei58/agentproof.git
cd agentproof
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

之后在需要检查的代码仓库中，用安装目录下 `.venv/bin/agentproof` 的绝对路径调用。
已有 pipx 的用户也可以在安装目录运行 `pipx install .`，获得全局命令。

## 使用

```bash
# 在你希望 AI 修改的 Git 仓库中，开始任务，保留当前未提交的改动
agentproof init --goal "修复支付重试逻辑"

# 使用原有 AI 工具修改代码后，保存阶段快照
agentproof checkpoint --label "第一次实现"

# 主动运行项目已有的检查；工具记录退出码和日志
agentproof verify -- python -m unittest discover -s tests
agentproof verify --timeout 60 -- npm run lint

# 可选：导入 Claude Code JSONL 会话的基础信号统计
agentproof import-session /path/to/session.jsonl

# 查看和生成报告
agentproof status
agentproof report

# 先预览恢复；加 --apply 才写文件，写入前自动备份
agentproof restore CHECKPOINT_ID
agentproof restore CHECKPOINT_ID --apply
```

命令在 Git 仓库根目录执行，每个 checkout 暂支持一个任务。另一个任务请用独立 worktree。
`verify` 不解释 shell 管道，复杂命令需显式 `sh -c '...'`；仅执行你信任的命令。
检查失败返回 1，操作或参数错误返回 2。完整输出保存在 `.agentproof/checks/`。
失败时终端直接显示最后 12 行、最多 4,000 字符的输出片段；超时会明确提示，成功时保持简洁。

## 第一版实际做到的事

- 保存 Git 已跟踪文件及未被忽略的新文件，保留初始未提交内容，不创建 Git 提交。
- 对照任务起点展示新增、修改和删除的文件。
- 用文件哈希把检查结果绑定到代码；检查后再改代码，结果会标记为 **STALE**。
- 长命令和多行脚本在报告中显示简短摘要，可展开 **Full command** 查看完整调用。
- 记录命令真实退出码、输出和超时；检查自己改了文件也会标记为旧证据。
- 导入 Claude Code 风格 JSONL，统计工具调用、工具错误、上下文压缩、截断标记及坏行。
- 生成本地 Markdown 报告，明确记录已知事实与未验证范围。

## 边界

它不能保证代码正确，也不会自动断言“需求已完成”。目前没有实时监听、自动测试发现、
模型风险评分或跨工具自动关联。会话只导入统计，不复制原始消息；格式变化可能导致漏计。
截断文本只是启发式信号，不代表重要信息必然丢失。

证据保存在 `.agentproof/`，初始化会加入本地 Git 排除。已忽略文件、符号链接、子模块内容、
Git 暂存状态和外部服务不在快照范围。`.env*`、私钥扩展名及 `credentials.json` 等名称默认排除，
但这不是秘密扫描器；检查命令输出仍可能包含敏感信息，请保持本地证据私密。

单文件超过 10 MiB 或总内容超过 100 MiB 会中止快照。记录或检查时请暂停其他工具的并发编辑。
同一仓库只允许一个 AgentProof 命令执行；异常中断后可检查并手动移除 `.agentproof/lock`。

**恢复只重写目标快照中的文件，后来新建的文件会保留。** 它是文件恢复，不是工作区精确回滚。
Git 历史和暂存区不会改变；恢复前会保存备份，拒绝符号链接路径，并校验快照内容哈希。
写入前会检查文件与目录冲突，包括被普通文件阻挡的父路径。恢复不是文件系统事务，并发修改或
写入错误仍可能中断操作。
被排除的文件不提供恢复保证。不要手动修改快照及其清单。

## 本地开发

```bash
python3 -m unittest discover -s tests -v
python3 -m agentproof --help
```

MIT 开源协议。欢迎提交可复现问题、会话格式样例或带测试的 PR。
