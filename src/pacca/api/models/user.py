"""
Legacy SQLAlchemy User model.

Moved from `src/pacca/api/models.py` into the `models/` package when
the SME-authoring API needed its own Pydantic models module.

This file is the canonical location for the legacy sync User table;
re-exported from `models/__init__.py` so existing imports
(`from .models import User`) continue to work without changes elsewhere.
"""

from sqlalchemy import Column, Integer, String

from ..database import Base


class User(Base):  # type: ignore[misc]
    """Legacy sync SQLAlchemy User table — used at startup for table creation."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    # RBAC (see api/rbac.py). server_default backfills existing rows to the
    # least-privileged role on migrate (migration 006) — must match that
    # migration's add_column call EXACTLY or the migration-drift CI job fails.
    role = Column(String(30), nullable=False, server_default="clinician")
