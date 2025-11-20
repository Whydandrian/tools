import os
from logging.config import fileConfig
from sqlalchemy import create_engine, pool
from alembic import context
from dotenv import load_dotenv

# Load .env
load_dotenv()

# --- DATABASE CONFIG FROM .ENV ---
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "dokumi")

DATABASE_URL = f"mysql+mysqlconnector://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Alembic Config
config = context.config
fileConfig(config.config_file_name)

# ------------------------------------
#   DEFINE SQLALCHEMY MODELS
# ------------------------------------

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, JSON, ForeignKey
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class Documents(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uuid = Column(String(64), nullable=False, unique=True)
    file_name = Column(String(255), nullable=False)
    type = Column(String(20), nullable=False)
    size = Column(String(20), nullable=False)
    total_page = Column(Integer, default=0)
    file_path = Column(String(500), nullable=False)

    is_letter_sirama = Column(Boolean, default=False)
    is_protected_text = Column(Boolean, default=False)
    is_passworded = Column(Boolean, default=False)

    upload_at = Column(DateTime)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class OCRFiles(Base):
    __tablename__ = "ocr_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)

    metadata_file = Column(JSON)
    extracted_text = Column(Text)
    status = Column(String(20), default="pending")

    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class CompressedFiles(Base):
    __tablename__ = "compressed_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)

    status = Column(String(20), default="pending")
    extracted_path = Column(String(500))
    extracted_size = Column(String(20))

    created_at = Column(DateTime)
    updated_at = Column(DateTime)


# TARGET METADATA FOR MIGRATIONS
target_metadata = Base.metadata

# ------------------------------------
#        MIGRATION FUNCTIONS
# ------------------------------------

def run_migrations_offline():
    """
    Run migrations in 'offline' mode.
    """
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """
    Run migrations in 'online' mode.
    """
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
