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
        "a3854cbd-39a2-489e-bac2-52406a9412e1": {
            "id": "a3854cbd-39a2-489e-bac2-52406a9412e1",
            "title": "123",
            "parent_id": "25a6a794-0b26-4436-af04-307677003d50",
            "data": {
                "childOrder": [
                    "44a3c875-cbe7-4d74-a514-cc58c04cb9ec",
                    "1b899206-3e43-4244-bd2f-a687f4dfb7b5"
                ]
            },
            "updated_at": "2025-10-12T16:28:09.622346+00:00",
            "children": [
                "44a3c875-cbe7-4d74-a514-cc58c04cb9ec",
                "1b899206-3e43-4244-bd2f-a687f4dfb7b5"
            ]
        },
        "44a3c875-cbe7-4d74-a514-cc58c04cb9ec": {
            "id": "44a3c875-cbe7-4d74-a514-cc58c04cb9ec",
            "title": "1234",
            "parent_id": "a3854cbd-39a2-489e-bac2-52406a9412e1",
            "data": {
                "childOrder": [
                    "85df47a6-5ce8-4084-81e9-4f2345e1da1c",
                    "013d0977-964f-4d97-aeb8-e1ebdfead607"
                ]
            },
            "updated_at": "2025-10-12T16:28:36.363023+00:00",
            "children": [
                "85df47a6-5ce8-4084-81e9-4f2345e1da1c",
                "013d0977-964f-4d97-aeb8-e1ebdfead607"
            ]
        },
        "1b899206-3e43-4244-bd2f-a687f4dfb7b5": {
            "id": "1b899206-3e43-4244-bd2f-a687f4dfb7b5",
            "title": "13424",
            "parent_id": "a3854cbd-39a2-489e-bac2-52406a9412e1",
            "data": {
                "childOrder": [
                    "24c1ccee-b14c-4173-910d-db168bff4069",
                    "5abcf24e-1289-40cd-96d9-7ca7741a5753"
                ]
            },
            "updated_at": "2025-10-12T16:28:16.248982+00:00",
            "children": [
                "24c1ccee-b14c-4173-910d-db168bff4069",
                "5abcf24e-1289-40cd-96d9-7ca7741a5753"
            ]
        },
        "85df47a6-5ce8-4084-81e9-4f2345e1da1c": {
            "id": "85df47a6-5ce8-4084-81e9-4f2345e1da1c",
            "title": None,
            "parent_id": "44a3c875-cbe7-4d74-a514-cc58c04cb9ec",
            "data": {
                "view": "link",
                "source": "f367ed54-bfbd-4507-bc5c-831ff409e635",
                "target": "f367ed54-bfbd-4507-bc5c-831ff409e635"
            },
            "updated_at": "2025-10-12T15:32:54.139323+00:00",
            "children": []
        },
        "013d0977-964f-4d97-aeb8-e1ebdfead607": {
            "id": "013d0977-964f-4d97-aeb8-e1ebdfead607",
            "title": "32",
            "parent_id": "44a3c875-cbe7-4d74-a514-cc58c04cb9ec",
            "data": {
                "color": [
                    200,
                    90,
                    70,
                    0
                ],
                "childOrder": [
                    "4915ca69-7421-4be5-97ac-967a46928c90"
                ]
            },
            "updated_at": "2025-10-12T16:28:54.080793+00:00",
            "children": [
                "4915ca69-7421-4be5-97ac-967a46928c90"
            ]
        },
        "24c1ccee-b14c-4173-910d-db168bff4069": {
            "id": "24c1ccee-b14c-4173-910d-db168bff4069",
            "title": "243",
            "parent_id": "1b899206-3e43-4244-bd2f-a687f4dfb7b5",
            "data": {
                "color": [
                    110,
                    90,
                    70,
                    0
                ],
                "childOrder": [
                    "6a7f1c61-2e64-47ee-8b3b-71f841dc0519"
                ]
            },
            "updated_at": "2025-10-12T16:28:49.120743+00:00",
            "children": [
                "6a7f1c61-2e64-47ee-8b3b-71f841dc0519"
            ]
        },
        "5abcf24e-1289-40cd-96d9-7ca7741a5753": {
            "id": "5abcf24e-1289-40cd-96d9-7ca7741a5753",
            "title": "4312",
            "parent_id": "1b899206-3e43-4244-bd2f-a687f4dfb7b5",
            "data": {
                "childOrder": []
            },
            "updated_at": "2025-10-12T16:28:16.238101+00:00",
            "children": []
        },
        "4915ca69-7421-4be5-97ac-967a46928c90": {
            "id": "4915ca69-7421-4be5-97ac-967a46928c90",
            "title": "55",
            "parent_id": "013d0977-964f-4d97-aeb8-e1ebdfead607",
            "data": {
                "childOrder": [
                    "dcd6f72e-e6df-4559-a037-744ab2ef7f81"
                ]
            },
            "updated_at": "2025-10-12T16:28:42.943688+00:00",
            "children": [
                "dcd6f72e-e6df-4559-a037-744ab2ef7f81"
            ]
        },
        "6a7f1c61-2e64-47ee-8b3b-71f841dc0519": {
            "id": "6a7f1c61-2e64-47ee-8b3b-71f841dc0519",
            "title": "543]",
            "parent_id": "24c1ccee-b14c-4173-910d-db168bff4069",
            "data": {
                "childOrder": []
            },
            "updated_at": "2025-10-12T16:28:13.923577+00:00",
            "children": []
        }
    }
    print(str(replace_uuids_in_json(data)).replace("'", '"'))
