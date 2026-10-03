"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-03

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

uuid_pk = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=uuid_pk, primary_key=True),
        sa.Column("username", sa.String(50), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("push_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("device_token", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "posts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=uuid_pk, primary_key=True),
        sa.Column(
            "author_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.String(2000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_posts_author_created", "posts", ["author_id", sa.text("created_at DESC")])

    op.create_table(
        "post_media",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=uuid_pk, primary_key=True),
        sa.Column(
            "post_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("media_type", sa.String(10), nullable=False),
        sa.Column("url", sa.Text),
        sa.Column("position", sa.SmallInteger, nullable=False),
        sa.CheckConstraint("media_type IN ('text', 'image', 'video')", name="ck_post_media_media_type"),
        sa.CheckConstraint("position BETWEEN 0 AND 9", name="chk_media_count"),
    )
    op.create_index("idx_post_media_post_id", "post_media", ["post_id"])

    op.create_table(
        "friendships",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "friend_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("blocked", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("muted", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_friendships_user_id", "friendships", ["user_id"])
    op.create_index("idx_friendships_friend_id", "friendships", ["friend_id"])


def downgrade() -> None:
    op.drop_table("friendships")
    op.drop_table("post_media")
    op.drop_table("posts")
    op.drop_table("users")
