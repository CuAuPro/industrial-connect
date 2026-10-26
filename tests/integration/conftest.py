import os

import pytest


@pytest.fixture
def service(request):
    """Only services explicitly selected by the operator may be contacted."""
    selected = set(os.environ.get("IC_TEST_SERVICES", "").split(","))
    name = request.param
    if name not in selected:
        pytest.skip(f"Opt in with IC_TEST_SERVICES containing {name}")
    return name


@pytest.fixture
def test_password():
    value = os.environ.get("IC_TEST_PASSWORD")
    if not value:
        pytest.fail("Set IC_TEST_PASSWORD for the disposable test services")
    return value
