"""Bind every pytest JUnit testcase to its exact collected node ID."""


def pytest_collection_modifyitems(session, items) -> None:
    del session
    for item in items:
        item.user_properties[:] = [("google_live_nodeid", item.nodeid)]
