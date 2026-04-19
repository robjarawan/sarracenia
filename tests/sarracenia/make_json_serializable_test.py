import json
import pytest

import sarracenia.make_json_serializable


class ObjWithToJson:
    def __init__(self, value):
        self.value = value

    def to_json(self):
        return {"value": self.value}


class ObjWithoutToJson:
    pass


def test_object_with_to_json_method():
    obj = ObjWithToJson(42)
    result = json.dumps(obj)
    assert '"value": 42' in result


def test_object_without_to_json_raises():
    obj = ObjWithoutToJson()
    with pytest.raises(TypeError):
        json.dumps(obj)


def test_dict_serialization_unchanged():
    d = {"key": "value", "num": 123}
    result = json.dumps(d)
    assert json.loads(result) == d


def test_list_serialization_unchanged():
    lst = [1, "two", 3.0, None, True]
    result = json.dumps(lst)
    assert json.loads(result) == lst


def test_nested_object_with_to_json():
    obj = ObjWithToJson("nested")
    data = {"outer": obj}
    result = json.dumps(data)
    parsed = json.loads(result)
    assert parsed == {"outer": {"value": "nested"}}


def test_to_json_returns_dict():
    class DictObj:
        def to_json(self):
            return {"a": 1, "b": 2}

    result = json.dumps(DictObj())
    assert json.loads(result) == {"a": 1, "b": 2}


def test_to_json_returns_string():
    class StrObj:
        def to_json(self):
            return "hello"

    result = json.dumps(StrObj())
    assert json.loads(result) == "hello"


def test_to_json_returns_list():
    class ListObj:
        def to_json(self):
            return [1, 2, 3]

    result = json.dumps(ListObj())
    assert json.loads(result) == [1, 2, 3]
