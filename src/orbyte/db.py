"""本地 SQLite（主库）。

为什么是主库而不是缓存：断网时必须能正常增删改，所以本地先落盘、
服务器只是副本。由此带来两个硬性要求，本模块与 todo_store 一起保证：

1. **业务表与待发队列必须同一事务写**。分两次写就会出现
   "改动生效了但没进队列"，这条数据永远不会上传，且没有任何迹象。
2. **连接按线程隔离**。UI 线程与同步线程各用各的连接（sqlite3 的
   连接不允许跨线程共享），WAL 模式下并发读写互不阻塞。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .paths import DB_PATH

logger = logging.getLogger("orb.db")

#: 当前库结构版本，改表时 +1 并在 _MIGRATIONS 里补一条
SCHEMA_VERSION = 12

#: 建表语句（全新安装直接跑这个）
_SCHEMA = """
CREATE TABLE IF NOT EXISTS todo (
  uuid        TEXT PRIMARY KEY,
  title       TEXT NOT NULL DEFAULT '',
  project     TEXT NOT NULL DEFAULT '',
  -- todo=未开始(红) / doing=进行中(黄) / done=已完成(绿)
  state       TEXT NOT NULL DEFAULT 'todo',
  -- work=工作（会同步） / life=生活（永不出本机）
  space       TEXT NOT NULL DEFAULT 'work',
  position    INTEGER NOT NULL DEFAULT 0,
  started_at  TEXT,
  spent_sec   INTEGER NOT NULL DEFAULT 0,
  -- P0..P2，空串 = 未设置；due_date 只到天（YYYY-MM-DD）
  priority    TEXT NOT NULL DEFAULT '',
  due_date    TEXT,
  -- 计划工时（分钟），0 = 未填
  planned_min INTEGER NOT NULL DEFAULT 0,
  -- 双向同步映射键：应用侧 work_log_table.id。
  -- 应用拉来的条目靠它识别「这条在应用里是哪一行」，改动回写才不会建重复行
  log_id      TEXT,
  -- 应用侧项目 id（work_log_table.project_id）：主页按完整项目树挂载条目用，
  -- 只有一级项目名时深层节点没法定位
  app_project_id TEXT,
  -- local=仅本地 / pending=待上传 / synced=已上传 / conflict=冲突待处理
  sync_state  TEXT NOT NULL DEFAULT 'local',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  deleted_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_todo_list ON todo (space, deleted_at, position, created_at);

-- 待发队列：只有 space='work' 的记录才允许进来（见 todo_store.write_tx）
CREATE TABLE IF NOT EXISTS outbox (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  uuid       TEXT NOT NULL,
  -- pending=待发 / inflight=已发出等确认 / acked=服务端已确认
  state      TEXT NOT NULL DEFAULT 'pending',
  retries    INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outbox_state ON outbox (state, seq);

-- 工时采集：当前正在计时的那一条（同一时刻最多一条）
CREATE TABLE IF NOT EXISTS active_session (
  uuid       TEXT PRIMARY KEY,
  started_at TEXT NOT NULL
);

-- 工时流水：每结束一段就落一条，「今天 2h / 累计 6.5h」靠它算
-- （只累计 spent_sec 的话，"今天"永远算不出来）
CREATE TABLE IF NOT EXISTS work_session (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  uuid       TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at   TEXT NOT NULL,
  sec        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_work_session_uuid ON work_session (uuid, started_at);

-- 项目排序表：项目本身在服务端只是待办上的一个字符串，
-- 本地单独存一行只为了「空项目也能显示」和「组序稳定」
CREATE TABLE IF NOT EXISTS project (
  name       TEXT PRIMARY KEY,
  sort       INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  deleted_at TEXT
);

-- 键值：令牌、服务器地址、配对状态、上次同步时间
CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY,
  v TEXT
);

-- 暂存区草稿：纯本机数据，永不同步（和生活待办同一隔离级别）
CREATE TABLE IF NOT EXISTS scratch (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL DEFAULT '',
  content    TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- 草稿图片：本体在数据目录 scratch-images/，库里只存文件名和顺序
CREATE TABLE IF NOT EXISTS scratch_image (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  scratch_id INTEGER NOT NULL,
  filename   TEXT NOT NULL,
  position   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_scratch_image ON scratch_image (scratch_id, position);
"""

#: 增量迁移：(目标版本, 该版本要执行的全部语句)，只追加、不改历史项。
#:
#: 为什么按"版本"分组而不是平铺一串语句：早期实现用 enumerate 的下标当版本号
#: 跟 user_version 比大小，一条版本里有多条语句时下标会错位 —— 升级过半的老库
#: 会把后面某条 ALTER 永久跳过（典型的 "no such column: priority"），
#: 而结尾又把 user_version 直接写成最新，这个缺口再也补不回来。
_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        # v1 → v2：加入 outbox.retries（早期版本没有退避计数）
        2,
        ("ALTER TABLE outbox ADD COLUMN retries INTEGER NOT NULL DEFAULT 0",),
    ),
    (
        # v2 → v3：待办排序位（拖动排序用）
        3,
        ("ALTER TABLE todo ADD COLUMN position INTEGER NOT NULL DEFAULT 0",),
    ),
    (
        # v3 → v4：优先级 / 截止日 / 计划工时
        4,
        (
            "ALTER TABLE todo ADD COLUMN priority TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE todo ADD COLUMN due_date TEXT",
            "ALTER TABLE todo ADD COLUMN planned_min INTEGER NOT NULL DEFAULT 0",
        ),
    ),
    (
        # v5 → v6：工时流水（算「今日工时」）
        6,
        (
            """CREATE TABLE IF NOT EXISTS work_session (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 uuid TEXT NOT NULL,
                 started_at TEXT NOT NULL,
                 ended_at TEXT NOT NULL,
                 sec INTEGER NOT NULL DEFAULT 0
               )""",
            "CREATE INDEX IF NOT EXISTS idx_work_session_uuid "
            "ON work_session (uuid, started_at)",
        ),
    ),
    (
        # v7 → v8：项目表（空项目 + 组序）
        8,
        (
            """CREATE TABLE IF NOT EXISTS project (
                 name TEXT PRIMARY KEY,
                 sort INTEGER NOT NULL DEFAULT 0,
                 created_at TEXT NOT NULL,
                 deleted_at TEXT
               )""",
        ),
    ),
    (
        # v8 → v9：双向同步映射键（应用侧 work_log_table.id）
        9,
        ("ALTER TABLE todo ADD COLUMN log_id TEXT",),
    ),
    (
        # v9 → v10：暂存区草稿（仅本机，永不同步）
        10,
        (
            """CREATE TABLE IF NOT EXISTS scratch (
                 id         INTEGER PRIMARY KEY AUTOINCREMENT,
                 name       TEXT NOT NULL DEFAULT '',
                 content    TEXT NOT NULL DEFAULT '',
                 created_at TEXT NOT NULL,
                 updated_at TEXT NOT NULL
               )""",
            """CREATE TABLE IF NOT EXISTS scratch_image (
                 id         INTEGER PRIMARY KEY AUTOINCREMENT,
                 scratch_id INTEGER NOT NULL,
                 filename   TEXT NOT NULL,
                 position   INTEGER NOT NULL DEFAULT 0
               )""",
            "CREATE INDEX IF NOT EXISTS idx_scratch_image "
            "ON scratch_image (scratch_id, position)",
        ),
    ),
    (
        # v10 → v11：应用侧项目 id（主页项目树挂载）
        11,
        ("ALTER TABLE todo ADD COLUMN app_project_id TEXT",),
    ),
    (
        # v11 → v12：应用侧详细说明（app_content 纯文本，详情页单独铺块展示）
        12,
        ("ALTER TABLE todo ADD COLUMN app_detail TEXT",),
    ),
)

#: 兜底补列：(表, 列, 列定义)。跑在版本迁移之后，按 PRAGMA 实测结果补，
#: 所以哪怕某台机器的 user_version 已经被写脏，结构也能自愈。
_REQUIRED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("todo", "position", "INTEGER NOT NULL DEFAULT 0"),
    ("todo", "priority", "TEXT NOT NULL DEFAULT ''"),
    ("todo", "due_date", "TEXT"),
    ("todo", "planned_min", "INTEGER NOT NULL DEFAULT 0"),
    ("todo", "log_id", "TEXT"),
    ("todo", "app_project_id", "TEXT"),
    ("todo", "app_detail", "TEXT"),
    ("outbox", "retries", "INTEGER NOT NULL DEFAULT 0"),
)


class Database:
    """SQLite 连接持有者（每个线程一个连接）。"""

    def __init__(self, path: Path | str = DB_PATH) -> None:
        self._path = Path(path)
        self._local = threading.local()
        self._lock = threading.Lock()

    # ---- 连接 ----

    def conn(self) -> sqlite3.Connection:
        """取当前线程的连接（首次访问时创建）。"""
        existing = getattr(self._local, "conn", None)
        if existing is not None:
            return existing
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.row_factory = sqlite3.Row
        # WAL：写不阻塞读，同步线程跑着的时候界面不卡
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """显式事务：正常退出提交，异常回滚。

        业务表与 outbox 必须在这个 with 里一起写，否则会出现
        "改了但没排队"的漏传。
        """
        conn = self.conn()
        with self._lock:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # ---- 结构 ----

    def init_schema(self) -> None:
        """建表 + 按 user_version 增量升级 + 补列兜底（幂等）。"""
        conn = self.conn()
        with self._lock:
            conn.executescript(_SCHEMA)
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            for version, statements in _MIGRATIONS:
                if version <= current:
                    continue
                for statement in statements:
                    try:
                        conn.execute(statement)
                    except sqlite3.OperationalError as exc:
                        # 典型是「列已存在」（升级过又回退版本），不该中断启动；
                        # 缺列的情况下面 _ensure_columns 会按实测结果补上。
                        logger.debug("迁移到 v%s 的语句被跳过：%s", version, exc)
            self._ensure_columns(conn)
            # 优先级口径 2026-11-04 收敛为 P0/P1/P2（与应用侧 0023 迁移的
            # high→P0 / medium→P1 / low→P2 对齐）：历史 P3 归到 P2。
            # 幂等、零成本，不用单开一个 schema 版本
            conn.execute("UPDATE todo SET priority = 'P2' WHERE UPPER(priority) = 'P3'")
            # 只在向上升级时写版本号：装过更高版本又回退的场景保留原号，
            # 否则下次升回去会把已经生效的迁移整段重跑一遍。
            if current < SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        """按 PRAGMA 实测结构补缺失的列。

        存在意义：版本号是"声明"，列才是"事实"。历史上有过 user_version 已经
        写到最新、但某条 ALTER 被跳过的库（升级路径错位导致），此后所有迁移
        都会被版本号挡在门外，只能靠实测补列自愈。
        """
        for table, column, ddl in _REQUIRED_COLUMNS:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            if not rows:
                continue  # 表还没建出来（不该发生），交给建表语句
            if column in {row["name"] for row in rows}:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            logger.warning("检测到结构缺口，已补列 %s.%s", table, column)

    # ---- meta ----

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn().execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
        return row["v"] if row and row["v"] is not None else default

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO meta (k, v) VALUES (?, ?) "
                "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                (key, value),
            )

    def clear_meta(self, key: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM meta WHERE k = ?", (key,))

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


#: 默认实例（绝大多数调用方直接用它）
database = Database()
