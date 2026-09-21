"""
The primitive the game loop leans on: a transition claimable exactly once
across every instance.
"""
import pytest

from app.websocket.manager import ConnectionManager

REDIS_URL = "redis://127.0.0.1:6379/15"


@pytest.fixture
async def pair():
    a, b = ConnectionManager(), ConnectionManager()
    await a.start(redis_url=REDIS_URL)
    await b.start(redis_url=REDIS_URL)
    if not (a.fanout_active and b.fanout_active):
        await a.stop(); await b.stop()
        pytest.skip("redis not reachable")
    await a._redis.flushdb()
    yield a, b
    await a.stop(); await b.stop()


async def test_only_one_instance_wins_a_transition(pair):
    """Two instances ticking at the same moment must not both advance the
    question — that would skip a question for every player."""
    a, b = pair
    first = await a.claim_once("advance:game1:question:3")
    second = await b.claim_once("advance:game1:question:3")
    assert (first, second) == (True, False)


async def test_the_same_instance_cannot_claim_twice(pair):
    a, _ = pair
    assert await a.claim_once("advance:game1:question:3") is True
    assert await a.claim_once("advance:game1:question:3") is False


async def test_different_transitions_are_independent(pair):
    a, _ = pair
    assert await a.claim_once("advance:game1:question:3") is True
    assert await a.claim_once("advance:game1:question:4") is True
    assert await a.claim_once("advance:game2:question:3") is True


async def test_without_redis_every_claim_succeeds():
    """No Redis means one instance, so there is nothing to coordinate with
    and blocking the transition would only freeze the game."""
    m = ConnectionManager()
    await m.start(redis_url="")
    assert await m.claim_once("advance:game1:question:3") is True
    assert await m.claim_once("advance:game1:question:3") is True
    await m.stop()
