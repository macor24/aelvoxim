"""记忆用户隔离（2026-10-17）：可见性规则与归属键。

背景：读侧原先写成 `entry_uid != viewer_uid ⇒ 丢弃`，把 user_id 为空的历史条目
（生产 1994 行里 1981 行）一并滤掉；写侧又用 email 当键，而库里存的是 UUID。
这组测试锁住三条规则：全局可见、私有隔离、键取 UUID。
"""
from aelvoxim.memory import visible_to


def test_global_entries_visible_to_all():
    """user_id 为空 = 全局，谁都能看见（含匿名调用）。"""
    assert visible_to("", "u1") is True
    assert visible_to("", "") is True


def test_private_entries_only_to_owner():
    assert visible_to("u1", "u1") is True
    assert visible_to("u2", "u1") is False


def test_memory_key_is_uuid_not_email():
    """归属键必须与库里一致（UUID），不能退成 email —— 否则自己的记忆反而查不到。"""
    from aelvoxim.learn.service_chat import _user_scope as learn_scope
    from aelvoxim.server.service_chat import _user_scope

    assert _user_scope({"id": "uuid-1", "email": "a@b.c"}) == "uuid-1"
    assert _user_scope({"user_id": "u2"}) == "u2"
    assert _user_scope({}) == ""
    assert learn_scope({"id": "uuid-1", "email": "a@b.c"}) == "uuid-1"
