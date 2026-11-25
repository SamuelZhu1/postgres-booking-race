-- book_class: the whole check-then-book step in one transaction, with the class row locked.
-- Returns 'ok', 'full', 'already_booked' or 'not_found'.
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
