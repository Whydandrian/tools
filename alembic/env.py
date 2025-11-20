from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from sqlalchemy import MetaData
from alembic import context

# Import model base
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, JSON, ForeignKey
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# ---- MODEL DEFINITIONS ----

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


# ---- END MODEL DEFINITIONS ----

config = context.config
fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

