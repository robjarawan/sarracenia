import pytest


def pytest_collection_modifyitems(config, items):
    """Auto-apply the 'unit' marker to any test that has no tier marker.

    This keeps the default behaviour simple: every test is 'unit' unless
    explicitly marked otherwise (optional_dep, integration, etc.).
    """
    tier_markers = {"optional_dep", "integration", "requires_redis_lua", "requires_docker"}
    unit_marker = pytest.mark.unit
    for item in items:
        item_markers = {m.name for m in item.iter_markers()}
        if not item_markers & tier_markers:
            item.add_marker(unit_marker)


# ---------------------------------------------------------------------------
# Useful for debugging tests
# ---------------------------------------------------------------------------
class debug():
    def pretty(*things, **named_things):
        import pprint
        for t in things:
            pprint.PrettyPrinter(indent=2, width=200).pprint(t)
        for k,v in named_things.items():
            print(str(k) + ":")
            pprint.PrettyPrinter(indent=2, width=200).pprint(v)