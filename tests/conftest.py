"""Tests must never touch real keys, the real .env or the real database."""
import os

import dotenv
import pytest

dotenv.load_dotenv = lambda *a, **k: False  # runs before any project module is imported
os.environ.pop("DATABASE_URL", None)         # an exported DATABASE_URL would point tests at the production database


@pytest.fixture(autouse=True)
def never_use_the_real_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
