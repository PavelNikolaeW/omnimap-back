import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union
from uuid import UUID as _UUID

from django.db import transaction
from psqlextra.types import ConflictAction

from api.models import Block, BlockPermission, CHANGE_PERMISSION_CHOICES, BlockLink

DEFAULT_CREATOR_PERMISSION = "delete"


# ---------- Модели отчёта / проблем ------------------------------------------


@dataclass
class ProblemItem:
    block_id: str
    code: str


@dataclass
class Permission:
    user_id: int
    permission: Dict[str, List[str]]

    def to_json(self):
        return {
            "user_id": self.user_id,
            "permission": self.permission
        }


@dataclass
class ImportReport:
    created: Set[_UUID] = field(default_factory=set)
    updated: Set[_UUID] = field(default_factory=set)
    unchanged: Set[_UUID] = field(default_factory=set)
    deleted: Set[_UUID] = field(default_factory=set)

    permissions_upserted: Dict[int, Dict[str, List[str]]] = field(default_factory=dict)
    links_upserted: int = 0

    errors: Set[str] = field(default_factory=set)
    problem_blocks: List[ProblemItem] = field(default_factory=list)

    def add_perms(self, perms):
        for perm in perms:
            permission: str = perm['permission']
            block_id: str = str(perm['block_id'])
            if (user := perm['user_id']) not in self.permissions_upserted.keys():
                self.permissions_upserted[user] = {permission: [block_id]}
            else:
                self.permissions_upserted[user][permission].append(block_id)

    def add_problem(self, block_id: Union[str, _UUID], code: str) -> None:
        self.problem_blocks.append(
            ProblemItem(block_id=str(block_id), code=code)
        )
        self.errors.add(code)

    def to_json(self):
        return json.dumps(
            {
                'created': [str(bid) for bid in self.created],
                'updated': [str(bid) for bid in self.updated],
                'unchanged': [str(bid) for bid in self.unchanged],
                'deleted': [str(bid) for bid in self.deleted],
                'permission_upserted': [
                    {user_id: {permission: bids}}
                    for user_id, perm_idis in self.permissions_upserted.items()
                    for permission, bids in perm_idis.items()
                ],
                'links_upserted': self.links_upserted,
                'errors': list(self.errors),
                'problem_blocks': [{str(i.block_id): i.code} for i in self.problem_blocks]
            }
        )


@dataclass
class ImportContext:
    user: Any
    payload_by_id: Dict[_UUID, dict]  # нормализованный payload
    existing_by_id: Dict[_UUID, dict]  # Block.values(...) по id
    allowed_ids: Set[_UUID]  # какие блоки юзер может менять
    rep: ImportReport
    links_create: List[BlockLink]
    links_update: List[BlockLink]
    deleted_ids: Set[_UUID]
    child_parent: Dict[_UUID, _UUID]  # child -> new_parent (внешние дети)
    perms: Dict[Tuple[_UUID, int], dict]  # (block_id, user_id) -> perm dict
    parent_child: Dict[_UUID, Set[_UUID]] = field(default_factory=dict)  # parent -> remove children

    allowed_perm_fields = {"user_id", "permission"}
    allowed_perm_values = {"view", "edit", "edit_ac", "delete"}

    def add_perms(self, bid: _UUID, permissions: List[dict]) -> None:
        """
        Валидация прав. Конфликты по (block_id, user_id) схлопываются.
        """
        for perm in permissions:
            if not isinstance(perm, dict):
                self.rep.add_problem(bid, "not_valid_permission")
                continue

            extra = set(perm.keys()) - self.allowed_perm_fields
            if extra:
                self.rep.add_problem(bid, "not_valid_permission")
                continue

            if perm.get("permission") not in self.allowed_perm_values:
                self.rep.add_problem(bid, "not_valid_permission_field")
                continue

            user_id = perm.get("user_id")
            if user_id is None:
                self.rep.add_problem(bid, "not_valid_permission")
                continue

            perm_obj = {
                "block_id": bid,
                "user_id": user_id,
                "permission": perm["permission"],
            }
            self.perms[(bid, user_id)] = perm_obj


# ---------- Утилиты ----------------------------------------------------------


def _to_uuid(val: Any) -> Optional[_UUID]:
    if isinstance(val, _UUID):
        return val
    if isinstance(val, str):
        try:
            return _UUID(val)
        except ValueError:
            return None
    return None


def _collect_payload(payload_blocks: Iterable[dict], rep: ImportReport) -> Tuple[Dict[_UUID, dict], set]:
    """
    Валидирует и нормализует payload:
    - id -> UUID
    - parent_id -> UUID или None
    - отбрасывает дубликаты id
    """
    payload_by_id: Dict[_UUID, dict] = {}
    parents_id = set()

    for raw in payload_blocks:
        raw_id = raw.get("id")
        bid = _to_uuid(raw_id)
        if not bid:
            rep.add_problem(str(raw_id), "not_valid_uuid")
            continue

        if bid in payload_by_id:
            rep.add_problem(str(bid), "duplicate_id")
            continue

        block = dict(raw)

        parent_id = block.get("parent_id")
        if parent_id:
            parent_uuid = _to_uuid(parent_id)
            parents_id.add(parent_uuid)
            if not parent_uuid:
                rep.add_problem(str(parent_id), "not_valid_uuid")
                parent_uuid = None
            block["parent_id"] = parent_uuid

        payload_by_id[bid] = block

    return payload_by_id, parents_id


def _load_existing_blocks(ids: Set[_UUID]) -> Dict[_UUID, dict]:
    """
    Загружает минимальный набор полей по существующим блокам.
    Возвращает: id -> {id, title, data, parent_id}
    """
    if not ids:
        return {}

    rows = (
        Block.objects
        .filter(id__in=ids)
        .values("id", "title", "data", "parent_id")
    )
    return {row["id"]: row for row in rows}


def _load_allowed_ids(user) -> Set[_UUID]:
    """
    Какие блоки пользователь имеет право менять.
    """
    if not user:
        return set()

    return set(
        BlockPermission.objects.filter(
            user_id=user,
            permission__in=CHANGE_PERMISSION_CHOICES,
        ).values_list("block_id", flat=True)
    )


# ---------- Детекция циклов --------------------------------------------------


def _build_parent_after(ctx: ImportContext) -> Dict[_UUID, Optional[_UUID]]:
    """
    Итоговая карта child -> parent после применения payload.

    Берём:
    - все id из payload;
    - существующие id из existing_by_id, если на них есть права (allowed_ids),
      чтобы отловить циклы в редактируемой области.

    Приоритет: parent_id из payload, иначе из existing_by_id.
    """
    parent_after: Dict[_UUID, Optional[_UUID]] = {}

    scope: Set[_UUID] = set(ctx.payload_by_id.keys()) | (
            set(ctx.existing_by_id.keys()) & ctx.allowed_ids
    )

    for bid in scope:
        exist = ctx.existing_by_id.get(bid)
        if exist:
            parent_after[bid] = exist.get("parent_id")

    for bid, block in ctx.payload_by_id.items():
        # parent_id уже нормализован в _collect_payload
        parent_after[bid] = block.get("parent_id")

    return parent_after


def detect_cycle(
        payload_by_id: Dict[_UUID, dict],
        block_parent_map: Dict[_UUID, Optional[_UUID]],
        start_id: _UUID,
) -> Optional[Set[_UUID]]:
    """
    идём по цепочке parent'ов:
      - сначала смотрим parent_id из payload (если есть),
      - иначе берём parent_id из block_parent_map (БД по allowed_ids),
    как только количество шагов начинает обгонять количество уникальных id на 1,
    считаем, что есть цикл и возвращаем все собранные id.
    """
    current = start_id
    cycle: Set[_UUID] = set()
    size = 0

    while current:
        cycle.add(current)

        # хак: если шагов на 1 больше, чем уникальных вершин, значит какую-то уже прошли дважды
        if size - len(cycle) == 1:
            return cycle

        size += 1

        block = payload_by_id.get(current)
        if block:
            parent_id = block.get("parent_id")
            if parent_id:
                # в твоей логике здесь просто приведение к UUID
                if isinstance(parent_id, _UUID):
                    current = parent_id
                else:
                    try:
                        current = _UUID(str(parent_id))
                    except ValueError:
                        # невалидный parent — обрываем, цикла нет
                        return None
                continue

        parent_id = block_parent_map.get(current)
        if parent_id:
            current = parent_id
        else:
            current = None

    return None


def _check_cycle(ctx: ImportContext) -> bool:
    """
    - смотрим только на блоки из payload
    - используем parent_id из payload, а когда его нет — parent_id из БД,
      но только для блоков, на которые есть права (allowed_ids)
    - если где-то по этой цепочке detect_cycle что-то нашёл — помечаем cycle_detected.
    """
    # карта parent'ов только по доступным блокам
    map_allowed_block_parent: Dict[_UUID, Optional[_UUID]] = dict(
        Block.objects
        .filter(id__in=ctx.allowed_ids)
        .values_list("id", "parent_id")
    )

    wrong_uuids: Set[_UUID] = set()

    for bid, block in ctx.payload_by_id.items():
        # проверяем только те, у кого есть parent_id в payload
        if block.get("parent_id"):
            if bid not in wrong_uuids:
                cycle = detect_cycle(ctx.payload_by_id, map_allowed_block_parent, bid)
                if cycle:
                    wrong_uuids.update(cycle)

    if wrong_uuids:
        for bid in wrong_uuids:
            ctx.rep.add_problem(block_id=str(bid), code="cycle_detected")

    # true, если есть хотя бы одна проблема цикла
    return any(p.code == "cycle_detected" for p in ctx.rep.problem_blocks)


# ---------- Разбор create / update ------------------------------------------


def _get_create_and_update_blocks(ctx: ImportContext) -> Tuple[Set[_UUID], Set[_UUID]]:
    create_ids: Set[_UUID] = set()
    update_ids: Set[_UUID] = set()

    for bid in ctx.payload_by_id.keys():
        if bid in ctx.existing_by_id:
            if bid in ctx.allowed_ids:
                update_ids.add(bid)
            else:
                ctx.rep.add_problem(str(bid), "forbidden")
        else:
            create_ids.add(bid)

    return create_ids, update_ids


def _check_link(data, ctx, bid, parent_id, create_ids):
    if data and data.get('view', '') == 'link':
        if not (source := data.get('source')):
            ctx.rep.add_problem(bid, 'not_valid_link')
        elif not (source_uuid := _to_uuid(source)):
            ctx.rep.add_problem(bid, 'not_valid_source_uuid')
        elif source_uuid not in ctx.allowed_ids and source_uuid not in create_ids:
            ctx.rep.add_problem(bid, 'not_allowed_link')
        elif not parent_id:
            ctx.rep.add_problem(bid, 'not_link_parent')
        elif source_uuid == parent_id:
            ctx.rep.add_problem(bid, 'wrong_parent_link')
        else:
            return source, parent_id


def _set_update_blocks(update_ids: Set[_UUID], ctx: ImportContext, create_ids, default_perms=None) -> List[Block]:
    """
    Готовит список Block для bulk_update.
    Не мутирует исходный payload_by_id.
    """
    rep = ctx.rep
    payload_by_id = ctx.payload_by_id
    existing_by_id = ctx.existing_by_id
    allowed_ids = ctx.allowed_ids

    rep_add = rep.add_problem
    add_perms = ctx.add_perms
    check_link = _check_link
    to_uuid = _to_uuid
    parent_child = ctx.parent_child
    child_parent = ctx.child_parent
    links_update = ctx.links_update

    # В больших пайлоадах проверка "ключ в payload" быстрее через set
    payload_keys = set(payload_by_id)

    update_blocks: List[Block] = []
    allowed_fields = {"id", "title", "data", "parent_id", "creator", "permissions"}

    for bid in update_ids:
        payload = payload_by_id.get(bid)
        if not payload:
            rep_add(str(bid), "payload_missing")
            continue

        # Без глубокой копии всего payload: работаем с плоской копией только если нужно мутировать
        # (нам надо модифицировать только 'data' и 'parent_id', остальное читаем как есть).
        new_block = payload.copy()

        new_data = new_block.get("data") or {}
        parent_id_val = new_block.get("parent_id")

        old_block = existing_by_id[bid]
        old_data = (old_block.get("data") or {})
        # childOrder часто нужен для membership — сразу set строк
        old_child_order_list = old_data.get("childOrder") or []
        old_child_order_set = set(old_child_order_list)

        old_parent_uuid = old_block.get("parent_id")

        # --- links ---
        link = check_link(new_data, ctx, bid, parent_id_val, create_ids)
        if link:
            links_update.append(BlockLink(source_id=link[0], target_id=link[1]))

        # --- permissions ---
        perms = new_block.pop("permissions", default_perms)
        if perms:
            add_perms(bid, perms)

        # --- parent_id ---
        if parent_id_val is not None:
            if isinstance(parent_id_val, _UUID):
                parent_uuid = parent_id_val
            else:
                parent_uuid = to_uuid(parent_id_val)
                if not parent_uuid:
                    rep_add(str(bid), "not_valid_parent_uuid")

            if parent_uuid and (parent_uuid != old_parent_uuid):
                # обновим индексы перемещений: проверяем принадлежность к payload через set
                if parent_uuid not in payload_keys:
                    if parent_uuid not in allowed_ids:
                        rep_add(str(bid), "not_found_parent")
                    parent_child.setdefault(parent_uuid, set()).add(bid)
                if (old_parent_uuid is not None) and (old_parent_uuid not in payload_keys):
                    child_parent[bid] = old_parent_uuid
            new_block["parent_id"] = parent_uuid

        # --- лишние поля ---
        extra = set(new_block) - allowed_fields
        if extra:
            rep_add(str(bid), "not_valid_field")

        # --- childOrder из payload (если есть) ---
        new_child_order = None
        if isinstance(new_data, dict) and ("childOrder" in new_data):
            raw_co = new_data.get("childOrder") or []
            if not isinstance(raw_co, list):
                rep_add(str(bid), "not_valid_childOrder")
                raw_co = []

            # Формируем сразу корректный список, конвертируя и проверяя по месту
            co_out: List[str] = []
            append_co = co_out.append

            for child in raw_co:
                cu = to_uuid(child)
                if not cu:
                    rep_add(str(bid), "not_valid_childOrder")
                    continue

                # 1) ребёнок приходит в payload
                if cu in payload_keys:
                    append_co(str(cu))
                    continue

                # 2) ребёнок уже был раньше
                if child in old_child_order_set:
                    append_co(str(cu))
                    continue

                # 3) внешний ребёнок, можно перепривязать
                if cu in allowed_ids:
                    child_parent[cu] = bid
                    append_co(str(cu))
                    continue

                # 4) иного не осталось — ошибка
                rep_add(str(bid), "not_found_child")

            new_child_order = co_out
            new_data["childOrder"] = new_child_order

        # --- поиск детей для удаления ---
        if old_child_order_list and ("childOrder" in new_data):
            if new_child_order is not None:
                # всё, что пропало из нового порядка и не придёт в payload — удалить
                new_ids_set = set(new_child_order)
                for old_child in old_child_order_set:
                    if (old_child not in new_ids_set) and (to_uuid(old_child) not in payload_keys):
                        cu = to_uuid(old_child)
                        if cu:
                            ctx.deleted_ids.add(cu)
            else:
                # порядок не трогали: удаляем только детей, которых нет в payload
                for old_child in old_child_order_set:
                    cu = to_uuid(old_child)
                    if cu and (cu not in payload_keys):
                        ctx.deleted_ids.add(cu)

        # Быстрые проверки по лёгким полям
        is_update = False
        nt = new_block.get("title", old_block.get("title"))
        npid = new_block.get("parent_id", old_parent_uuid)

        if (nt != old_block.get("title")) or (npid != old_parent_uuid):
            is_update = True
        else:
            nd = new_block.get("data")
            if nd is not None and nd != old_block.get("data"):
                is_update = True

        if not is_update:
            rep.unchanged.add(bid)
            continue

        # Создаём объект для bulk_update только с нужными полями, без копии всего old_block
        update_blocks.append(
            Block(id=bid, title=nt, data=new_block.get("data", old_block.get("data")), parent_id=npid)
        )
        rep.updated.add(bid)

    return update_blocks


def _set_create_blocks(create_ids: Set[_UUID], ctx: ImportContext, default_perms=list) -> List[Block]:
    """
    Готовит список Block для bulk_create.
    """
    rep = ctx.rep
    rep_add = rep.add_problem

    payload_by_id = ctx.payload_by_id
    payload_keys = set(payload_by_id)
    allowed_ids = ctx.allowed_ids

    user = ctx.user
    add_perms = ctx.add_perms
    check_link = _check_link
    to_uuid = _to_uuid

    parent_child = ctx.parent_child
    child_parent = ctx.child_parent
    links_create = ctx.links_create

    allowed_fields = {"id", "title", "data", "parent_id", "creator", "permissions"}

    new_blocks: List[Block] = []

    for bid in create_ids:
        payload = payload_by_id.get(bid)
        if not payload:
            rep_add(str(bid), "payload_missing")
            continue

        # Нужна только неглубокая копия: будем менять permissions/data/parent_id/creator
        new_block = payload.copy()

        data = new_block.get("data") or {}
        parent_val = new_block.get("parent_id")

        # --- links ---
        link = check_link(data, ctx, bid, parent_val, create_ids)
        if link:
            links_create.append(BlockLink(source_id=link[0], target_id=link[1]))

        # --- permissions: добавляем право создателю ---
        perms = new_block.pop("permissions", default_perms) or []
        perms.append({"user_id": user.id, "permission": DEFAULT_CREATOR_PERMISSION})
        add_perms(bid, perms)

        # --- лишние поля ---
        extra = set(new_block) - allowed_fields
        if extra:
            rep_add(str(bid), "not_valid_field")

        # --- creator ---
        # setdefault, чтобы не перетирать, если уже пришёл явный creator
        new_block.setdefault("creator", user)

        # --- parent_id ---
        parent_uuid: Optional[_UUID] = None
        if parent_val is not None:  # явная проверка, чтобы пустые строки не проходили
            parent_uuid = parent_val if isinstance(parent_val, _UUID) else to_uuid(parent_val)
            if not parent_uuid:
                rep_add(str(bid), "not_valid_uuid")
                parent_uuid = None

        # валидация доступности родителя (если указан)
        if parent_uuid is not None:
            # родитель допустим, если он уже есть в БД (allowed_ids) или создаётся в этом payload
            # индексация перемещений/привязок: если родитель не приходит в payload — фиксируем связь
            if parent_uuid not in payload_keys:
                if parent_uuid not in allowed_ids:
                    rep_add(str(bid), "not_found_parent")
                parent_child.setdefault(parent_uuid, set()).add(bid)

        new_block["parent_id"] = parent_uuid

        # --- childOrder ---
        raw_co = data.get("childOrder") or []
        if raw_co:
            normalized_co: List[str] = []
            append_co = normalized_co.append

            for chid in raw_co:
                cu = to_uuid(chid)
                if not cu:
                    # child некорректный
                    rep_add(str(chid), "not_valid_uuid")
                    continue

                if cu in payload_keys:
                    # ребёнок создаётся сейчас — проверим согласованность его parent
                    child_payload = payload_by_id[cu]
                    child_parent_val = child_payload.get("parent_id")
                    child_parent_uuid = (
                        child_parent_val if isinstance(child_parent_val, _UUID) else to_uuid(child_parent_val)
                    )
                    if child_parent_uuid != bid:
                        rep_add(str(bid), "not_valid_childOrder")
                    append_co(str(cu))
                elif cu in allowed_ids:
                    # внешний блок с правами, перепривязываем
                    child_parent[cu] = bid
                    append_co(str(cu))
                else:
                    rep_add(str(bid), "not_found_child")

            if normalized_co:
                data = dict(data)  # избегаем мутаций исходного словаря из payload
                data["childOrder"] = normalized_co

        # Собираем минимально нужные поля для модели — без лишних ключей
        new_blocks.append(
            Block(
                id=bid,
                title=new_block.get("title"),
                data=data if data is not None else None,
                parent_id=parent_uuid,
                creator=new_block.get("creator", user),
            )
        )
        rep.created.add(bid)

    return new_blocks


# ---------- Применение к БД --------------------------------------------------


def _apply(update_blocks: List[Block], new_blocks: List[Block], ctx: ImportContext, task) -> None:
    """
    Применяет изменения одной транзакцией.
    """
    parent_child = ctx.parent_child  # dict[parent_id -> set(child_id)]
    child_parent = ctx.child_parent  # dict[child_id  -> old_parent_id]
    rep = ctx.rep

    pc_parent_ids = set(parent_child.keys())
    cp_child_ids = set(child_parent.keys())
    cp_child_ids_str = {str(c) for c in cp_child_ids}  # childOrder хранит строки — избегаем _to_uuid в цикле

    with transaction.atomic():
        if new_blocks:
            Block.objects.bulk_create(new_blocks, batch_size=1000)

        # 2) Добавляем новых детей в childOrder у их родителей (которые указаны как parent_child)
        if pc_parent_ids:
            updates = []
            for row in (
                    Block.objects
                            .filter(id__in=pc_parent_ids)
                            .values("id", "data")
                            .iterator(chunk_size=1000)
            ):
                pid = row["id"]
                data = row["data"] or {}
                co = data.get("childOrder")
                if not isinstance(co, list):
                    co = []
                    data["childOrder"] = co
                # extend разом, без множества append в цикле Python
                co.extend(str(c) for c in parent_child.get(pid, ()))
                updates.append(Block(id=pid, data=data))
                rep.updated.add(pid)

            if updates:
                Block.objects.bulk_update(updates, fields=["data"])

        # 3) Чистим childOrder у родителей, откуда «переехали» дети.
        if cp_child_ids:
            updates = []
            for row in (
                    Block.objects
                            .filter(children__in=cp_child_ids)  # родители старых детей
                            .distinct()
                            .values("id", "data")
                            .iterator(chunk_size=1000)
            ):
                pid = row["id"]
                data = row["data"] or {}
                co = data.get("childOrder")
                if not isinstance(co, list):
                    continue

                # Фильтруем одним проходом по строковым id:
                filtered = [cid for cid in co if cid not in cp_child_ids_str]
                if filtered != co:
                    data["childOrder"] = filtered
                    updates.append(Block(id=pid, data=data))
                    rep.updated.add(pid)

            if updates:
                Block.objects.bulk_update(updates, fields=["data"])

            # 4) Переставляем parent_id у "внешних" детей
            moved = [Block(id=child, parent_id=parent) for child, parent in child_parent.items()]
            if moved:
                Block.objects.bulk_update(moved, fields=["parent_id"])
                rep.updated.update(cp_child_ids)

        # 5) Обновления самих блоков
        if update_blocks:
            Block.objects.bulk_update(update_blocks, fields=["title", "data", "parent_id"])

        # 6) Права (upsert)
        if ctx.perms:
            BlockPermission.objects.on_conflict(
                ["block_id", "user_id"],
                ConflictAction.UPDATE,
            ).bulk_insert(list(ctx.perms.values()))
            rep.add_perms(ctx.perms.values())

        # 7) Ссылки
        if ctx.links_create:
            BlockLink.objects.bulk_create(ctx.links_create)
            rep.links_upserted += len(ctx.links_create)

        if ctx.links_update:
            BlockLink.objects.bulk_update(ctx.links_update, fields=["source", "target"])
            rep.links_upserted += len(ctx.links_update)

        # 8) удаляем
        if ctx.deleted_ids:
            Block.objects.filter(id__in=ctx.deleted_ids).delete()


# ---------- Публичная функция ------------------------------------------------

class DummyTask:
    def __init__(self):
        self.states = []

    def update_state(self, state=None, meta=None):
        self.states.append((state, meta))


def import_blocks(payload_blocks: Iterable[dict], user, task=DummyTask(), default_permissions=None) -> ImportReport:
    try:
        task.update_state(state='START')
        rep = ImportReport()
        # 1. нормализуем payload
        payload_by_id, parent_id = _collect_payload(payload_blocks, rep)

        # 2. грузим существующие блоки
        existing_by_id = _load_existing_blocks(parent_id.union(payload_by_id.keys()))

        # 3. права пользователя
        allowed_ids = _load_allowed_ids(user)

        # контекст
        ctx = ImportContext(
            user=user,
            payload_by_id=payload_by_id,
            existing_by_id=existing_by_id,
            allowed_ids=allowed_ids,
            rep=rep,
            deleted_ids=set(),
            child_parent={},
            perms={},
            links_update=[],
            links_create=[]
        )

        # 4. делим на create / update
        create_ids, update_ids = _get_create_and_update_blocks(ctx)
        rep.created |= create_ids

        # 5. если уже есть фатальные проблемы или обнаружены циклы — выходим без изменений
        if rep.problem_blocks or _check_cycle(ctx):
            return rep
        # 6. готовим обновления / создания
        update_blocks = _set_update_blocks(update_ids, ctx, create_ids, default_permissions)
        new_blocks = _set_create_blocks(create_ids, ctx, default_permissions)

        # если в процессе подготовки нашли ошибки — не трогаем БД
        if rep.problem_blocks:
            return rep
        # 7. применяем
        task.update_state(state='DATA_PREPARED')
        rep.deleted = ctx.deleted_ids
        _apply(update_blocks, new_blocks, ctx, task)
        task.update_state(state='SUCCESS', meta={'result': rep.to_json()})
        return rep
    except Exception as e:
        task.update_state(state=str(e), meta={'result': rep.to_json(), 'error': str(e)})
        return rep
