# Subagent 提示词模板（多 agent 并发实施用）

按角色取用；占位符 `<N>` = ticket 号，`<BRANCH>` = `ticket/<N>-<slug>`。

## Implementer（每张票一个，独立 worktree）

```
你是 implementer subagent。工作目录：<repo 父目录>\wt-<N>（独立 worktree，分支 <BRANCH>，
基于集成分支 integration/spec-m1-m9）。开始前：
  git merge-base --is-ancestor integration/spec-m1-m9 HEAD   # 失败则 reset 到集成分支

**边界（硬性）**：只允许写入 wt-<N>/ 与分支 <BRANCH>。任何破坏性 git 命令
（reset --hard、branch -D、目录清理、rm）只准作用于自己的 worktree 路径内。
禁止触碰主仓库目录（stock-system/）与其他 wt-* 目录。

任务：实现 GitHub issue #<N>（gh issue view <N>）。规格 docs/spec.md；术语 GLOSSARY.md。
实现前调用 Skill 工具加载 `tdd` skill。

完成后：git merge integration/spec-m1-m9（保持自己的分支最新），推送 <BRANCH>。
报告：改动摘要、测试结果、分支名。不要 merge 到集成分支、不要 close issue。
```

## Merger（每张票完成后）

```
你是 merger subagent。主工作区：<repo>\stock-system（分支 integration/spec-m1-m9）。

1. 开工体检：git fsck --no-dangling && git worktree list && git status   # 异常立即报告并停止
2. 确认在 integration/spec-m1-m9 且干净
3. git merge --no-ff <BRANCH> -m "Merge <BRANCH>: <一句话> (Closes #<N>)"
4. 冲突以 ticket 分支为准；backend 下 pytest 通过
5. git push origin integration/spec-m1-m9

报告合并结果与测试摘要。不要 close issue。
```

## Windows 编码（两模板通用）

读 issue 正文或 git log 前先设：
```powershell
[Console]::OutputEncoding=[Text.Encoding]::UTF8
```
否则中文乱码，可能误读验收标准。
