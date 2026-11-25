-- One row per class. spots_left is the counter that two requests can race on.
CREATE TABLE IF NOT EXISTS classes (
    id         serial PRIMARY KEY,
    name       text    NOT NULL,
    capacity   integer NOT NULL CHECK (capacity > 0),
    spots_left integer NOT NULL
);

CREATE TABLE IF NOT EXISTS bookings (
    id        serial PRIMARY KEY,
    class_id  integer NOT NULL REFERENCES classes(id),
    member    text    NOT NULL,
    booked_at timestamptz NOT NULL DEFAULT now()
);
