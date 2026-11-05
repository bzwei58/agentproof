# 五分钟生成第一份报告

[English](quickstart.md) · [安装](../README.zh-CN.md#安装) · [真实任务试用](pilot.md)

先确认终端里可以运行 `agentproof`、Python 3.10+ 和 Git。教程会在临时目录创建一个故意写错的
示例项目，用来理解流程，不代表工具会自动发现 Bug。请在**同一个终端**依次执行，
不要开启 `set -e`，因为其中一次检查本来就应该失败。

## 1. 创建独立示例

```bash
AGENTPROOF_DEMO=$(mktemp -d "${TMPDIR:-/tmp}/agentproof-demo.XXXXXX")
cd "$AGENTPROOF_DEMO"
git init -q
printf '__pycache__/\n' > .gitignore
cat > price.py <<'PY'
def total_price(unit_price, quantity):
    return unit_price + quantity
PY
cat > test_price.py <<'PY'
import unittest
from price import total_price

class PriceTest(unittest.TestCase):
    def test_three_items(self):
        self.assertEqual(total_price(12, 3), 36)
PY
```

`.gitignore` 避免 Python 缓存进入快照。真实项目也应使用原有规则忽略生成的缓存、构建产物。

## 2. 开始任务，记录失败

```bash
agentproof init --goal "Correct the total price for multiple items"
agentproof verify -- python3 -B -m unittest discover -v
```

预期看到 **FAIL (exit 1)**，错误中包含 `15 != 36`，完整日志会保留。这里失败是正常结果，请继续。

## 3. 修复并验证

```bash
cat > price.py <<'PY'
def total_price(unit_price, quantity):
    return unit_price * quantity
PY
agentproof checkpoint --label "Correct quantity calculation"
agentproof verify -- python3 -B -m unittest discover -v
```

预期看到 **PASS (exit 0)**。这里确实执行了一个测试，不是“零测试通过”。
记下输出中的 checkpoint ID，稍后可以用它预览恢复。

## 4. 观察旧结果失效

```bash
printf '\n# Temporary review note\n' >> price.py
agentproof report
agentproof status
```

刚才通过的检查现在会标记为 **STALE / files changed**。即使只加注释，文件指纹也会改变。
工具不会自行判断这个改动是否影响行为。

## 5. 验证当前代码，查看报告

```bash
agentproof verify -- python3 -B -m unittest discover -v
agentproof report
printf 'Report: %s/.agentproof/report.md\n' "$PWD"
```

打开输出路径中的报告。最新一次通过应显示 **matches current captured files**，早期记录仍保留。
日志和快照位于 `.agentproof/`，不会进入 Git。若想预览恢复，在 `agentproof restore` 后填上
刚才记下的 checkpoint ID；默认只预览，不写文件。加 `--apply` 才会在自动备份后写入，
后来新建的文件仍保留，因此不是工作区精确回滚。

## 放到真实任务中使用

在一个新的 checkout/worktree 中，在 AI 修改前执行 `agentproof init --goal "任务目标"`。
修改后，用 `agentproof verify -- 项目已有的测试命令` 记录检查，再生成报告。
应使用项目实际的测试、构建或 lint 命令，工具不会自动发现检查命令。接受修改前仍需读日志和 diff。

每个 checkout 暂支持一个任务。[试用指南](pilot.md) 提供了两个真实任务的反馈方式。
如果提示找不到命令，请完成 pipx 的 PATH 配置并重开终端。如果初始化超过单文件 10 MiB 或
总内容 100 MiB 上限，先选一个较小项目或 worktree；已被 Git 跟踪的文件不会因为后来加入
`.gitignore` 就被排除。
