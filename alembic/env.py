"""
Alembic environment script.

Schema ownership lives here now, not in db.py's old _SCHEMA-on-every-connect
idiom (see the initial revision's docstring for why that idiom doesn't carry
over to Postgres). This is the one place DDL gets applied — an explicit,
deliberate step (`alembic upgrade head`, exposed as `strategic-reports db
upgrade`), not something that happens implicitly on every connection.

DATABASE_URL is read the same way every other entry point in this project
reads its config (load_dotenv() + os.environ) — see cli.py, flows/daily_report.py.
There's no separate alembic-specific config file for the connection string.
"""

import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

load_dotenv()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError(
        "DATABASE_URL is not set. Set it in the environment or in a .env file "
        "before running `alembic upgrade head` (or `strategic-reports db upgrade`)."
    )
# The app's own DATABASE_URL is a plain "postgresql://..." URL, passed
# straight to psycopg.connect() elsewhere in this codebase (see db.py) —
# psycopg needs no dialect prefix. Alembic's engine, however, goes through
# SQLAlchemy, which defaults bare "postgresql://" to the psycopg2 driver;
# only psycopg (v3) is installed here, so SQLAlchemy needs the explicit
# "+psycopg" dialect qualifier to pick the right driver.
sqlalchemy_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
config.set_main_option("sqlalchemy.url", sqlalchemy_url)

# No SQLAlchemy metadata/models in this codebase (see db.py, tag_tracking.py,
# etc. — plain psycopg SQL throughout). target_metadata=None means Alembic
# never autogenerates revisions by diffing against a model; every revision
# in versions/ is hand-written.
target_metadata = None


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection (`alembic upgrade head --sql`)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection — the normal path."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
