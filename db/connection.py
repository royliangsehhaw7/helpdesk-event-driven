import os
import asyncio
import aiomysql  # Must use the async driver


"""
**************** STATIC OR UTILITIY CLASS (NOT A TRUE SINGLETON) *****************
*** using async.io
    Its primary purpose is to handle I/O-bound operations (like database queries, 
    network requests, or file reading) without freezing your application. It allows 
    a single thread to manage thousands of simultaneous tasks by pausing execution 
    during slow network or database operations and switching to other work.
***
**************** /STATIC OR UTILITIY CLASS (NOT A TRUE SINGLETON) *****************
"""


class Database:
    _pool = None
    _lock = asyncio.Lock()  # Must use the async lock

    def __init__(self):
        """Prevent instantiation entirely."""
        raise RuntimeError("Use Database.get_pool() directly. Do not instantiate.")

    @classmethod
    async def get_pool(cls):
        """Async-safe static accessor."""
        if cls._pool is None:
            async with cls._lock:
                # Double-check pattern ensures only one pool is created
                if cls._pool is None:
                    cls._pool = await aiomysql.create_pool(
                        host=os.getenv('DB_HOST'),
                        user=os.getenv('DB_USER'),
                        password=os.getenv('DB_PWD'),
                        db=os.getenv('DB_NAME'),
                        autocommit=True,
                        cursorclass=aiomysql.DictCursor
                    )
        return cls._pool

    @classmethod
    async def close(cls):
        """Async-safe static cleanup."""
        async with cls._lock:
            if cls._pool:
                cls._pool.close()
                await cls._pool.wait_closed()
                cls._pool = None
