"""Fire N booking requests at once for a class with a few spots, then count what happened.

Usage: python loadtest.py --endpoint naive --spots 1 --requests 50
The API must be running (uvicorn app.main:app).
"""
import argparse
import asyncio
from collections import Counter

import httpx
import psycopg

from app.main import DATABASE_URL


def reset_class(spots: int) -> int:
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("TRUNCATE bookings, classes RESTART IDENTITY")
        return conn.execute(
            "INSERT INTO classes (name, capacity, spots_left) VALUES ('Tuesday 6pm', %s, %s) RETURNING id",
            (spots, spots)).fetchone()[0]


def count(class_id: int) -> tuple[int, int]:
    with psycopg.connect(DATABASE_URL) as conn:
        booked = conn.execute("SELECT count(*) FROM bookings WHERE class_id = %s", (class_id,)).fetchone()[0]
        left = conn.execute("SELECT spots_left FROM classes WHERE id = %s", (class_id,)).fetchone()[0]
    return booked, left


async def fire(base: str, path: str, n: int) -> Counter:
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:
        responses = await asyncio.gather(
            *(client.post(path, params={"member": f"member{i}@example.com"}) for i in range(n)))
    return Counter(r.status_code for r in responses)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", choices=["naive", "locked"], default="naive")
    p.add_argument("--spots", type=int, default=1)
    p.add_argument("--requests", type=int, default=50)
    p.add_argument("--base", default="http://127.0.0.1:8000")
    args = p.parse_args()

    class_id = reset_class(args.spots)
    prefix = "/naive" if args.endpoint == "naive" else ""
    codes = asyncio.run(fire(args.base, f"{prefix}/classes/{class_id}/book", args.requests))
    booked, left = count(class_id)
    print(f"{args.endpoint}: {args.requests} requests for {args.spots} spot(s) -> "
          f"{codes.get(201, 0)} got 201, {codes.get(409, 0)} got 409, "
          f"{booked} bookings in the table, spots_left = {left}")


if __name__ == "__main__":
    main()
