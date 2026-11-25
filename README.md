# Two people, one spot: fixing a booking race in PostgreSQL

Last summer I built the booking backend for a small fencing club: FastAPI, PostgreSQL, about
20 classes a month. When I went back to the code months later and pointed a load test at it, I found
that 50 people booking the last spot in a class at the same moment could *all* get in. Nobody had
hit it in real use, but a popular class opening at a set time is exactly when it would happen.

This repo is a small standalone version of that bug and its fix. It isn't the club's code, just the
same pattern in about 80 lines, so you can watch it break on your own machine and then fix it.

You'll need Python 3.12, Docker (or any local PostgreSQL 16) and about 15 minutes.

## 1. Run it

```bash
docker compose up -d                 # PostgreSQL 16 on localhost:5432 (user/password/db: app/app/booking)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000     # creates the tables and the book_class() function on startup
```

In a second terminal:

```bash
python loadtest.py --endpoint naive --spots 1 --requests 50
```

The script creates a class with one spot, fires 50 booking requests at the same time, then counts
the rows in the `bookings` table. On my laptop:

```
naive: 50 requests for 1 spot(s) -> 50 got 201, 0 got 409, 50 bookings in the table, spots_left = 0
```

One spot, 50 bookings. Try a bigger class:

```bash
python loadtest.py --endpoint naive --spots 12 --requests 50
```

```
naive: 50 requests for 12 spot(s) -> 50 got 201, 0 got 409, 50 bookings in the table, spots_left = 11
```

That one is worse: the class took 50 bookings and still says it has 11 spots left. The counter
and the table no longer agree, so the next person to book makes it worse.

## 2. Why it happens

Here is the naive endpoint, trimmed (`app/main.py`):

```python
row = await conn.execute("SELECT spots_left FROM classes WHERE id = %s", (class_id,))
spots_left = row[0]
if spots_left <= 0:
    raise HTTPException(409, "class is full")
await conn.execute("INSERT INTO bookings (class_id, member) VALUES (%s, %s)", (class_id, member))
await conn.execute("UPDATE classes SET spots_left = %s WHERE id = %s", (spots_left - 1, class_id))
```

It reads like correct code, and with one user at a time it is. The problem is the gap between the
`SELECT` and the `UPDATE`. With two requests running at once:

| time | request A                 | request B                 |
|------|---------------------------|---------------------------|
| 1    | reads `spots_left = 1`    |                           |
| 2    |                           | reads `spots_left = 1`    |
| 3    | 1 > 0, inserts booking    |                           |
| 4    |                           | 1 > 0, inserts booking    |
| 5    | writes `spots_left = 0`   |                           |
| 6    |                           | writes `spots_left = 0`   |

Both requests made their decision from a number that was already out of date. This is called a
*check-then-act* race, and PostgreSQL's default isolation level (READ COMMITTED) does nothing to stop
it: each statement sees committed data, but nothing holds the row still between your statements.

It also explains the 12-spot result. Every request read 12, so every request wrote back 11.

## 3. The number of extra bookings is your connection count

This part surprised me. Run the server with a smaller connection pool:

```bash
POOL_SIZE=5 uvicorn app.main:app --port 8000
python loadtest.py --endpoint naive --spots 1
```

| pool size | bookings for 1 spot (50 requests) |
|-----------|-----------------------------------|
| 5         | 5                                 |
| 20        | 20                                |
| 50        | 50                                |

Every request that holds a connection at the same moment reads the same `spots_left`, so the
number of people who get in equals the number of connections in flight. The other requests wait for a
connection, and by the time they get one, the counter already says 0.

So a small pool hides the bug in testing, and scaling the app up (more workers, a bigger pool) makes
it worse in production. "It works on my machine" is a real risk with races like this.

## 4. Fixing it

There are a few ways to fix this in PostgreSQL. I looked at three:

1. **Atomic update.** `UPDATE classes SET spots_left = spots_left - 1 WHERE id = $1 AND spots_left > 0`
   and check that one row changed. Short and fast, and the right answer if decrementing is the
   whole job.
2. **SERIALIZABLE isolation.** PostgreSQL detects the conflict and aborts one transaction, and your
   code has to catch the error and retry.
3. **Lock the row first** with `SELECT ... FOR UPDATE`, then check and write inside the same
   transaction.

I went with option 3, inside a PL/pgSQL function, because the real booking step does more than
decrement a counter. It also has to reject a second booking by the same person and, in the club's
version, manage a waitlist. Those checks need the same consistent view, and one lock on the class row
gives it to all of them. Option 1 only covers the counter. Option 2 works but pushes retry logic into
every caller.

Here is the function (`functions.sql`):

```sql
CREATE OR REPLACE FUNCTION book_class(p_class_id integer, p_member text)
RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    c classes%ROWTYPE;
BEGIN
    -- Every other booking for this class waits on this line until we commit.
    SELECT * INTO c FROM classes WHERE id = p_class_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN 'not_found';
    END IF;

    IF EXISTS (SELECT 1 FROM bookings WHERE class_id = p_class_id AND member = p_member) THEN
        RETURN 'already_booked';
    END IF;

    IF c.spots_left <= 0 THEN
        RETURN 'full';
    END IF;

    INSERT INTO bookings (class_id, member) VALUES (p_class_id, p_member);
    UPDATE classes SET spots_left = spots_left - 1 WHERE id = p_class_id;
    RETURN 'ok';
END;
$$;
```

`FOR UPDATE` takes a row lock. A second transaction that tries to lock the same class waits until
the first one commits, then reads the *new* `spots_left`. Bookings for the same class now run one
after another. Bookings for different classes still run in parallel, because they lock different rows.

The endpoint becomes a single call:

```python
row = await conn.execute("SELECT book_class(%s, %s)", (class_id, member))
```

Run the load test against it:

```bash
python loadtest.py --endpoint locked --spots 1
python loadtest.py --endpoint locked --spots 12
```

```
locked: 50 requests for 1 spot(s) -> 1 got 201, 49 got 409, 1 bookings in the table, spots_left = 0
locked: 50 requests for 12 spot(s) -> 12 got 201, 38 got 409, 12 bookings in the table, spots_left = 0
```

Exactly 1, and exactly 12. I ran each 5 times and got the same numbers every time, and the
one-spot run gave exactly 1 with a pool of 5 and a pool of 50.

A few things worth knowing:

- **Keep the locked part short.** Everything after `FOR UPDATE` runs one request at a time per class.
  Don't call a payment API or send an email while holding the lock. Do that after the commit.
- **Lock in the same order everywhere.** If cancelling locks a booking and then the class, while
  booking locks the class and then a booking, two requests can wait on each other forever (a
  deadlock). In the club's version, every function locks the class row first.
- **The duplicate check is safe here** because the class lock makes all bookings for that class take
  turns. A `UNIQUE (class_id, member)` constraint is still a good backstop if other code writes to the table.

## 5. Test it so it stays fixed

A race fix you can't test is a fix you'll break later. `tests/test_race.py` runs against a real
PostgreSQL database (not SQLite or a mock, since the bug lives in the database) and fires the same
bursts of requests:

```bash
pytest -q
```

- the naive endpoint oversells the last spot (this test documents the bug)
- 50 requests for the last spot give exactly one 201 and 49 409s
- a 12-spot class ends with exactly 12 bookings and 0 spots left
- one person double-clicking 10 times gets one booking
- a full class returns 409

GitHub Actions runs the same tests against a `postgres:16` service container on every push
(`.github/workflows/ci.yml`).

## What I took away

- If your code reads a value, decides, then writes, ask what happens when two requests do that at once.
- Load-test with as many concurrent connections as production will have. A small pool hid this bug.
- Put the decision where the data is. PostgreSQL already knows how to make requests take turns.

## Files

```
app/main.py         FastAPI app with the naive and locked endpoints
schema.sql          classes and bookings tables
functions.sql       book_class() PL/pgSQL function
loadtest.py         fires N concurrent requests and counts the results
tests/test_race.py  concurrency tests against a real PostgreSQL database
```
