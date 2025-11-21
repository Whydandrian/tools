"""create convert_files, merge_files, split_files tables

Revision ID: create_convert_merge_split_tables
Revises: create_docs_ocr_compress
Create Date: 2025-11-21
"""
from alembic import op
import sqlalchemy as sa


revision = "create_convert_merge_split_tables"
down_revision = "create_docs_ocr_compress"
branch_labels = None
depends_on = None


def upgrade():
    # convert_files table
    op.create_table(
        "convert_files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("uuid", sa.String(64), nullable=False, unique=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("converted_path", sa.String(500)),
        sa.Column("converted_file_name", sa.String(255)),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )

    # merge_files table
    op.create_table(
        "merge_files",
        sa.Column("id", sa.Integer, primary_key=True),
        # store list of document ids (JSON array): [{"id": 1}, {"id": 2}] or [1,2] depending on app convention
        sa.Column("document_id", sa.JSON, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("merged_file_name", sa.String(255)),
        sa.Column("merged_path", sa.String(500)),
        sa.Column("merged_size", sa.String(50)),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )

    # split_files table
    op.create_table(
        "split_files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("uuid", sa.String(64), nullable=False, unique=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("splited_file_name", sa.String(255)),
        sa.Column("splited_path", sa.String(500)),
        sa.Column("splited_size", sa.String(50)),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )


def downgrade():
    op.drop_table("split_files")
    op.drop_table("merge_files")
    op.drop_table("convert_files")