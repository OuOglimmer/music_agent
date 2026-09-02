"""Create the configured PostgreSQL database and initialize its schema."""

from __future__ import annotations

import logging

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine, init_db

logger = logging.getLogger(__name__)


def _psycopg_url(url: URL) -> str:
    """Convert a SQLAlchemy URL into a URL accepted by psycopg directly."""
    # str(URL) masks the password as "***"; render_as_string keeps the real one
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def create_database_if_missing(database_url: URL) -> None:
    database_name = database_url.database
    if not database_name:
        raise ValueError("DATABASE_URL must include a database name")

    admin_url = database_url.set(database="postgres")
    with psycopg.connect(_psycopg_url(admin_url), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (database_name,),
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
                )
                logger.info("Created PostgreSQL database: %s", database_name)
            else:
                logger.info("PostgreSQL database already exists: %s", database_name)


def initialize_database() -> None:
    database_url = make_url(str(settings.DATABASE_URL))
    create_database_if_missing(database_url)

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    logger.info(
        "Connected to PostgreSQL at %s:%s/%s",
        database_url.host,
        database_url.port or 5432,
        database_url.database,
    )

    alembic_config = Config("alembic.ini")
    command.upgrade(alembic_config, "head")
    logger.info("Alembic migrations applied")

    with Session(engine) as session:
        init_db(session)
    logger.info("Initial data initialized")


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        initialize_database()
    except (OSError, psycopg.Error, SQLAlchemyError, ValueError) as error:
        logger.error("Database initialization failed: %s", error)
        logger.error(
            "Check DATABASE_URL, ensure PostgreSQL is running, and verify that "
            "the configured user can connect to the postgres maintenance database.",
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
