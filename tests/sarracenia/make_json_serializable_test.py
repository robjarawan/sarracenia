import json
import pytest
from tests.conftest import *

import sarracenia.make_json_serializable
from json import JSONEncoder


class ObjectWithToJson:
    """Test object that has a to_json method."""
    def __init__(self, value):
        self.value = value

    def to_json(self):
        return {'value': self.value}


class ObjectWithoutToJson:
    """Test object that does NOT have a to_json method."""
    def __init__(self, data):
        self.data = data


class ObjectWithBrokenToJson:
    """Test object whose to_json raises an exception."""
    def to_json(self):
        raise RuntimeError("broken serializer")


def test_object_with_to_json_is_serializable():
    obj = ObjectWithToJson(42)
    result = json.dumps(obj)
    parsed = json.loads(result)
    assert parsed == {'value': 42}


def test_object_with_to_json_string_value():
    obj = ObjectWithToJson("hello")
    result = json.dumps(obj)
    parsed = json.loads(result)
    assert parsed == {'value': 'hello'}


def test_object_without_to_json_raises():
    obj = ObjectWithoutToJson("test")
    with pytest.raises(TypeError):
        json.dumps(obj)


def test_standard_types_still_work():
    """Standard JSON types should serialize normally."""
    data = {
        'string': 'hello',
        'int': 42,
        'float': 3.14,
        'bool': True,
        'null': None,
        'list': [1, 2, 3],
        'nested': {'a': 1},
    }
    result = json.dumps(data)
    assert json.loads(result) == data


def test_nested_object_with_to_json():
    obj = ObjectWithToJson(99)
    data = {'items': [obj]}
    result = json.dumps(data)
    parsed = json.loads(result)
    assert parsed == {'items': [{'value': 99}]}


def test_broken_to_json_raises():
    obj = ObjectWithBrokenToJson()
    with pytest.raises(RuntimeError):
        json.dumps(obj)


def test_encoder_default_was_patched():
    """Verify that JSONEncoder.default has been monkey-patched."""
    original_module = JSONEncoder.default.__module__ if hasattr(JSONEncoder.default, '__module__') else None
    # The patched default should not be the original one
    assert JSONEncoder.default.__name__ == '_default'


def test_to_json_returning_string():
    class ReturnsString:
        def to_json(self):
            return "serialized_string"

    obj = ReturnsString()
    result = json.dumps(obj)
    assert json.loads(result) == "serialized_string"


def test_to_json_returning_list():
    class ReturnsList:
        def to_json(self):
            return [1, 2, 3]

    obj = ReturnsList()
    result = json.dumps(obj)
    assert json.loads(result) == [1, 2, 3]


def test_to_json_returning_none():
    class ReturnsNone:
        def to_json(self):
            return None

    obj = ReturnsNone()
    result = json.dumps(obj)
    assert json.loads(result) is None
