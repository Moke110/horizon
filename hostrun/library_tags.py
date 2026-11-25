"""The marker attached to explicitly generated development records."""

TEST_TAG = "测试"


def new_test_tags(command):
    return {"tags": [TEST_TAG]} if command.get("test_data") is True else {}


def visible_tags(record):
    return {"tags": record["tags"]} if "tags" in record else {}
