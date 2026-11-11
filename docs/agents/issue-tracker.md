# Issue tracker: GitHub

> **Windows 会话**：读 issue 正文（`gh issue view`）或 git log 前，先执行
> `[Console]::OutputEncoding=[Text.Encoding]::UTF8`，否则中文乱码可能误读验收标准。

Issues and specs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --json number,title,body,labels,comments`.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Blocking**: GitHub's **native issue dependencies**. Add an edge with `gh issue create --blocked-by <n>` or `gh issue edit <child> --add-blocked-by <blocker>`. A ticket is unblocked when every blocker is closed.
- **Count open blockers**: `gh api repos/<owner>/<repo>/issues/<n> --jq .issue_dependencies_summary.blocked_by` (0 means startable).
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: post the explanation with `gh issue comment`, then `gh issue close <number>`.

Infer the repo from `git remote -v`; `gh` does this automatically inside a clone.

File issues only into the user's own repo (`sishoui/stock-system`).

## Pull requests as a triage surface

**PRs as a request surface: no.**

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Read it as in **Read an issue** above.
