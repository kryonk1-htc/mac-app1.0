"""暂存区草稿（纯本机）。

定位：文本 + 图片的随手暂存，跟待办完全两套语义——不进同步、不进 outbox、
不参与任何推送。图片本体放数据目录 scratch-images/，库里只存文件名，
这样换机器/清目录时不会留下悬空引用（删草稿时连文件一起清）。

命名规则：默认「草稿1、草稿2、…」，取现有名字里最大的编号 +1，
删除中间的草稿不会导致编号复用（避免「草稿2」指过两个不同内容的混淆）。
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from .db import database
from .paths import SCRATCH_IMG_DIR

_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def list_drafts() -> list[dict[str, Any]]:
    """草稿列表（按更新时间倒序），附每条图片数与内容摘要。"""
    rows = database.conn().execute(
        "SELECT s.id, s.name, s.content, s.created_at, s.updated_at,"
        " (SELECT COUNT(*) FROM scratch_image i WHERE i.scratch_id = s.id) AS img_count"
        " FROM scratch s WHERE 1=1 ORDER BY s.updated_at DESC, s.id DESC"
    ).fetchall()
    out = []
    for row in rows:
        content = row["content"] or ""
        out.append(
            {
                "id": row["id"],
                "name": row["name"],
                "content": content,
                "preview": content.replace("\n", " ").strip()[:30],
                "img_count": row["img_count"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    return out


def get_draft(draft_id: int) -> dict[str, Any] | None:
    row = database.conn().execute(
        "SELECT id, name, content, created_at, updated_at FROM scratch WHERE id = ?",
        (draft_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "content": row["content"],
        "images": list_images(draft_id),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _max_serial() -> int:
    """现有「草稿N」里的最大 N；没有就 0。"""
    rows = database.conn().execute("SELECT name FROM scratch").fetchall()
    biggest = 0
    for row in rows:
        match = re.fullmatch(r"草稿(\d+)", row["name"] or "")
        if match:
            biggest = max(biggest, int(match.group(1)))
    return biggest


def create_draft(name: str = "") -> int:
    """新建草稿，返回 id。默认名「草稿{max+1}」。"""
    if not name.strip():
        name = f"草稿{_max_serial() + 1}"
    with database.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO scratch (name, content, created_at, updated_at) VALUES (?, '', ?, ?)",
            (name.strip(), _now(), _now()),
        )
        return int(cursor.lastrowid)


def rename_draft(draft_id: int, name: str) -> None:
    name = name.strip()
    if not name:
        return
    with database.transaction() as conn:
        conn.execute(
            "UPDATE scratch SET name = ?, updated_at = ? WHERE id = ?",
            (name, _now(), draft_id),
        )


def save_content(draft_id: int, content: str) -> None:
    """只更新文字内容（图片走 add/remove_image）。"""
    with database.transaction() as conn:
        conn.execute(
            "UPDATE scratch SET content = ?, updated_at = ? WHERE id = ?",
            (content, _now(), draft_id),
        )


def delete_draft(draft_id: int) -> None:
    """删草稿 + 图片记录 + 图片文件（文件不存在不报错）。"""
    with database.transaction() as conn:
        for row in conn.execute(
            "SELECT filename FROM scratch_image WHERE scratch_id = ?", (draft_id,)
        ).fetchall():
            try:
                (SCRATCH_IMG_DIR / row["filename"]).unlink(missing_ok=True)
            except OSError:
                pass
        conn.execute("DELETE FROM scratch_image WHERE scratch_id = ?", (draft_id,))
        conn.execute("DELETE FROM scratch WHERE id = ?", (draft_id,))


def list_images(draft_id: int) -> list[dict[str, Any]]:
    rows = database.conn().execute(
        "SELECT id, filename, position FROM scratch_image WHERE scratch_id = ?"
        " ORDER BY position, id",
        (draft_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "filename": row["filename"],
            "path": str(SCRATCH_IMG_DIR / row["filename"]),
        }
        for row in rows
    ]


def add_image(draft_id: int, src_path: str) -> dict[str, Any] | None:
    """把一张图片收进草稿：复制进数据目录，返回图片记录。

    非图片扩展名直接拒绝；同内容多份拷贝用 uuid 文件名隔离，不查重。
    """
    import shutil
    from pathlib import Path

    src = Path(src_path)
    if src.suffix.lower() not in _ALLOWED_EXT:
        return None
    filename = f"{uuid.uuid4().hex}{src.suffix.lower()}"
    SCRATCH_IMG_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, SCRATCH_IMG_DIR / filename)
    with database.transaction() as conn:
        nxt = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM scratch_image WHERE scratch_id = ?",
            (draft_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO scratch_image (scratch_id, filename, position) VALUES (?, ?, ?)",
            (draft_id, filename, nxt),
        )
        conn.execute(
            "UPDATE scratch SET updated_at = ? WHERE id = ?", (_now(), draft_id)
        )
    return {"filename": filename, "path": str(SCRATCH_IMG_DIR / filename)}


def remove_image(draft_id: int, image_id: int) -> None:
    row = database.conn().execute(
        "SELECT filename FROM scratch_image WHERE id = ? AND scratch_id = ?",
        (image_id, draft_id),
    ).fetchone()
    if not row:
        return
    with database.transaction() as conn:
        conn.execute("DELETE FROM scratch_image WHERE id = ?", (image_id,))
        conn.execute(
            "UPDATE scratch SET updated_at = ? WHERE id = ?", (_now(), draft_id)
        )
    try:
        (SCRATCH_IMG_DIR / row["filename"]).unlink(missing_ok=True)
    except OSError:
        pass
