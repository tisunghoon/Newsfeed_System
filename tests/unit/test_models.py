from app.models import Base


def test_tables_registered():
    assert set(Base.metadata.tables) == {"users", "posts", "post_media", "friendships"}


def test_users_columns():
    users = Base.metadata.tables["users"]
    assert set(users.columns.keys()) == {
        "id", "username", "email", "push_enabled", "device_token", "created_at", "updated_at",
    }
    assert users.c.username.unique and users.c.email.unique
    assert not users.c.username.nullable
    assert users.c.device_token.nullable


def test_posts_columns_and_index():
    posts = Base.metadata.tables["posts"]
    assert set(posts.columns.keys()) == {"id", "author_id", "body", "created_at", "updated_at", "deleted_at"}
    assert posts.c.body.type.length == 2000
    assert posts.c.deleted_at.nullable
    assert "idx_posts_author_created" in {i.name for i in posts.indexes}


def test_post_media_columns_and_constraints():
    media = Base.metadata.tables["post_media"]
    assert set(media.columns.keys()) == {"id", "post_id", "media_type", "url", "position"}
    assert "idx_post_media_post_id" in {i.name for i in media.indexes}
    check_names = {c.name for c in media.constraints if c.name}
    assert {"ck_post_media_media_type", "chk_media_count"} <= check_names


def test_friendships_keys_and_indexes():
    friendships = Base.metadata.tables["friendships"]
    assert [c.name for c in friendships.primary_key.columns] == ["user_id", "friend_id"]
    assert {i.name for i in friendships.indexes} == {"idx_friendships_user_id", "idx_friendships_friend_id"}
    assert friendships.c.blocked.server_default is not None
    assert friendships.c.muted.server_default is not None


def test_foreign_keys_cascade_on_delete():
    for name, column in [("posts", "author_id"), ("post_media", "post_id"), ("friendships", "friend_id")]:
        fk = next(iter(Base.metadata.tables[name].c[column].foreign_keys))
        assert fk.ondelete == "CASCADE"
