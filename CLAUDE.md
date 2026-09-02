# Conventions

- TDD: tests before implementation. Tests make no network calls and use fixtures.
- Run `pytest` and `ruff check .` before reporting a task done.
- Topic-specific knowledge lives in `config/*.yaml`, never in `src/`. Adapters validate their own options.
- The Slack webhook URL never exists as a value anywhere in this codebase. The repo is public and Actions logs are public.
- Don't add dependencies without saying why.
- Don't create files the prompt didn't ask for. If you think one is needed, say so and wait.
- Flag judgment calls explicitly rather than burying them in a summary.
- Never remove or reconstruct working code to shape a commit. Stage with `git add -p` from the real files, or say the split isn't clean and take a coarser commit.
- After splitting commits, verify with `git status` and `git log --stat`.
- If a task has been redirected twice, stop and ask rather than trying a third approach.
