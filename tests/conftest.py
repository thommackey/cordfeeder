"""Shared test fixtures."""

from __future__ import annotations

import pytest_asyncio

from cordfeeder.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialise()
    yield database
    await database.close()
