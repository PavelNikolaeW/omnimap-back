from pprint import pprint
import numpy as np


def parse_grid_line(s, prefix):
    s = s[len(prefix):]

    split = '__'
    if '_sl_' in s:
        split = '_sl_'
    if split in s:
        start, end = map(int, s.split(split))
        size = abs(start - end)
    else:
        start, size = int(s), 1
    return start, size


def compute_min_rectangle_area(childrenPositions):
    min_area = float('inf')
    col_span, row_span = 1, 1
    occupants = []

    for key, value in childrenPositions.items():
        if key in ("col", "row"):
            continue

        col_start, col_span, row_start, row_span = 1, 1, 1, 1
        for item in value:
            if item.startswith('grid-column_'):
                col_start, col_span = parse_grid_line(item, 'grid-column_')
            elif item.startswith('grid-row_'):
                row_start, row_span = parse_grid_line(item, 'grid-row_')

        occupants.append((col_start, col_span, row_start, row_span))
        area = col_span * row_span

        if area < min_area:
            min_area = area
    return occupants, col_span, row_span


def calc_size_grid(classes):
    col, row = 1, 1
    for cls in classes:
        if cls.startswith('grid-template-columns'):
            col = len(cls.rstrip('_').split('__'))
        if cls.startswith('grid-template-rows'):
            row = len(cls.rstrip('_').split('__'))
    return col, row


def mark_occupied_areas(rectangles, col, row):
    plane = [[0] * col for _ in range(row)]
    for rect in rectangles:
        col_start, col_span, row_start, row_span = rect
        for r in range(row_start, row_start + row_span):
            for c in range(col_start, col_start + col_span):
                if r - 1 < row and c - 1 < col:
                    plane[r - 1][c - 1] = 1
                else:
                    print(f"Прямоугольник выходит за границы сетки ({r}, {c})")
    return plane


def calc_content_area(contentPosition):
    col_start, col_span, row_start, row_span = 1, 1, 1, 1
    for cls in contentPosition:
        if cls.startswith('grid-column_'):
            col_start, col_span = parse_grid_line(cls, 'grid-column_')
        elif cls.startswith('grid-row_'):
            row_start, row_span = parse_grid_line(cls, 'grid-row_')
    return col_start, col_span, row_start, row_span


def can_place_np(A, B, start_y, start_x):
    """Проверяет, можно ли разместить массив B в массиве A начиная с позиции (start_y, start_x)."""
    A_sub = A[start_y:start_y + B.shape[0], start_x:start_x + B.shape[1]]
    if A_sub.shape != B.shape:
        return False
    # Проверяем, что все позиции, где B == 1, в A == 0
    overlap = (B == 1) & (A_sub != 0)
    return not np.any(overlap)


def place_array_np(A, B, start_y, start_x):
    """Размещает массив B в массиве A начиная с позиции (start_y, start_x)."""
    A[start_y:start_y + B.shape[0], start_x:start_x + B.shape[1]] |= B


def find_and_place_np(A, B):
    """
    Ищет место для размещения массива B в массиве A.
    Расширяет A по строкам или столбцам по очереди при необходимости.
    Возвращает итоговые размеры массива A, координаты размещения B и итоговый массив A.
    """
    A = np.array(A)
    B = np.array(B)
    max_y, max_x = A.shape
    B_height, B_width = B.shape

    # Флаг для определения, что добавлять: True — строку, False — столбец
    add_row = True

    while True:
        for y in range(max_y - B_height + 1):
            for x in range(max_x - B_width + 1):
                if can_place_np(A, B, y, x):
                    place_array_np(A, B, y, x)
                    # pprint(A.tolist())
                    return A.shape, (x, y)

        # Если место не найдено, расширяем массив A
        if add_row:
            new_row = np.zeros((1, max_x), dtype=A.dtype)
            A = np.vstack([A, new_row])
        else:
            new_col = np.zeros((A.shape[0], 1), dtype=A.dtype)
            A = np.hstack([A, new_col])

        # Переключаем флаг для следующего шага
        add_row = not add_row
        max_y, max_x = A.shape


def set_grid(col, row):
    return [
        f'grid-template-columns_{"1fr__" * col}',
        f'grid-template-rows_auto__{"1fr__" * (row - 1)}'
    ]


def set_child_position(x, y, col_span, row_span):
    return [f"grid-column_{1 + x}__{1 + x + col_span}",
            f"grid-row_{1 + y}__{1 + y + row_span}"]


def custom_grid_update(customGrid, child):
    # print(customGrid)
    children_positions = customGrid['childrenPositions']
    occupants, col_span, row_span = compute_min_rectangle_area(children_positions)
    occupants.append(calc_content_area(customGrid['contentPosition']))
    col, row = calc_size_grid(customGrid['grid'])
    grid = mark_occupied_areas(occupants, col, row)
    min_rectangle = [[1] * col_span for _ in range(row_span)]
    # pprint(min_rectangle)

    new_children_position = {}
    col_row = [col, row]

    # for child in map(str, children):
    # if child not in children_positions:
    col_row, child_position = find_and_place_np(grid, min_rectangle)
    new_children_position[child] = child_position
    grid = mark_occupied_areas(occupants, col, row)
    # pprint(occupants)


    # Обновляем сетку, если увеличилась
    if col != col_row[0] or row != col_row[1]:
        customGrid['grid'] = set_grid(*col_row)

    for key, value in new_children_position.items():
        new_children_position[key] = set_child_position(*value, col_span, row_span)

    if new_children_position:
        children_positions.update(new_children_position)
        # children.append(new_children_position)
        customGrid['childrenPositions'] = children_positions
        # custom_grid_update(customGrid, children)
    pprint(customGrid)


if __name__ == '__main__':
    customGrid = {"grid": [
        "grid-template-columns_1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__",
        "grid-template-rows_auto__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__"],
        "contentPosition": ["grid-column_1_sl_21"],
        "childrenPositions": {"1264": ["grid-column_1__3", "grid-row_2__4"],
                              "1265": ["grid-column_3__5", "grid-row_2__4"]}}

    for i in ['11', 14, 15]:
        custom_grid_update(customGrid, i)
