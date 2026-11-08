"""Public tests are offline by default; local synthetic DB tests require opt-in."""
import os
import pytest


def pytest_collection_modifyitems(items):
    if os.environ.get('CLEARQUERY_RUN_DB_TESTS')=='1':
        return
    skip=pytest.mark.skip(reason='Set CLEARQUERY_RUN_DB_TESTS=1 for your isolated synthetic demo database.')
    for item in items:
        if 'db' in item.keywords:
            item.add_marker(skip)
