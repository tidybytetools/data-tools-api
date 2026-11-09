from app.flatten import flatten


def test_nested_dict_and_list():
    data = {"customer": {"email": "a@b.com", "tags": ["x", "y"]}, "ok": True}
    out = flatten(data)
    assert out["customer.email"] == "a@b.com"
    assert out["customer.tags.0"] == "x"
    assert out["customer.tags.1"] == "y"
    assert out["ok"] is True


def test_custom_separator():
    assert flatten({"a": {"b": 1}}, sep="/") == {"a/b": 1}


def test_empty_objects():
    assert flatten({"a": {}, "b": []}) == {"a": {}, "b": []}


def test_top_level_list():
    assert flatten(["a", "b"]) == {"0": "a", "1": "b"}
