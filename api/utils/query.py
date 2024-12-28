from django.conf import settings


get_blocks_query = f'''WITH RECURSIVE cte(
    id,
    title,
    access_type,
    effective_access_type,
    path,
    depth,
    data,
    can_be_edited_by_others,
    updated_at
) AS (
    -- Base case: initial blocks
    SELECT
        b.id,
        b.title,
        b.access_type,
        CASE
            WHEN b.access_type != 'inherited' THEN b.access_type
            ELSE 'public'  -- Default to 'private' if no parent for inheritance
        END AS effective_access_type,
        ARRAY[b.id]::uuid[] AS path,  -- To detect cycles
        0 AS depth,
        b.data,
        -- Determine if the block can be edited by others
        CASE
            WHEN b.access_type = 'public' THEN TRUE
            WHEN EXISTS (
                SELECT 1
                FROM api_block_visible_to_users v
                WHERE v.block_id = b.id
                  AND v.user_id != %(user_id)s
            ) THEN TRUE
            ELSE FALSE
        END AS can_be_edited_by_others,
        b.updated_at
    FROM api_block b
    WHERE b.id = ANY(%(block_ids)s)  -- Initial block IDs (array of UUIDs)

    UNION ALL

    -- Recursive case: traverse child blocks
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
        child_b.data,
        -- Determine if the child block can be edited by others
        CASE
            WHEN child_b.access_type = 'public' THEN TRUE
            WHEN EXISTS (
                SELECT 1
                FROM api_block_visible_to_users v
                WHERE v.block_id = child_b.id
                  AND v.user_id != %(user_id)s
            ) THEN TRUE
            WHEN parent_cte.can_be_edited_by_others THEN TRUE
            ELSE FALSE
        END AS can_be_edited_by_others,
        child_b.updated_at  -- Добавляем updated_at
    FROM cte parent_cte
    JOIN api_block_children c ON c.from_block_id = parent_cte.id
    JOIN api_block child_b ON child_b.id = c.to_block_id
    WHERE parent_cte.depth < 100  -- Limit recursion depth
      AND NOT (child_b.id::uuid = ANY(parent_cte.path))  -- Prevent cycles
)
SELECT
    cte.id,
    cte.title,
    cte.data,
    cte.can_be_edited_by_others,
    cte.updated_at,
    ARRAY_REMOVE(ARRAY_AGG(DISTINCT c.to_block_id), NULL) AS children
FROM cte
LEFT JOIN api_block_children c ON c.from_block_id = cte.id
WHERE (
        cte.effective_access_type = 'public' OR
        (cte.effective_access_type = 'private' AND EXISTS (
            SELECT 1
            FROM api_block_visible_to_users v
            WHERE v.block_id = cte.id
              AND v.user_id = %(user_id)s
        ))
    )
GROUP BY cte.id, cte.title, cte.data, cte.can_be_edited_by_others, cte.updated_at
LIMIT {settings.LIMIT_BLOCKS};'''


is_descendant_query = '''
WITH RECURSIVE block_relatives AS (
    -- Начальный блок для поиска родства
    SELECT id, id = %s AS is_target
    FROM api_block
    WHERE id = %s -- ID первого блока

    UNION ALL

    -- Рекурсивное соединение для поиска всех связанных блоков
    SELECT b.id, b.id = %s -- Проверяем, является ли блок целевым
    FROM api_block b
    INNER JOIN api_block_children bc ON b.id = bc.to_block_id -- Связь через дочерние блоки
    INNER JOIN block_relatives br ON bc.from_block_id = br.id -- Рекурсивное соединение с уже найденными блоками
    WHERE b.id != br.id AND NOT br.is_target -- Защита от циклов и остановка при нахождении цели
)
-- Проверяем, существует ли целевой блок в рекурсивно найденных данных
SELECT EXISTS (
    SELECT 1
    FROM block_relatives
    WHERE is_target
);'''