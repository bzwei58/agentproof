# AgentProof

**给 AI 编程任务留下可核对的证据：本地快照、真实检查结果、交付报告。**

[English](README.md) · [五分钟上手](docs/quickstart.zh-CN.md) · [示例报告](examples/report.md) · [变更日志](CHANGELOG.md) · [安全说明](SECURITY.md)

AI 说“完成了”，但它改了什么？测试真的跑过吗？测试通过之后代码又变了吗？
AgentProof 先解决这些具体问题，不需要模型、API Key 或云服务。

## 安装

需要 Python 3.10+、Git、macOS、Linux 或 Windows。已发布到 [PyPI](https://pypi.org/project/agentproof-cli/0.3.0/)。

已[安装 pipx 并配置 PATH](https://pipx.pypa.io/stable/installation/) 时，一条命令即可安装正式版本：

```bash
pipx install agentproof-cli==0.3.0
```

之后在任意项目执行 `agentproof --version`。如果提示找不到命令，执行 `pipx ensurepath` 并重开终端。
安装包名是 `agentproof-cli`，命令名是 `agentproof`；卸载命令为 `pipx uninstall agentproof-cli`。
如需体验未发布的开发代码，可改装 `git+https://github.com/bzwei58/agentproof.git@main`。

不用 pipx 也可以：先用 `python3 -m venv /你选择的路径/venv` 创建虚拟环境，再执行
`/你选择的路径/venv/bin/python -m pip install agentproof-cli==0.3.0`。
使用该目录下 `bin/agentproof` 的绝对路径调用，或激活虚拟环境。

建议先完成[五分钟教程](docs/quickstart.zh-CN.md)：在独立临时目录里体验失败、修复、旧检查失效和报告。

## 使用

```bash
# 在你希望 AI 修改的 Git 仓库中，开始任务，保留当前未提交的改动
agentproof init --goal "修复支付重试逻辑"

# 使用原有 AI 工具修改代码后，保存阶段快照
agentproof checkpoint --label "第一次实现"

# 主动运行项目已有的检查；工具记录退出码和日志
agentproof verify --kind test -- python -m unittest discover -s tests
agentproof verify --kind lint --timeout 60 -- npm run lint

# 可选：导入 Claude Code JSONL 会话的基础信号统计
agentproof import-session /path/to/session.jsonl

# 把 AI 最终总结变成人工审查清单（每行一条声明）
agentproof review --summary ai-summary.txt
# 或直接输入声明
agentproof review --claim "修复了支付重试问题" --claim "所有测试通过"

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
- `review` 将 AI 的完成声明与文件变化、最新检查结果匹配，自然语言声明保持 **UNVERIFIED（待核实）**；匹配的命令执行记录单独展示。

## 边界

它不能保证代码正确，也不会自动断言“需求已完成”。`review` 使用透明规则、不调用模型，
也不判断需求本身是否正确。目前没有实时监听、自动测试发现、
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
git clone https://github.com/bzwei58/agentproof.git
cd agentproof
python3 -m venv .venv
.venv/bin/python -m pip install -e .
python3 -m unittest discover -s tests -v
python3 -m agentproof --help
```

愿意在两个真实任务中试用？查看[试用指南和反馈表](docs/pilot.md)。

MIT 开源协议。欢迎提交可复现问题、会话格式样例或带测试的 PR。

## 0.3.1 的证据匹配

使用 `verify --kind test|lint|build|other -- COMMAND` 明确声明检查类型。
类型由调用者提供，不是认证；旧检查不会自动获得类型。
自然语言完成声明保持 `UNVERIFIED`，文件变化或命令返回 0 都不能证明具体需求已经完成。
匹配的命令执行记录单独展示，仍需确认测试是否实际执行、数量、覆盖范围及业务要求。

报告分别显示历史执行结果、当前文件匹配情况及日志完整性：`INTACT`（完整）、
`MODIFIED`（已变化）、`MISSING`（缺失）、`UNRECORDED`（未记录哈希）、`UNREADABLE`（无法读取）。
只有成功、匹配当前文件且日志完整的检查能列为匹配记录。每次 `status` 或 `report` 都重新计算；
已导出的 Markdown 是静态快照。哈希可检测部分变化，但不是防止本地记录被整体改写的独立审计证明。
外部服务、被忽略的依赖和环境变化尚不在指纹范围。

会话导入识别 Claude 消息块与 Codex 的 `response_item.payload`、`compacted` 记录。
Cline、Cursor 原生格式尚未验证；通用事件统计不等于完整支持。
