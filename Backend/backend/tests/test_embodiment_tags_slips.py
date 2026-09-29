"""She sometimes writes her body tags wrong; they must never show up as text."""
from backend.embodiment.self_model import normalize_body_tags, parse_feel, strip_body_tags


def test_slipped_tags_are_understood_and_hidden():
    a = "<feel=curious:0.6>That sounds like game dialogue. What's playing?"
    assert strip_body_tags(a) == "That sounds like game dialogue. What's playing?"
    assert parse_feel(a).label == "curious" and abs(parse_feel(a).intensity - 0.6) < 1e-6
    b = "<feel=happy:0.7 | they're back after 13 minutes away</feel><gesture=wave>Welcome back."
    assert strip_body_tags(b) == "Welcome back."
    assert parse_feel(b).reason == "they're back after 13 minutes away"
    assert normalize_body_tags("<gesture=wave>Hi") == "<gesture>wave</gesture>Hi"
    assert strip_body_tags("<gesture=wave>Hi</gesture> there") == "Hi there"
    # Proper tags and attribute forms are left alone.
    assert normalize_body_tags("<feel>calm:0.3</feel>ok") == "<feel>calm:0.3</feel>ok"
    assert normalize_body_tags('<gesture name="nod"/>') == '<gesture name="nod"/>'


def test_replies_are_saved_with_proper_tags():
    from backend.models import core

    conn = core.get_connection()
    try:
        cid = conn.execute("INSERT INTO conversations (title) VALUES ('t')").lastrowid
        conn.commit()
    finally:
        conn.close()
    mid = core.add_message(cid, "assistant", "<feel=happy:0.7>Welcome back.")
    saved = [m for m in core.get_messages(cid) if m["id"] == mid][0]["content"]
    assert saved == "<feel>happy:0.7</feel>Welcome back."
