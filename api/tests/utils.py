def draw_complex_forest(tree_data):
    """
    Рисует лес, где каждый ключ верхнего уровня содержит своё дерево.

    :param tree_data: Словарь, представляющий лес с группами деревьев.
    """
    def recurse(node_id, tree, prefix="", is_last=True):
        node = tree[node_id]
        connector = "└─ " if is_last else "├─ "
        title = node.get('title', 'Без названия')
        print(f"{prefix} + {connector} + {title}")

        children = node.get('children', [])
        for i, child_id in enumerate(children):
            is_child_last = (i == len(children) - 1)
            new_prefix = prefix + ("    " if is_last else "│   ")
            recurse(child_id, tree, new_prefix, is_child_last)

    # Обрабатываем каждое дерево из групп
    for tree_id, tree in tree_data.items():
        print(f"Дерево с корнем {tree_id}:")
        root = tree.get(tree_id)
        if root:
            recurse(tree_id, tree)
        print()  # Разделяем деревья


if '__main__' == __name__:

    # Пример использования
    tree_structure = {
        "64f44a08-076e-42f0-b85d-d5450d7de128": {
            "64f44a08-076e-42f0-b85d-d5450d7de128": {
                "id": "64f44a08-076e-42f0-b85d-d5450d7de128",
                "title": "2",
                "data": {},
                "updated_at": "2024-12-28T12:22:28.239486+00:00",
                "children": [
                    "b5f431ea-9172-4ce2-a2c5-09b7c79b2b9b",
                    "b148d63b-5c0a-4bd9-8811-fedb9e406dd4"
                ]
            },
            "b5f431ea-9172-4ce2-a2c5-09b7c79b2b9b": {
                "id": "b5f431ea-9172-4ce2-a2c5-09b7c79b2b9b",
                "title": "3",
                "data": {},
                "updated_at": "2024-12-28T12:22:39.836890+00:00",
                "children": []
            },
            "b148d63b-5c0a-4bd9-8811-fedb9e406dd4": {
                "id": "b148d63b-5c0a-4bd9-8811-fedb9e406dd4",
                "title": "4",
                "data": {},
                "updated_at": "2024-12-28T13:17:36.205759+00:00",
                "children": []
            }
        },
        "dd59e19f-7ff6-40cd-94ec-3dbc88527401": {
            "dd59e19f-7ff6-40cd-94ec-3dbc88527401": {
                "id": "dd59e19f-7ff6-40cd-94ec-3dbc88527401",
                "title": "new tree",
                "data": {
                    "test": "data"
                },
                "updated_at": "2024-12-30T16:18:13.230987+00:00",
                "children": []
            }
        },
        "aaf93755-efea-43f9-abb7-27b01465d5d3": {
            "aaf93755-efea-43f9-abb7-27b01465d5d3": {
                "id": "aaf93755-efea-43f9-abb7-27b01465d5d3",
                "title": None,
                "data": {
                    "view": "link",
                    "source": "1d58e686-5b64-4dd4-aebb-8ed852111b0d"
                },
                "updated_at": "2025-01-04T09:12:35.000549+00:00",
                "children": []
            }
        },
        "c629fef3-88d1-4d04-9879-474d2a0194f8": {
            "c629fef3-88d1-4d04-9879-474d2a0194f8": {
                "id": "c629fef3-88d1-4d04-9879-474d2a0194f8",
                "title": None,
                "data": {
                    "view": "link",
                    "source": "4a5dd54d-859b-4571-bf7a-3ee1547e118d"
                },
                "updated_at": "2025-01-04T09:29:03.049034+00:00",
                "children": []
            }
        }
    }

    draw_complex_forest(tree_structure)