from django.conf import settings

# Рекурсивный запрос:
# 1) В "root" берем все корни (creator_id = user_id и parent IS NULL).
#    - root_id = id (чтобы помнить, кто корень данного ряда)
# 2) В "cte" сначала берем сами корни, потом всех потомков, указывая root_id неизменным.
get_all_trees_query = f"""
WITH RECURSIVE 
    root AS (
        -- Находим корневые блоки, к которым у пользователя есть доступ
        SELECT DISTINCT 
            b.id AS root_id,
            b.id,
            b.parent_id,
            b.title,
            b.data,
            b.updated_at
        FROM api_block b
        LEFT JOIN api_blockpermission bp
            ON b.id = bp.block_id
            AND bp.user_id = %(user_id)s
        WHERE 
            b.creator_id = %(creator_id)s
            AND b.parent_id IS NULL
    ),
    cte AS (
        -- Шаг 1: сами корни
        SELECT 
            r.root_id,
            r.id,
            r.parent_id,
            r.title,
            r.data,
            r.updated_at
        FROM root r

        UNION ALL

        -- Шаг 2: рекурсивно добавляем потомков с учётом разрешений
        SELECT
            cte.root_id,
            b.id,
            b.parent_id,
            b.title,
            b.data,
            b.updated_at
        FROM api_block b
        LEFT JOIN api_blockpermission bp
            ON b.id = bp.block_id
            AND bp.user_id = %(user_id)s
        JOIN cte 
            ON b.parent_id = cte.id
        WHERE 
            (bp.permission IS NULL OR bp.permission != 'deny') -- Исключаем запрещённые блоки
    ),
    child_counts AS (
        -- Считаем количество детей для каждого блока
        SELECT parent_id, COUNT(*) AS total_children
        FROM api_block
        GROUP BY parent_id
    )
SELECT
    cte.root_id,
    cte.id,
    cte.parent_id,
    cte.title,
    cte.data,
    cte.updated_at,
    COALESCE(child_counts.total_children, 0) AS total_children
FROM cte
LEFT JOIN child_counts 
    ON cte.id = child_counts.parent_id;
"""

load_empty_blocks_query = f"""
WITH RECURSIVE block_hierarchy AS (
    -- Начальная выборка (ANCHOR)
    SELECT
        b.id,
        b.parent_id,
        b.title,
        b.data,
        b.updated_at,
        1 AS depth,
        CASE 
            WHEN bp_deny.block_id IS NOT NULL THEN 'deny'
            WHEN bp_other.permission IS NOT NULL THEN bp_other.permission
            ELSE 'deny'
        END AS permission
    FROM api_block AS b
    LEFT JOIN api_blockpermission AS bp_deny
        ON b.id = bp_deny.block_id
        AND bp_deny.user_id = %(user_id)s
        AND bp_deny.permission = 'deny'
    LEFT JOIN (
        SELECT bp.block_id, bp.permission
        FROM api_blockpermission AS bp
        WHERE bp.user_id = %(user_id)s
          AND bp.permission != 'deny'
    ) AS bp_other
        ON b.id = bp_other.block_id
    WHERE
        b.id = ANY(%(block_ids)s)

    UNION ALL

    -- Рекурсивная часть (RECURSIVE)
    SELECT
        c.id,
        c.parent_id,
        c.title,
        c.data,
        c.updated_at,
        bh.depth + 1 AS depth,
        CASE 
            WHEN bp_deny2.block_id IS NOT NULL THEN 'deny'
            WHEN bp_other2.permission IS NOT NULL THEN bp_other2.permission
            ELSE 'deny'
        END AS permission
    FROM api_block AS c
    LEFT JOIN api_blockpermission AS bp_deny2
        ON c.id = bp_deny2.block_id
        AND bp_deny2.user_id = %(user_id)s
        AND bp_deny2.permission = 'deny'
    LEFT JOIN (
        SELECT bp.block_id, bp.permission
        FROM api_blockpermission AS bp
        WHERE bp.user_id = %(user_id)s
          AND bp.permission != 'deny'
    ) AS bp_other2
        ON c.id = bp_other2.block_id
    INNER JOIN block_hierarchy AS bh
        ON c.parent_id = bh.id
    WHERE
        bh.permission != 'deny'  -- Останавливаем рекурсию, если у родителя нет прав
        AND bh.depth < %(max_depth)s
)
SELECT
    id,
    parent_id,
    title,
    data,
    updated_at,
    depth,
    permission
FROM block_hierarchy;"""

recursive_set_block_access_query = '''
WITH RECURSIVE subblocks AS (
    -- Шаг 1: выбираем стартовые блоки, если у инициатора есть 'edit_ac' или 'delete'
    SELECT b.id
    FROM api_block b
    JOIN api_blockpermission bp ON b.id = bp.block_id
    WHERE b.id = ANY(%(start_block_ids)s)
      AND bp.user_id = %(initiator_id)s
      AND bp.permission IN ('edit_ac', 'delete')

    UNION ALL

    -- Шаг 2: рекурсивно выбираем дочерние блоки с нужными правами
    SELECT child.id
    FROM api_block child
    JOIN api_blockpermission bp_child ON child.id = bp_child.block_id
    JOIN subblocks sb ON child.parent_id = sb.id
    WHERE bp_child.user_id = %(initiator_id)s
      AND bp_child.permission IN ('edit_ac', 'delete')
),
inserted AS (
    INSERT INTO api_blockpermission (block_id, user_id, permission)
    SELECT s.id, %(target_user_id)s, %(new_permission)s
    FROM subblocks s
    ON CONFLICT (block_id, user_id)
    DO UPDATE SET permission = EXCLUDED.permission
    RETURNING block_id
)
SELECT block_id
FROM inserted
WHERE block_id NOT IN (SELECT unnest(%(start_block_ids)s));
'''

recursive_set_block_group_access_query = '''
WITH RECURSIVE subblocks AS (
    -- Шаг 1: выбираем стартовые блоки, если у инициатора есть 'edit_ac' или 'delete'
    SELECT b.id
    FROM api_block b
    JOIN api_blockpermission bp ON b.id = bp.block_id
    WHERE b.id = ANY(%(start_block_ids)s)
      AND bp.user_id = %(initiator_id)s
      AND bp.permission IN ('edit_ac', 'delete')

    UNION ALL

    -- Шаг 2: рекурсивно выбираем дочерние блоки с нужными правами
    SELECT child.id
    FROM api_block child
    JOIN api_blockpermission bp_child ON child.id = bp_child.block_id
    JOIN subblocks sb ON child.parent_id = sb.id
    WHERE bp_child.user_id = %(initiator_id)s
      AND bp_child.permission IN ('edit_ac', 'delete')
),
group_users AS (
    SELECT gu.user_id
    FROM api_group_users gu
    JOIN api_group g ON gu.group_id = g.id
    WHERE gu.group_id = %(group_id)s
      AND gu.user_id <> g.owner_id
),
inserted AS (
    INSERT INTO api_blockpermission (block_id, user_id, permission)
    SELECT s.id, gu.user_id, %(new_permission)s
    FROM subblocks s
    CROSS JOIN group_users gu
    ON CONFLICT (block_id, user_id)
    DO UPDATE SET permission = EXCLUDED.permission
    RETURNING block_id
)
SELECT block_id
FROM inserted
WHERE block_id NOT IN (SELECT unnest(%(start_block_ids)s));
'''


delete_tree_query = """
WITH RECURSIVE block_hierarchy AS (
    -- Начальная выборка: выбираем стартовый блок и его право
    SELECT
        b.id,
        b.parent_id,
        CASE 
            WHEN bp_delete.block_id IS NOT NULL THEN 'delete'
            ELSE 'deny'
        END AS permission
    FROM api_block AS b
    LEFT JOIN api_blockpermission AS bp_delete
        ON b.id = bp_delete.block_id
        AND bp_delete.user_id = %(user_id)s
        AND bp_delete.permission = 'delete'
    WHERE b.id = %(block_id)s

    UNION ALL

    -- Рекурсивная выборка: собираем все дочерние блоки без ограничения по правам родителя
    SELECT
        c.id,
        c.parent_id,
        CASE 
            WHEN bp_delete2.block_id IS NOT NULL THEN 'delete'
            ELSE 'deny'
        END AS permission
    FROM api_block AS c
    LEFT JOIN api_blockpermission AS bp_delete2
        ON c.id = bp_delete2.block_id
        AND bp_delete2.user_id = %(user_id)s
        AND bp_delete2.permission = 'delete'
    INNER JOIN block_hierarchy AS bh
        ON c.parent_id = bh.id
),
totals AS (
    SELECT 
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE permission = 'delete') AS deletable
    FROM block_hierarchy
)
SELECT bh.id
FROM block_hierarchy AS bh, totals t
WHERE t.total = t.deletable
  AND bh.permission = 'delete';
"""


get_block_for_url = f"""
        WITH RECURSIVE descendants AS (
            SELECT *, 1 AS depth
            FROM api_block
            WHERE id = %(block_id)s
            UNION ALL
            SELECT b.*, d.depth + 1 AS depth
            FROM api_block b
            INNER JOIN descendants d ON b.parent_id = d.id
            WHERE d.depth < %(max_depth)s
        )
        SELECT * FROM descendants;
    """