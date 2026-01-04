"""add image_model to projects

Revision ID: 008_add_project_image_model
Revises: 007_add_seedream_api_key
Create Date: 2026-01-03 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "008_add_project_image_model"
down_revision = "007_add_seedream_api_key"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if column exists (idempotent migrations for SQLite)."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Add per-project image_model override to projects table."""
    if not _column_exists("projects", "image_model"):
        op.add_column("projects", sa.Column("image_model", sa.String(length=100), nullable=True))


def downgrade() -> None:
    """Remove image_model from projects table."""
    if _column_exists("projects", "image_model"):
        op.drop_column("projects", "image_model")

