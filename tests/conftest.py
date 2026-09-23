import pytest

from app.search import set_index
from app.sessions import reset


@pytest.fixture(autouse=True)
def _isolate():
    reset()
    set_index(None)
    yield
    reset()
    set_index(None)
