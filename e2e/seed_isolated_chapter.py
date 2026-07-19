"""Seed one accepted chapter only inside the disposable Playwright database."""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: seed_isolated_chapter.py DB_PATH PROJECT_ID")

    database = Path(sys.argv[1]).resolve()
    if ".e2e-state" not in database.parts or database.name != "novel-os-e2e.db":
        raise SystemExit(f"refusing non-isolated database: {database}")
    project_id = str(uuid.UUID(sys.argv[2]))
    chapter_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    content = (
        "隔离验收前置正文。人物在既定时间线内完成选择，并承担明确代价；"
        "该文本只用于验证暂停后的持久化恢复能力，不来自也不触发模型调用。"
    )

    connection = sqlite3.connect(database, timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        project = connection.execute(
            "SELECT id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if project is None:
            raise SystemExit(f"project does not exist: {project_id}")
        existing = connection.execute(
            "SELECT id FROM chapters WHERE project_id = ? AND chapter_no = 1",
            (project_id,),
        ).fetchone()
        if existing is not None:
            raise SystemExit(f"chapter 1 already exists: {existing[0]}")

        with connection:
            connection.execute(
                """
                INSERT INTO chapters (
                    id, project_id, chapter_no, title, status, word_count,
                    target_words, current_version_id, created_at, updated_at
                ) VALUES (?, ?, 1, ?, 'approved', ?, 3000, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (chapter_id, project_id, "隔离验收前置章", len(content)),
            )
            connection.execute(
                """
                INSERT INTO chapter_versions (
                    id, chapter_id, version_no, content, word_count, status,
                    created_by_agent, created_at
                ) VALUES (?, ?, 1, ?, ?, 'approved', 'E2EFixture', CURRENT_TIMESTAMP)
                """,
                (version_id, chapter_id, content, len(content)),
            )
            connection.execute(
                "UPDATE chapters SET current_version_id = ? WHERE id = ?",
                (version_id, chapter_id),
            )
            connection.execute(
                "UPDATE projects SET current_chapter_no = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (project_id,),
            )
    finally:
        connection.close()

    print(
        json.dumps(
            {
                "ok": True,
                "project_id": project_id,
                "chapter_id": chapter_id,
                "version_id": version_id,
                "status": "approved",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
