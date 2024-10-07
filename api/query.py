get_blocks_query = '''WITH RECURSIVE cte(id, title, access_type, effective_access_type, path, depth, data) AS (
    -- Базовый случай: начальные блоки
    SELECT
        b.id,
        b.title,
        b.access_type,
        CASE
            WHEN b.access_type != 'inherited' THEN b.access_type
            ELSE 'public'  -- По умолчанию 'public', если нет родителя для наследования
        END AS effective_access_type,
        ARRAY[b.id]::uuid[] AS path,  -- Для обнаружения циклов
        0 AS depth,
        b.data
    FROM api_block b
    WHERE b.id = ANY(%(block_ids)s)  -- Начальные ID блоков (массив UUID)

    UNION ALL

    -- Рекурсивный случай: обход дочерних блоков
    SELECT
        child_b.id,
        child_b.title,
        child_b.access_type,
        CASE
            WHEN child_b.access_type != 'inherited' THEN child_b.access_type
            ELSE parent_cte.effective_access_type
        END AS effective_access_type,
        parent_cte.path || child_b.id::uuid,
        parent_cte.depth + 1,
        child_b.data
    FROM cte parent_cte
    JOIN api_block_children c ON c.from_block_id = parent_cte.id
    JOIN api_block child_b ON child_b.id = c.to_block_id
    WHERE parent_cte.depth < 100  -- Ограничение глубины рекурсии
      AND NOT (child_b.id::uuid = ANY(parent_cte.path))  -- Предотвращение циклов
)
SELECT
    cte.id,
    cte.title,
    cte.data,
    ARRAY_REMOVE(ARRAY_AGG(DISTINCT c.to_block_id), NULL) AS children
FROM cte
LEFT JOIN api_block_children c ON c.from_block_id = cte.id
WHERE (
        cte.effective_access_type = 'public' OR
        (cte.effective_access_type = 'private' AND EXISTS (
            SELECT 1 FROM api_block_visible_to_users v
            WHERE v.block_id = cte.id AND v.user_id = %(user_id)s  -- ID пользователя
        ))
    )
GROUP BY cte.id, cte.title, cte.data
LIMIT 100;'''

