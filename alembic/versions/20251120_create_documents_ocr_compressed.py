"""create documents, ocr_files, compressed_files tables

Revision ID: create_docs_ocr_compress
Revises:
Create Date: 2025-11-20

"""
from alembic import op
import sqlalchemy as sa


revision = "create_docs_ocr_compress"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Documents table
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("uuid", sa.String(64), nullable=False, unique=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("size", sa.String(20), nullable=False),
        sa.Column("total_page", sa.Integer, default=0),
        sa.Column("file_path", sa.String(500), nullable=False),

        sa.Column("is_letter_sirama", sa.Boolean, default=False),
        sa.Column("is_protected_text", sa.Boolean, default=False),
        sa.Column("is_passworded", sa.Boolean, default=False),

        sa.Column("upload_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )

    # OCR Files table
    op.create_table(
        "ocr_files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metadata_file", sa.JSON),
        sa.Column("extracted_text", sa.Text),
        sa.Column("status", sa.String(20), default="pending"),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )

    # Compressed Files table
    op.create_table(
        "compressed_files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), default="pending"),
        sa.Column("extracted_path", sa.String(500)),
        sa.Column("extracted_size", sa.String(20)),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )


def downgrade():
    op.drop_table("compressed_files")
    op.drop_table("ocr_files")
    op.drop_table("documents")

