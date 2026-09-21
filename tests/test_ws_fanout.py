"""
Cross-instance WebSocket fanout.

A WebSocket lives in one process, so a broadcast written straight to local
sockets only reaches the slice of the room that happens to share a process
with the publisher. These tests run two independent ConnectionManagers over
one Redis to prove a broadcast on one reaches sockets held by the other —
which is the whole point, and the thing that lets the service run more than
one instance.
"""
import asyncio
import json

import pytest

from app.websocket.manager import ConnectionManager

REDIS_URL = "redis://127.0.0.1:6379/15"   # db 15: kept away from real data


class FakeWS:
    """Stands in for a Starlette WebSocket. Records what was sent."""

    def __init__(self, fail_on_send: bool = False):
        self.sent: list[dict] = []
        self.accepted = False
        self.fail_on_send = fail_on_send

    async def accept(self):
        self.accepted = True

    async def send_text(self, text: str):
        if self.fail_on_send:
            raise RuntimeError("socket is gone")
        self.sent.append(json.loads(text))


async def _settle():
    """Give the Redis subscriber a moment to receive and dispatch."""
    for _ in range(40):
        await asyncio.sleep(0.05)


# ----------------------------------------------------------- local fallback

async def test_without_redis_broadcast_still_reaches_local_sockets():
    """No REDIS_URL is a supported deployment, not an error: one instance,
    broadcasts stay in-process. A game must not stop working because there is
    no cache configured."""
    m = ConnectionManager()
    await m.start(redis_url="")
    ws = FakeWS()
    await m.connect(ws, "ABCDEF")

    await m.broadcast("ABCDEF", {"event": "game_started"})

    assert ws.sent == [{"event": "game_started"}]
    await m.stop()


async def test_dead_sockets_are_pruned_not_retried():
    m = ConnectionManager()
    await m.start(redis_url="")
    alive, dead = FakeWS(), FakeWS(fail_on_send=True)
    await m.connect(alive, "ABCDEF")
    await m.connect(dead, "ABCDEF")

    await m.broadcast("ABCDEF", {"event": "ping"})

    assert alive.sent == [{"event": "ping"}]
    assert dead not in m.rooms["ABCDEF"], "a socket that failed to send must be dropped"
    await m.stop()


async def test_broadcast_to_an_empty_room_is_a_noop():
    m = ConnectionManager()
    await m.start(redis_url="")
    await m.broadcast("NOBODY", {"event": "x"})   # must not raise
    await m.stop()


# ------------------------------------------------------------ real fanout

@pytest.fixture
async def two_instances():
    """Two managers on one Redis, as two server processes would be."""
    a, b = ConnectionManager(), ConnectionManager()
    await a.start(redis_url=REDIS_URL)
    await b.start(redis_url=REDIS_URL)
    if not (a.fanout_active and b.fanout_active):
        await a.stop(); await b.stop()
        pytest.skip("redis not reachable on 127.0.0.1:6379")
    yield a, b
    await a.stop()
    await b.stop()


async def test_a_broadcast_on_one_instance_reaches_the_other(two_instances):
    """The bug this fixes: a player connected to instance B never saw events
    published by instance A, so their game silently froze."""
    a, b = two_instances
    on_b = FakeWS()
    await b.connect(on_b, "ABCDEF")

    await a.broadcast("ABCDEF", {"event": "next_question", "question_index": 3})
    await _settle()

    assert on_b.sent == [{"event": "next_question", "question_index": 3}]


async def test_the_publisher_delivers_to_its_own_sockets_exactly_once(two_instances):
    """Publishing and delivering locally would double up on the publisher's
    own clients, so delivery happens only in the subscriber — including for
    the instance that published."""
    a, b = two_instances
    on_a, on_b = FakeWS(), FakeWS()
    await a.connect(on_a, "ABCDEF")
    await b.connect(on_b, "ABCDEF")

    await a.broadcast("ABCDEF", {"event": "score_updated"})
    await _settle()

    assert on_a.sent == [{"event": "score_updated"}], f"publisher got {on_a.sent}"
    assert on_b.sent == [{"event": "score_updated"}]


async def test_only_the_addressed_room_receives(two_instances):
    a, b = two_instances
    room1, room2 = FakeWS(), FakeWS()
    await b.connect(room1, "AAAAAA")
    await b.connect(room2, "BBBBBB")

    await a.broadcast("AAAAAA", {"event": "for-room-1"})
    await _settle()

    assert room1.sent == [{"event": "for-room-1"}]
    assert room2.sent == []


async def test_a_malformed_payload_does_not_kill_the_listener(two_instances):
    """One bad message must not take fanout down for every game on the
    instance."""
    a, b = two_instances
    ws = FakeWS()
    await b.connect(ws, "ABCDEF")

    await a._redis.publish(a.CHANNEL, "this is not json")
    await _settle()
    await a.broadcast("ABCDEF", {"event": "still-working"})
    await _settle()

    assert ws.sent == [{"event": "still-working"}]
