"""
services/db.py — async MySQL connection pool (aiomysql)

Usage:
    await db.init()                   # call once at bot startup
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT ...")
            rows = await cur.fetchall()
    await db.close()                  # call on shutdown
"""
import aiomysql
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

_pool: aiomysql.Pool | None = None


async def init():
    global _pool
    _pool = await aiomysql.create_pool(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        db=config.DB_NAME,
        charset="utf8mb4",
        autocommit=False,
        minsize=2,
        maxsize=10,
    )


async def close():
    global _pool
    if _pool:
        _pool.close()
        await _pool.wait_closed()
        _pool = None


class _ConnCtx:
    """Async context manager that acquires a connection and auto-commits on exit."""
    async def __aenter__(self) -> aiomysql.Connection:
        self._conn = await _pool.acquire()
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            await self._conn.commit()
        else:
            await self._conn.rollback()
        _pool.release(self._conn)


def get_conn() -> _ConnCtx:
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call db.init() first")
    return _ConnCtx()
