"""add seedream_api_key to settings table

Revision ID: 007_add_seedream_api_key
Revises: 006_add_page_aspect_ratio
Create Date: 2026-01-03 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "007_add_seedream_api_key"
down_revision = "006_add_page_aspect_ratio"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if column exists (idempotent migrations for SQLite)."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Add optional Seedream API key to settings table."""
    if not _column_exists("settings", "seedream_api_key"):
        op.add_column("settings", sa.Column("seedream_api_key", sa.String(length=500), nullable=True))


def downgrade() -> None:
    """Remove seedream_api_key from settings table."""
    if _column_exists("settings", "seedream_api_key"):
        op.drop_column("settings", "seedream_api_key")

