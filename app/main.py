"""A tiny class-booking API with two versions of the same endpoint.

/naive/... reads the spot count, checks it in Python, then writes it back.
/classes/... calls the book_class() PostgreSQL function, which locks the row first.
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from psycopg_pool import AsyncConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5432/booking")
ROOT = Path(__file__).resolve().parent.parent

POOL_SIZE = int(os.environ.get("POOL_SIZE", "50"))


async def open_pool() -> AsyncConnectionPool:
    # A fixed-size pool, so every run starts with the same number of open connections.
    pool = AsyncConnectionPool(DATABASE_URL, min_size=POOL_SIZE, max_size=POOL_SIZE, open=False)
    await pool.open(wait=True)
    return pool


async def setup_db(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        await conn.execute((ROOT / "schema.sql").read_text())
        await conn.execute((ROOT / "functions.sql").read_text())


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await open_pool()
    await setup_db(app.state.pool)
    yield
    await app.state.pool.close()


app = FastAPI(lifespan=lifespan)


@app.post("/naive/classes/{class_id}/book", status_code=201)
async def book_naive(class_id: int, member: str):
    async with app.state.pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT spots_left FROM classes WHERE id = %s", (class_id,))).fetchone()
        if row is None:
            raise HTTPException(404, "class not found")
        spots_left = row[0]
        if spots_left <= 0:
            raise HTTPException(409, "class is full")
        # Between the SELECT above and these writes, other requests can read the same spots_left.
        await conn.execute(
            "INSERT INTO bookings (class_id, member) VALUES (%s, %s)", (class_id, member))
        await conn.execute(
            "UPDATE classes SET spots_left = %s WHERE id = %s", (spots_left - 1, class_id))
    return {"status": "booked"}


@app.post("/classes/{class_id}/book", status_code=201)
async def book(class_id: int, member: str):
    async with app.state.pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT book_class(%s, %s)", (class_id, member))).fetchone()
    result = row[0]
    if result == "not_found":
        raise HTTPException(404, "class not found")
    if result == "full":
        raise HTTPException(409, "class is full")
    if result == "already_booked":
        raise HTTPException(409, "already booked")
    return {"status": "booked"}
