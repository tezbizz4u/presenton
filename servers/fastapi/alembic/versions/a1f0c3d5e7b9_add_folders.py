"""add folders and presentations.folder_id

Revision ID: a1f0c3d5e7b9
Revises: 026c0ba8b35c
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a1f0c3d5e7b9"
down_revision: str | None = "026c0ba8b35c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "folders" not in tables:
        op.create_table(
            "folders",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("owner_id", sa.Uuid(), nullable=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("parent_id", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["parent_id"], ["folders.id"], ondelete="SET NULL"
            ),
        )
        op.create_index("ix_folders_owner_id", "folders", ["owner_id"])
        op.create_index("ix_folders_parent_id", "folders", ["parent_id"])

    if "presentations" in tables:
        columns = {c["name"] for c in inspector.get_columns("presentations")}
        if "folder_id" not in columns:
            with op.batch_alter_table("presentations") as batch_op:
                batch_op.add_column(sa.Column("folder_id", sa.Uuid(), nullable=True))
                batch_op.create_foreign_key(
                    "fk_presentations_folder_id",
                    "folders",
                    ["folder_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
                batch_op.create_index("ix_presentations_folder_id", ["folder_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "presentations" in tables:
        columns = {c["name"] for c in inspector.get_columns("presentations")}
        if "folder_id" in columns:
            with op.batch_alter_table("presentations") as batch_op:
                batch_op.drop_index("ix_presentations_folder_id")
                batch_op.drop_constraint("fk_presentations_folder_id", type_="foreignkey")
                batch_op.drop_column("folder_id")
    if "folders" in tables:
        op.drop_table("folders")
