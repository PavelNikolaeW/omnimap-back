import json
import uuid
import re
from typing import Any, Dict

UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}\b"
)

def replace_uuids(data: Any, mapping: Dict[str, str] = None) -> Any:
    """
    Рекурсивно проходит структуру данных (dict, list, str)
    и заменяет все UUID на новые, сохраняя соответствие.
    """
    if mapping is None:
        mapping = {}

    if isinstance(data, dict):
        new_dict = {}
        for key, value in data.items():
            # сначала проверяем ключи
            new_key = key
            if isinstance(key, str):
                for old_uuid in UUID_PATTERN.findall(key):
                    if old_uuid not in mapping:
                        mapping[old_uuid] = str(uuid.uuid4())
                    new_key = new_key.replace(old_uuid, mapping[old_uuid])
            # затем значения
            new_dict[new_key] = replace_uuids(value, mapping)
        return new_dict

    elif isinstance(data, list):
        return [replace_uuids(item, mapping) for item in data]

    elif isinstance(data, str):
        result = data
        for old_uuid in UUID_PATTERN.findall(data):
            if old_uuid not in mapping:
                mapping[old_uuid] = str(uuid.uuid4())
            result = result.replace(old_uuid, mapping[old_uuid])
        return result

    else:
        return data


def replace_uuids_in_json(json_input: str | dict) -> dict:
    """
    Принимает строку JSON или словарь.
    Возвращает новый словарь с заменёнными UUID.
    """
    if isinstance(json_input, str):
        data = json.loads(json_input)
    else:
        data = json_input
    new_data = replace_uuids(data)
    return new_data


if __name__ == "__main__":
    data = {
        "aa941a7d-64bc-46c6-a7d7-75dd84e11dbf": {
            "id": "aa941a7d-64bc-46c6-a7d7-75dd84e11dbf",
            "title": "123",
            "parent_id": "25a6a794-0b26-4436-af04-307677003d50",
            "data": {
                "childOrder": [
                    "d1c9a218-61ac-44b5-b228-b6526b243707"
                ]
            },
            "updated_at": "2025-10-12T08:30:03.714297+00:00",
            "children": [
                "d1c9a218-61ac-44b5-b228-b6526b243707"
            ]
        },
        "d1c9a218-61ac-44b5-b228-b6526b243707": {
            "id": "d1c9a218-61ac-44b5-b228-b6526b243707",
            "title": "1234",
            "parent_id": "aa941a7d-64bc-46c6-a7d7-75dd84e11dbf",
            "data": {
                "childOrder": [
                    "5c4d2145-6cc8-4630-afb6-eac78ac1b227"
                ]
            },
            "updated_at": "2025-10-12T08:30:14.928695+00:00",
            "children": [
                "5c4d2145-6cc8-4630-afb6-eac78ac1b227"
            ]
        },
        "5c4d2145-6cc8-4630-afb6-eac78ac1b227": {
            "id": "5c4d2145-6cc8-4630-afb6-eac78ac1b227",
            "title": None,
            "parent_id": "d1c9a218-61ac-44b5-b228-b6526b243707",
            "data": {
                "view": "link",
                "source": "f367ed54-bfbd-4507-bc5c-831ff409e635"
            },
            "updated_at": "2025-10-12T08:30:14.921941+00:00",
            "children": []
        }
    }
    print(replace_uuids_in_json(data))
