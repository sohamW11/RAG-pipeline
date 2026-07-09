"""Add ``keywords`` to the ``documents`` table.

Stores comma-separated keywords derived from each document's title/category so
downstream consumers get cheap context without parsing the PDF.

Revision ID: 0002_document_keywords
Revises: 0001_initial
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_document_keywords"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable ``keywords`` text column."""
    op.add_column("documents", sa.Column("keywords", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the ``keywords`` column."""
    op.drop_column("documents", "keywords")
