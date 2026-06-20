"""
好感度数据 SQLite 存储层

管理用户好感度、昵称、最后活跃时间等数据的持久化存储。
"""

from pathlib import Path

import aiosqlite

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

# 数据库文件路径
DB_PATH = (
    Path(get_astrbot_data_path())
    / "plugin_data"
    / "astrbot_plugin_interstitial_context"
    / "affection.db"
)

# 建表 SQL
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS affection (
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    nickname TEXT DEFAULT '',
    affection INTEGER DEFAULT 0,
    last_active TEXT,
    PRIMARY KEY (user_id, session_id)
);
"""

# 索引 SQL
_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_affection_session_id ON affection(session_id);
"""

# session_info 表 SQL
_CREATE_SESSION_INFO_SQL = """
CREATE TABLE IF NOT EXISTS session_info (
    session_id TEXT PRIMARY KEY,
    session_name TEXT DEFAULT ''
);
"""


class AffectionDB:
    """好感度数据库管理器"""

    def __init__(self):
        self._db_path = str(DB_PATH)
        self._initialized = False

    async def _ensure_initialized(self):
        """确保数据库已初始化"""
        if not self._initialized:
            await self.init_db()

    async def init_db(self):
        """创建表和索引，执行迁移"""
        if self._initialized:
            return

        # 确保目录存在
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(_CREATE_TABLE_SQL)
            await conn.execute(_CREATE_INDEX_SQL)
            await conn.execute(_CREATE_SESSION_INFO_SQL)
            await conn.commit()

        self._initialized = True
        logger.debug(f"[AffectionDB] 数据库初始化完成: {self._db_path}")

    async def get_affection(self, user_id: str, session_id: str) -> int:
        """获取好感度，不存在返回 0"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self._db_path) as conn:
            cursor = await conn.execute(
                "SELECT affection FROM affection WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def set_affection(
        self, user_id: str, session_id: str, value: int, nickname: str = ""
    ) -> int:
        """设置好感度（upsert），返回实际值"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self._db_path) as conn:
            # 如果提供了昵称则更新，否则保留原昵称
            if nickname:
                await conn.execute(
                    """INSERT INTO affection (user_id, session_id, nickname, affection)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(user_id, session_id) DO UPDATE SET
                           affection = excluded.affection,
                           nickname = excluded.nickname""",
                    (user_id, session_id, nickname, value),
                )
            else:
                await conn.execute(
                    """INSERT INTO affection (user_id, session_id, nickname, affection)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(user_id, session_id) DO UPDATE SET
                           affection = excluded.affection""",
                    (user_id, session_id, nickname, value),
                )
            await conn.commit()
        return value

    async def adjust_affection(
        self, user_id: str, session_id: str, delta: int, nickname: str = ""
    ) -> int:
        """调整好感度（增减），返回调整后的值"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self._db_path) as conn:
            if nickname:
                await conn.execute(
                    """INSERT INTO affection (user_id, session_id, nickname, affection)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(user_id, session_id) DO UPDATE SET
                           affection = affection + excluded.affection,
                           nickname = excluded.nickname""",
                    (user_id, session_id, nickname, delta),
                )
            else:
                await conn.execute(
                    """INSERT INTO affection (user_id, session_id, nickname, affection)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(user_id, session_id) DO UPDATE SET
                           affection = affection + excluded.affection""",
                    (user_id, session_id, nickname, delta),
                )
            await conn.commit()
            # 查询调整后的值
            cursor = await conn.execute(
                "SELECT affection FROM affection WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def delete_affection(self, user_id: str, session_id: str) -> bool:
        """删除记录，返回是否成功删除"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self._db_path) as conn:
            cursor = await conn.execute(
                "DELETE FROM affection WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def upsert_nickname(
        self, user_id: str, session_id: str, nickname: str, affection_initial: int = 0
    ):
        """更新昵称（upsert），不存在则插入默认好感度"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                """INSERT INTO affection (user_id, session_id, nickname, affection)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, session_id) DO UPDATE SET
                       nickname = excluded.nickname""",
                (user_id, session_id, nickname, affection_initial),
            )
            await conn.commit()

    async def update_last_active(
        self, user_id: str, session_id: str, time_str: str, affection_initial: int = 0
    ):
        """更新最后活跃时间"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                """INSERT INTO affection (user_id, session_id, nickname, affection, last_active)
                   VALUES (?, ?, '', ?, ?)
                   ON CONFLICT(user_id, session_id) DO UPDATE SET
                       last_active = excluded.last_active""",
                (user_id, session_id, affection_initial, time_str),
            )
            await conn.commit()

    async def get_last_active(self, user_id: str, session_id: str) -> str | None:
        """获取最后活跃时间，不存在返回 None"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self._db_path) as conn:
            cursor = await conn.execute(
                "SELECT last_active FROM affection WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def list_by_user(self, user_id: str) -> list[dict]:
        """查询某用户所有会话的好感度"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT user_id, session_id, nickname, affection, last_active FROM affection WHERE user_id = ?",
                (user_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def list_by_session(self, session_id: str) -> list[dict]:
        """查询某会话所有用户的好感度（按好感度降序）"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT user_id, session_id, nickname, affection, last_active FROM affection WHERE session_id = ? ORDER BY affection DESC",
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_ranking(self, session_id: str, limit: int = 10) -> list[dict]:
        """获取某会话好感度排名前 N"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT user_id, session_id, nickname, affection, last_active FROM affection WHERE session_id = ? ORDER BY affection DESC LIMIT ?",
                (session_id, limit),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def migrate_from_kv(self, kv_get_func, kv_delete_func):
        """从 AstrBot KV 存储（preferences 表）迁移数据到 SQLite

        AstrBot KV 存储结构：
            - 表名: preferences
            - 列: scope, scope_id, key, value (JSON: {"val": <actual>})
            - 插件数据: scope="plugin", scope_id="{author}/{name}"

        KV key 格式:
            - affection:{group_id}:{user_id}  (群聊)
            - affection:private:{user_id}     (私聊)
            - last_active:{group_id}:{user_id}
            - last_active:private:{user_id}
        """
        await self._ensure_initialized()

        import json

        main_db_path = Path(get_astrbot_data_path()) / "data_v4.db"
        if not main_db_path.exists():
            logger.info("[AffectionDB] 未找到主数据库，跳过迁移")
            return

        plugin_id = "anteriortag127/astrbot_plugin_interstitial_context"

        affection_data = {}
        keys_to_delete = []

        async with aiosqlite.connect(str(main_db_path)) as conn:
            conn.row_factory = aiosqlite.Row

            cursor = await conn.execute(
                "SELECT key, value FROM preferences WHERE scope = ? AND scope_id = ? AND key LIKE 'affection:%'",
                ("plugin", plugin_id),
            )
            affection_rows = await cursor.fetchall()

            for row in affection_rows:
                key = row["key"]
                raw_value = row["value"]
                keys_to_delete.append(key)

                try:
                    value_obj = (
                        json.loads(raw_value)
                        if isinstance(raw_value, str)
                        else raw_value
                    )
                    actual_value = (
                        value_obj.get("val", value_obj)
                        if isinstance(value_obj, dict)
                        else value_obj
                    )
                except (json.JSONDecodeError, TypeError):
                    actual_value = raw_value

                parts = key[len("affection:") :]
                # 私聊: affection:private:{user_id} -> session_id="private:{user_id}", user_id={user_id}
                # 群聊: affection:{group_id}:{user_id} -> session_id={group_id}, user_id={user_id}
                if parts.startswith("private:"):
                    user_id = parts[len("private:"):]
                    session_id = f"private:{user_id}"
                else:
                    split_idx = parts.rfind(":")
                    if split_idx == -1:
                        logger.warning(f"[AffectionDB] 无法解析 KV key: {key}，跳过")
                        continue
                    session_id = parts[:split_idx]
                    user_id = parts[split_idx + 1 :]

                try:
                    affection_val = int(actual_value)
                except (ValueError, TypeError):
                    logger.warning(
                        f"[AffectionDB] 无法解析好感度值 key={key} value={actual_value}，跳过"
                    )
                    continue

                affection_data[(user_id, session_id)] = {"affection": affection_val}

            cursor = await conn.execute(
                "SELECT key, value FROM preferences WHERE scope = ? AND scope_id = ? AND key LIKE 'last_active:%'",
                ("plugin", plugin_id),
            )
            last_active_rows = await cursor.fetchall()

            for row in last_active_rows:
                key = row["key"]
                raw_value = row["value"]
                keys_to_delete.append(key)

                try:
                    value_obj = (
                        json.loads(raw_value)
                        if isinstance(raw_value, str)
                        else raw_value
                    )
                    actual_value = (
                        value_obj.get("val", value_obj)
                        if isinstance(value_obj, dict)
                        else value_obj
                    )
                except (json.JSONDecodeError, TypeError):
                    actual_value = raw_value

                parts = key[len("last_active:") :]
                if parts.startswith("private:"):
                    user_id = parts[len("private:"):]
                    session_id = f"private:{user_id}"
                else:
                    split_idx = parts.rfind(":")
                    if split_idx == -1:
                        logger.warning(f"[AffectionDB] 无法解析 KV key: {key}，跳过")
                        continue
                    session_id = parts[:split_idx]
                    user_id = parts[split_idx + 1 :]

                time_val = str(actual_value) if actual_value else None

                if (user_id, session_id) in affection_data:
                    affection_data[(user_id, session_id)]["last_active"] = time_val
                else:
                    affection_data[(user_id, session_id)] = {
                        "affection": 0,
                        "last_active": time_val,
                    }

        if not affection_data:
            logger.info("[AffectionDB] 无 KV 数据需要迁移")
            return

        async with aiosqlite.connect(self._db_path) as conn:
            for (user_id, session_id), data in affection_data.items():
                await conn.execute(
                    """INSERT INTO affection (user_id, session_id, nickname, affection, last_active)
                       VALUES (?, ?, '', ?, ?)
                       ON CONFLICT(user_id, session_id) DO UPDATE SET
                           affection = excluded.affection,
                           last_active = COALESCE(excluded.last_active, last_active)""",
                    (user_id, session_id, data["affection"], data.get("last_active")),
                )
            await conn.commit()

        for key in keys_to_delete:
            try:
                await kv_delete_func(key)
            except Exception as e:
                logger.warning(f"[AffectionDB] 删除 KV key 失败: {key}, 错误: {e}")

        migrated_count = len(affection_data)
        logger.info(f"[AffectionDB] KV 迁移完成，共迁移 {migrated_count} 条记录")

    async def upsert_session_name(self, session_id: str, session_name: str):
        """更新会话名称（群名），不存在则插入"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                """INSERT INTO session_info (session_id, session_name)
                   VALUES (?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                       session_name = excluded.session_name""",
                (session_id, session_name),
            )
            await conn.commit()

    async def list_sessions(self) -> list[dict]:
        """获取所有不同的 session，包含名称"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """SELECT DISTINCT a.session_id,
                       COALESCE(s.session_name, '') as session_name
                   FROM affection a
                   LEFT JOIN session_info s ON a.session_id = s.session_id
                   ORDER BY a.session_id"""
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def list_users(self) -> list[dict]:
        """获取所有不同的用户（user_id + 最新昵称）"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT user_id, MAX(nickname) as nickname FROM affection GROUP BY user_id ORDER BY user_id"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def close(self):
        """关闭数据库连接（aiosqlite 每次操作后自动关闭，此方法保留用于兼容）"""
        self._initialized = False
        logger.debug("[AffectionDB] 数据库连接已关闭")
