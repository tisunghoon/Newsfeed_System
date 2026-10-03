import asyncio
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.services.social_graph_service import FRIEND_LIMIT
from tests.unit.test_social_graph import Harness, add_friend, new_user, remove_friend

USER_IDS = st.uuids().map(str)


async def add_then_check(user_id, target_id):
    h = Harness()
    h.repo.users |= {user_id, target_id}
    res = await add_friend(h, target_id, user_id)
    return res, h


# Feature: newsfeed-system, Property 17: 친구 추가는 양방향 관계를 생성하고 라운드트립을 보장한다
@given(user_id=USER_IDS, target_id=USER_IDS)
@settings(max_examples=100, deadline=None)
def test_add_friend_creates_both_directions(user_id, target_id):
    assume(user_id != target_id)
    res, h = asyncio.run(add_then_check(user_id, target_id))
    assert res.status_code == 201
    assert asyncio.run(h.repo.are_friends(user_id, target_id))
    assert asyncio.run(h.repo.are_friends(target_id, user_id))


# Feature: newsfeed-system, Property 18: 친구 삭제는 양방향 관계를 제거하는 라운드트립을 보장한다
@given(user_id=USER_IDS, target_id=USER_IDS)
@settings(max_examples=100, deadline=None)
def test_remove_friend_deletes_both_directions(user_id, target_id):
    assume(user_id != target_id)

    async def run():
        res, h = await add_then_check(user_id, target_id)
        assert res.status_code == 201
        res = await remove_friend(h, target_id, user_id)
        return res, h

    res, h = asyncio.run(run())
    assert res.status_code == 204
    assert not asyncio.run(h.repo.are_friends(user_id, target_id))
    assert not asyncio.run(h.repo.are_friends(target_id, user_id))


# Feature: newsfeed-system, Property 19: 친구 추가 중복 요청은 항상 409를 반환한다 (멱등성)
@given(user_id=USER_IDS, target_id=USER_IDS, repeats=st.integers(min_value=1, max_value=5))
@settings(max_examples=100, deadline=None)
def test_duplicate_add_always_returns_409(user_id, target_id, repeats):
    assume(user_id != target_id)

    async def run():
        res, h = await add_then_check(user_id, target_id)
        assert res.status_code == 201
        return [(await add_friend(h, target_id, user_id)).status_code for _ in range(repeats)], h

    codes, h = asyncio.run(run())
    assert codes == [409] * repeats
    assert len(h.repo.pairs) == 2


# Feature: newsfeed-system, Property 20: 친구 수는 5,000명 상한을 정확히 준수한다
@given(user_id=USER_IDS, extra=st.integers(min_value=1, max_value=3))
@settings(max_examples=100, deadline=None)
def test_friend_limit_is_enforced(user_id, extra):
    async def run():
        h = Harness()
        h.repo.pairs = {(user_id, f"other-{i}") for i in range(FRIEND_LIMIT)}
        responses = [await add_friend(h, new_user(h), user_id) for _ in range(extra)]
        return responses, h

    responses, h = asyncio.run(run())
    assert all(r.status_code == 409 and r.json()["code"] == "FRIEND_LIMIT_EXCEEDED" for r in responses)
    assert asyncio.run(h.repo.count_friends(user_id)) == FRIEND_LIMIT
