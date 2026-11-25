import asyncio

import pytest

from tests.conftest import bookings_and_spots

pytestmark = pytest.mark.anyio


async def burst(client, path, members):
    return await asyncio.gather(*(client.post(path, params={"member": m}) for m in members))


async def test_naive_endpoint_oversells_the_last_spot(client, make_class):
    class_id = make_class(1)
    await burst(client, f"/naive/classes/{class_id}/book", [f"m{i}" for i in range(50)])
    booked, _ = bookings_and_spots(class_id)
    assert booked > 1  # the bug this repo is about


async def test_locked_endpoint_books_exactly_one_for_the_last_spot(client, make_class):
    class_id = make_class(1)
    responses = await burst(client, f"/classes/{class_id}/book", [f"m{i}" for i in range(50)])
    codes = sorted(r.status_code for r in responses)
    assert codes.count(201) == 1 and codes.count(409) == 49
    assert bookings_and_spots(class_id) == (1, 0)


async def test_locked_endpoint_fills_a_12_spot_class_exactly(client, make_class):
    class_id = make_class(12)
    await burst(client, f"/classes/{class_id}/book", [f"m{i}" for i in range(50)])
    assert bookings_and_spots(class_id) == (12, 0)


async def test_double_click_books_once(client, make_class):
    class_id = make_class(5)
    responses = await burst(client, f"/classes/{class_id}/book", ["same@example.com"] * 10)
    assert [r.status_code for r in responses].count(201) == 1
    assert bookings_and_spots(class_id) == (1, 4)


async def test_full_class_returns_409(client, make_class):
    class_id = make_class(1)
    first = await client.post(f"/classes/{class_id}/book", params={"member": "a"})
    second = await client.post(f"/classes/{class_id}/book", params={"member": "b"})
    assert (first.status_code, second.status_code) == (201, 409)
