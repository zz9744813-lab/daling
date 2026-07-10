# Trae Repository Rules

1. 不允许一次性修改整个仓库。
2. 每次任务只完成一个 Phase。
3. 修改前必须阅读目标文件和测试。
4. 不允许通过删除测试、跳过测试、降低断言来制造通过。
5. 不允许用默认成功值掩盖异常。
6. 不允许 LLM 失败后生成占位正文并继续。
7. 所有核心 LLM 调用必须流式（Phase 4 后生效）。
8. 所有任务必须持久化（Phase 2 后生效）。
9. 所有重写必须生成新版本，不覆盖旧版本。
10. 未批准章节不得提交正式记忆。
11. 数据库结构修改必须使用 Alembic（Phase 2 后生效）。
12. 任何状态修改必须经过状态机（Phase 2 后生效）。
13. 每个 Phase 完成后运行：
    - backend pytest
    - backend ruff
    - frontend typecheck
    - frontend build
14. 失败时修复根因，不允许静默 catch Exception。
15. 不允许使用 broad except 后继续成功流程。
16. 每次提交必须给出：
    - 修改文件
    - 行为变化
    - 测试结果
    - 剩余风险
