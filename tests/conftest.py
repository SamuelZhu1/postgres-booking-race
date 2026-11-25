import httpx
import psycopg
import pytest

from app.main import DATABASE_URL, app, open_pool, setup_db


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    app.state.pool = await open_pool()
    await setup_db(app.state.pool)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await app.state.pool.close()


@pytest.fixture
def make_class():
    def _make(spots: int) -> int:
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("TRUNCATE bookings, classes RESTART IDENTITY")
            return conn.execute(
                "INSERT INTO classes (name, capacity, spots_left) VALUES ('test', %s, %s) RETURNING id",
                (spots, spots)).fetchone()[0]
    return _make


def bookings_and_spots(class_id: int) -> tuple[int, int]:
    with psycopg.connect(DATABASE_URL) as conn:
        booked = conn.execute("SELECT count(*) FROM bookings WHERE class_id = %s", (class_id,)).fetchone()[0]
        left = conn.execute("SELECT spots_left FROM classes WHERE id = %s", (class_id,)).fetchone()[0]
    return booked, left
