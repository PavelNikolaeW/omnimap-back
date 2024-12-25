from pprint import pprint

import numpy as np


# ==============================
# Часть 1: Преобразование входных данных в матрицы
# ==============================

def parse_grid_line(s, prefix):
    """
    Парсит строку сетки и возвращает начальную позицию и размер.

    Например:
    'grid-column_1__3' -> (1, 2)
    'grid-row_2_sl_4' -> (2, 2)
    """
    s = s[len(prefix):]

    split = '__'
    if '_sl_' in s:
        split = '_sl_'
    if split in s:
        start, end = map(int, s.split(split))
        size = abs(end - start)
    else:
        if s == 'auto':
            s = 1
        start, size = int(s), 1
    return start, size


def compute_min_rectangle_area(childrenPositions):
    """
    Вычисляет занятые области на сетке для всех детей и находит минимальную площадь прямоугольника.

    Возвращает список занятых областей, минимальный span по колонкам и строкам.
    """
    min_area = float('inf')
    min_col_span, min_row_span = 1, 1
    occupants = []

    for key, value in childrenPositions.items():
        if key in ("col", "row"):
            continue

        col_start, col_span, row_start, row_span = 1, 1, 1, 1
        for item in value:
            if item.startswith('grid-column_'):
                cs, cs_span = parse_grid_line(item, 'grid-column_')
                col_start, col_span = cs, cs_span
            elif item.startswith('grid-row_'):
                rs, rs_span = parse_grid_line(item, 'grid-row_')
                row_start, row_span = rs, rs_span

        occupants.append((col_start, col_span, row_start, row_span))
        area = col_span * row_span

        if area < min_area:
            min_area = area
            min_col_span, min_row_span = col_span, row_span

    return occupants, min_col_span, min_row_span


def calc_size_grid(classes):
    """
    Определяет размеры сетки по классам grid-template-columns и grid-template-rows.

    Возвращает количество колонок и строк.
    """
    col, row = 1, 1
    for cls in classes:
        if cls.startswith('grid-template-columns'):
            col = len(cls.rstrip('_').split('__'))
        if cls.startswith('grid-template-rows'):
            row = len(cls.rstrip('_').split('__'))
    return col, row


def mark_occupied_areas(rectangles, col, row):
    """
    Отмечает занятые области на матрице сетки.

    Возвращает матрицу сетки с отмеченными занятыми ячейками.
    """
    plane = [[0] * col for _ in range(row)]
    for rect in rectangles:
        col_start, col_span, row_start, row_span = rect
        for r in range(row_start, row_start + row_span):
            for c in range(col_start, col_start + col_span):
                if r - 1 < row and c - 1 < col:
                    plane[r - 1][c - 1] = 1
                # else:
                #     print(f"Прямоугольник выходит за границы сетки ({r}, {c})")
    return plane


def calc_content_area(contentPosition):
    """
    Извлекает область содержимого из списка позиций.

    Возвращает кортеж (col_start, col_span, row_start, row_span).
    """
    col_start, col_span, row_start, row_span = 1, 1, 1, 1
    for cls in contentPosition:
        if cls.startswith('grid-column_'):
            cs, cs_span = parse_grid_line(cls, 'grid-column_')
            col_start, col_span = cs, cs_span
        elif cls.startswith('grid-row_'):
            rs, rs_span = parse_grid_line(cls, 'grid-row_')
            row_start, row_span = rs, rs_span
    return col_start, col_span, row_start, row_span


# ==============================
# Часть 2: Размещение нового прямоугольника в сетку
# ==============================

def can_place_np(A, B, start_y, start_x):
    """
    Проверяет, можно ли разместить массив B в массиве A начиная с позиции (start_y, start_x).

    Возвращает True, если можно разместить без перекрытий, иначе False.
    """
    A_sub = A[start_y:start_y + B.shape[0], start_x:start_x + B.shape[1]]
    if A_sub.shape != B.shape:
        return False
    # Проверяем, что все позиции, где B == 1, в A == 0
    overlap = (B == 1) & (A_sub != 0)
    return not np.any(overlap)


def place_array_np(A, B, start_y, start_x):
    """
    Размещает массив B в массиве A начиная с позиции (start_y, start_x).

    Модифицирует массив A на месте.
    """
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


# ==============================
# Часть 3: Преобразование матриц обратно в данные
# ==============================

def set_grid(row, col):
    """
    Генерирует классы для grid-template-columns и grid-template-rows на основе размеров сетки.

    Возвращает список классов.
    """
    return [
        f'grid-template-columns_{"1fr__" * col}',
        f'grid-template-rows_auto__{"1fr__" * (row - 1)}'
    ]


def set_child_position(x, y, col_span, row_span):
    """
    Генерирует позиции для дочернего элемента на основе координат и спанов.

    Возвращает список позиций.
    """
    return [
        f"grid-column_{1 + x}__{1 + x + col_span}",
        f"grid-row_{1 + y}__{1 + y + row_span}"
    ]


# ==============================
# Обновление сетки с добавлением нового дочернего элемента
# ==============================

def custom_grid_update(customGrid, child):
    print(customGrid)
    """
    Обновляет customGrid, добавляя нового ребенка.

    Args:
        customGrid (dict): Словарь, представляющий текущую сетку.
        child (str): Ключ нового дочернего элемента.

    Возвращает:
        None. Обновляет customGrid на месте.
    """
    childrenPositions = customGrid['childrenPositions']

    # Вычисляем занятые области существующих детей
    occupants, min_col_span, min_row_span = compute_min_rectangle_area(childrenPositions)
    # Добавляем область содержимого
    content_area = calc_content_area(customGrid['contentPosition'])
    occupants.append(content_area)

    # Определяем текущие размеры сетки
    col, row = calc_size_grid(customGrid['grid'])
    print(col, row)

    # Создаем матрицу сетки с занятыми областями
    grid_matrix = mark_occupied_areas(occupants, col, row)
    # pprint(grid_matrix)

    # Создаем матрицу для нового прямоугольника
    min_rectangle = [[1] * min_col_span for _ in range(min_row_span)]
    min_rectangle = np.array(min_rectangle)

    # Ищем место для размещения нового прямоугольника
    new_grid_shape, (x, y) = find_and_place_np(grid_matrix, min_rectangle)
    pprint(new_grid_shape)
    print(x, y)
    # Обновляем сетку, если размеры изменились
    if (col, row) != new_grid_shape:
        customGrid['grid'] = set_grid(*new_grid_shape)

    # Генерируем позиции для нового дочернего элемента
    new_child_position = set_child_position(x, y, min_col_span, min_row_span)

    # Обновляем позиции детей
    if new_child_position:
        childrenPositions[child] = new_child_position
        customGrid['childrenPositions'] = childrenPositions

    print(customGrid)
    # pprint(customGrid)


# ==============================
# Основной блок
# ==============================

if __name__ == '__main__':

    customGrid = {
        "grid": [
            "grid-template-columns_1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__",
            "grid-template-rows_auto__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__1fr__"
        ],
        "contentPosition": ["grid-column_1_sl_21"],
        "childrenPositions": {
            "fwefw": ["grid-column_1__3", "grid-row_2__4"],
            "fdqewf": ["grid-column_3__5", "grid-row_2__4"]
        }
    }

    # Добавляем нового ребенка 'ewfewf'
    new_children = ['ewfewf']
    for child in new_children:
        custom_grid_update(customGrid, child)
