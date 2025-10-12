from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from uuid import UUID as _UUID

from django.contrib.auth import get_user_model
from django.db import transaction

from api.models import Block, BlockPermission, Group, BlockLink

User = get_user_model()

CHUNK = 1000

# Настраиваемые параметры
DEFAULT_CREATOR_PERMISSION = "delete"
MAX_BLOCKS_DEFAULT = 10_000


def convert_blocks_to_import_payload(blocks: dict):
    result_blocks = {}
    remove_uuid = {}

    for block_uuid, block in blocks.items():

        data = block.get("data", {})
        if data.get("view") == "link" and "source" in data:
            remove_uuid.setdefault(block['parent_id'], []).append(
                {'remove_child': block_uuid, 'link_uuid': data["source"]})
            continue

        new_block = deepcopy(block)
        result_blocks[block_uuid] = new_block

    for block_uuid, links in remove_uuid.items():
        for data in links:
            block = result_blocks[block_uuid]
            remove_child = data['remove_child']
            block['data']['childOrder'] = [uuid for uuid in block['data']['childOrder'] if uuid != remove_child]
            block.setdefault('links', []).append(data['link_uuid'])

    return {"blocks": list(result_blocks.values())}


# ========= Базовые структуры отчёта =========

@dataclass
class ProblemItem:
    block_id: str
    code: str
    message: str


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    updated_ids: list[_UUID] = None
    permissions_upserted: int = 0
    links_upserted: int = 0
    errors: List[str] = None
    problem_blocks: List[ProblemItem] = None


# ========= Утилиты =========

def _as_uuid_set(values: Iterable[Any]) -> Set[_UUID]:
    out: Set[_UUID] = set()
    for v in values:
        try:
            out.add(_UUID(str(v)))
        except Exception:
            pass
    return out


def chunked(it: Iterable[Any], n: int):
    buf = []
    for x in it:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def _child_order_get(data: dict) -> List[str]:
    """Берём порядок детей только из camelCase: data['childOrder']."""
    if not isinstance(data, dict):
        return []
    raw = data.get("childOrder")
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw if x is not None]


def _child_order_set(data: dict, order: List[str]) -> dict:
    """Записываем порядок детей только в camelCase: data['childOrder']."""
    d = dict(data or {})
    d["childOrder"] = list(order)
    return d


def _payload_core_equals(existing: Block, payload: dict) -> bool:
    """
    Проверяем равенство «плоских» полей (title, data).
    Если в payload поля нет — не считаем отличием.
    """
    if "title" in payload and (payload.get("title") or None) != (existing.title or None):
        return False
    if "data" in payload:
        pd = payload.get("data") or {}
        ed = existing.data or {}
        # сравниваем childOrder: если списки различаются — считаем различием
        if _child_order_get(pd) != _child_order_get(ed):
            return False
        # записей, кроме childOrder, тоже может быть много — сравним целиком
        if pd != ed:
            return False
    return True


def _detect_cycles(parent_map: Dict[str, Optional[str]]) -> Set[str]:
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {k: WHITE for k in parent_map}
    in_cycle: Set[str] = set()

    def dfs(v: str, stack: List[str]):
        c = color[v]
        if c == BLACK:
            return
        if c == GRAY:
            if v in stack:
                idx = stack.index(v)
                in_cycle.update(stack[idx:])
            return
        color[v] = GRAY
        stack.append(v)
        p = parent_map.get(v)
        if p is not None and p in parent_map:
            dfs(p, stack)
        color[v] = BLACK
        stack.pop()

    for node in parent_map.keys():
        if color[node] == WHITE:
            dfs(node, [])
    return in_cycle


# ========= Шаг 1. Подгрузки и первичные мапы =========

def _collect_ids(payload_blocks: List[dict]) -> Tuple[Dict[str, dict], List[str]]:
    by_id: Dict[str, dict] = {str(b["id"]): b for b in payload_blocks}
    return by_id, list(by_id.keys())


def _load_existing_locked(ids: List[str]) -> Dict[str, Block]:
    qs = Block.objects.select_for_update().filter(id__in=ids)
    return {str(b.id): b for b in qs}


def _load_external_refs(payload_blocks: List[dict], import_ids: Set[_UUID]) -> Dict[str, Block]:
    raw_parent_ids = [it.get("parent_id") for it in payload_blocks if it.get("parent_id") is not None]
    raw_link_target_ids: List[Any] = []
    for it in payload_blocks:
        raw_link_target_ids.extend(it.get("links") or [])
    parent_ids = _as_uuid_set(raw_parent_ids)
    link_target_ids = _as_uuid_set(raw_link_target_ids)
    missing_ref_ids = (parent_ids | link_target_ids) - import_ids
    if not missing_ref_ids:
        return {}
    ref_dict_uuid = Block.objects.in_bulk(missing_ref_ids, field_name="id")
    return {str(k): v for k, v in ref_dict_uuid.items()}


# ========= Шаг 2. Core upsert =========

def _prepare_core_upsert(
        ids: List[str],
        by_id: Dict[str, dict],
        existing: Dict[str, Block],
        default_creator: Optional[User],
        rep: ImportReport,
) -> Tuple[List[Block], List[Block], Set[str]]:
    to_create: List[Block] = []
    to_update: List[Block] = []
    touched_ids_core: Set[str] = set()

    for k in ids:
        item = by_id[k]
        if k not in existing:
            creator_id = item.get("creator_id") or (default_creator.id if default_creator else None)
            if not creator_id:
                rep.problem_blocks.append(
                    ProblemItem(
                        block_id=k, code="creator_missing",
                        message="creator_id is required for new blocks (no default user)."
                    )
                )
                continue
            obj = Block(id=item["id"], title=item.get("title"), data=item.get("data", {}), creator_id=creator_id)
            to_create.append(obj)
        else:
            obj = existing[k]
            if not _payload_core_equals(obj, item):
                if "title" in item:
                    obj.title = item["title"]
                if "data" in item:
                    order = _child_order_get(item.get("data") or {})
                    if order:
                        item["data"] = _child_order_set(item.get("data") or {}, order)
                    obj.data = item["data"]
                to_update.append(obj)
                touched_ids_core.add(k)

    return to_create, to_update, touched_ids_core


def _apply_core_upsert(to_create: List[Block], to_update: List[Block]) -> int:
    created_cnt = 0
    if to_create:
        for batch in chunked(to_create, CHUNK):
            Block.objects.bulk_create(batch, ignore_conflicts=True)
            created_cnt += len(batch)

    if to_update:
        update_fields = ["title", "data"]
        if hasattr(Block, "updated_at"):
            update_fields.append("updated_at")
        Block.objects.bulk_update(to_update, update_fields)

    return created_cnt


# ========= Шаг 3. Родители и childOrder =========

def _compute_parent_after(ids: List[str], by_id: Dict[str, dict], existing: Dict[str, Block]) -> Dict[
    str, Optional[str]]:
    """
    Какой parent будет «после импорта» (для цикло-детекта).
    """
    parent_after: Dict[str, Optional[str]] = {}
    for k in ids:
        db_obj = existing.get(k)
        if not db_obj:
            continue
        desired_parent = by_id[k].get("parent_id", None) if k in by_id else None
        if desired_parent is not None:
            parent_after[k] = str(desired_parent)
        else:
            parent_after[k] = str(db_obj.parent_id) if db_obj.parent_id else None
    return parent_after


def _desired_children_by_parent(by_id: Dict[str, dict]) -> Dict[str, Set[str]]:
    """Карта: parent_id -> множество желаемых child_ids из data.childOrder родителя (по пэйлоаду)."""
    out: Dict[str, Set[str]] = {}
    for sid, it in by_id.items():
        data = it.get("data") or {}
        if "childOrder" not in data:
            continue
        pid = str(sid)  # сам объект — это родитель
        order = _child_order_get(data)
        out[pid] = set(order)
    return out


def _apply_parent_updates(
        ids: List[str],
        by_id: Dict[str, dict],
        blocks_by_id: Dict[str, Block],
        existing: Dict[str, Block],
        rep: ImportReport,
) -> Tuple[Set[str], List[Tuple[Optional[str], Optional[str], str]]]:
    touched_ids: Set[str] = set()
    parent_updates: List[Block] = []
    parent_moves: List[Tuple[Optional[str], Optional[str], str]] = []  # (old_pid, new_pid, child_id)

    parent_after = _compute_parent_after(ids, by_id, existing)
    cycle_nodes = _detect_cycles(parent_after)
    if cycle_nodes:
        for bid in sorted(cycle_nodes):
            rep.problem_blocks.append(
                ProblemItem(
                    block_id=bid, code="cycle_detected",
                    message="cycle in parent chain detected; parent update skipped"
                )
            )

    # ---- (A) Явные обновления родителей из записей детей ----
    for k in ids:
        if k in cycle_nodes:
            continue
        b = existing.get(k)
        if not b:
            continue
        pid = by_id[k].get("parent_id") if k in by_id else None
        if pid is None:
            if (b.parent_id or None) is not None:
                parent_moves.append((str(b.parent_id), None, str(b.id)))
                b.parent = None
                parent_updates.append(b)
                touched_ids.add(str(b.id))
        else:
            np = blocks_by_id.get(str(pid))
            if np is None:
                rep.problem_blocks.append(
                    ProblemItem(
                        block_id=k, code="parent_not_found",
                        message=f"parent_id={pid} not found; parent not updated"
                    )
                )
            else:
                old_pid = str(b.parent_id) if b.parent_id else None
                new_pid = str(np.id)
                if old_pid != new_pid:
                    parent_moves.append((old_pid, new_pid, str(b.id)))
                    b.parent = np
                    parent_updates.append(b)
                    touched_ids.add(str(b.id))

    if parent_updates:
        Block.objects.bulk_update(parent_updates, ["parent"])

    # ---- (B) Примирение по childOrder родителя (childOrder — источник истины) ----
    desired_map = _desired_children_by_parent(by_id)  # parent_id -> set(child_ids)
    desired_children_all: Set[str] = set()
    for s in desired_map.values():
        desired_children_all.update(s)

    if desired_map:
        parent_ids = set(desired_map.keys())

        # 1) Подгружаем родителей пачкой
        parents_map: Dict[str, Block] = {pid: blocks_by_id[pid] for pid in parent_ids if pid in blocks_by_id}
        missing_parent_ids = parent_ids - set(parents_map.keys())
        if missing_parent_ids:
            extra_parents = Block.objects.in_bulk(_as_uuid_set(missing_parent_ids), field_name="id")
            parents_map.update({str(k): v for k, v in extra_parents.items()})
            blocks_by_id.update({str(k): v for k, v in extra_parents.items()})

        # 2) Текущее состояние детей у этих родителей
        db_rows = Block.objects.filter(parent_id__in=_as_uuid_set(parent_ids)).values_list("id", "parent_id")
        current_map: Dict[str, Set[str]] = {}
        current_children_all: Set[str] = set()
        for cid, pid in db_rows:
            sid = str(cid)
            pid_str = str(pid)
            current_map.setdefault(pid_str, set()).add(sid)
            current_children_all.add(sid)

        # 3) Массово подгружаем всех детей, встречающихся в desired_map
        detached_ids_for_cleanup: Set[str] = set()
        children_ids_needed = desired_children_all | current_children_all
        children_map: Dict[str, Block] = {cid: blocks_by_id[cid] for cid in children_ids_needed if cid in blocks_by_id}
        missing_child_ids = children_ids_needed - set(children_map.keys())
        if missing_child_ids:
            extra_children = Block.objects.in_bulk(_as_uuid_set(missing_child_ids), field_name="id")
            children_map.update({str(k): v for k, v in extra_children.items()})
            blocks_by_id.update({str(k): v for k, v in extra_children.items()})

        # 4) Считаем изменения
        to_detach: List[Block] = []
        to_attach: List[Block] = []

        # DETACH: есть в current, но нет в desired
        for pid, cur_children in current_map.items():
            desired_children = desired_map.get(pid)
            if desired_children is None:
                continue  # нет childOrder в пэйлоаде для этого родителя — не трогаем
            for cid in (cur_children - desired_children):
                ch = children_map.get(cid)
                if not ch:
                    continue
                ch.parent = None
                to_detach.append(ch)
                parent_moves.append((pid, None, cid))
                touched_ids.add(cid)
                detached_ids_for_cleanup.add(cid)

        # ATTACH: есть в desired, но ещё не прикреплены к этому родителю
        for pid, desired_children in desired_map.items():
            cur_children = current_map.get(pid, set())
            for cid in (desired_children - cur_children):
                if cid == pid:
                    rep.problem_blocks.append(
                        ProblemItem(block_id=cid, code="attach_self_cycle",
                                    message=f"cannot attach block as child to itself (parent_id={pid})")
                    )
                    continue
                ch = children_map.get(cid)
                par = parents_map.get(pid)
                if not ch or not par:
                    rep.problem_blocks.append(
                        ProblemItem(block_id=str(cid), code="attach_missing",
                                    message=f"cannot attach: block_id={cid} or parent_id={pid} not found")
                    )
                    continue

                # Быстрая защита от цикла: не присоединяем предка к потомку
                probe = par
                cycle = False
                seen: Set[str] = set()
                while probe and str(probe.id) not in seen:
                    if str(probe.id) == str(ch.id):
                        cycle = True
                        break
                    seen.add(str(probe.id))
                    probe = probe.parent
                if cycle:
                    rep.problem_blocks.append(
                        ProblemItem(block_id=str(cid), code="attach_cycle_detected",
                                    message=f"attach would create a cycle (parent_id={pid})")
                    )
                    continue

                old_pid = str(ch.parent_id) if ch.parent_id else None
                if old_pid != pid:
                    ch.parent = par
                    to_attach.append(ch)
                    parent_moves.append((old_pid, pid, str(ch.id)))
                    touched_ids.add(str(ch.id))

        # 5) Применяем изменения
        if to_detach:
            Block.objects.bulk_update(to_detach, ["parent"])
        if to_attach:
            Block.objects.bulk_update(to_attach, ["parent"])

        if detached_ids_for_cleanup:
            payload_ids_set = set(ids)
            ids_to_consider = [
                cid for cid in detached_ids_for_cleanup
                if cid not in desired_children_all and cid not in payload_ids_set
            ]
            if ids_to_consider:
                uuid_candidates = _as_uuid_set(ids_to_consider)
                if uuid_candidates:
                    used_as_parent_ids = {
                        str(pid) for pid in
                        Block.objects.filter(parent_id__in=uuid_candidates).values_list("parent_id", flat=True)
                    }
                    deletable_blocks = Block.objects.filter(id__in=uuid_candidates, parent__isnull=True)
                    for block in deletable_blocks:
                        sid = str(block.id)
                        if sid in used_as_parent_ids:
                            continue
                        block.delete()
                        blocks_by_id.pop(sid, None)
                        touched_ids.add(sid)

    # --- (C) childOrder sync у задетых родителей ---
    parent_ids_to_fix: Set[str] = set()
    for old_pid, new_pid, _child in parent_moves:
        if old_pid:
            parent_ids_to_fix.add(old_pid)
        if new_pid:
            parent_ids_to_fix.add(new_pid)

    if parent_ids_to_fix:
        parents_map: Dict[str, Block] = {}
        for pid in list(parent_ids_to_fix):
            if pid in blocks_by_id:
                parents_map[pid] = blocks_by_id[pid]
        missing_pids = {p for p in parent_ids_to_fix if p not in parents_map}
        if missing_pids:
            extra = Block.objects.in_bulk(_as_uuid_set(missing_pids), field_name="id")
            parents_map.update({str(k): v for k, v in extra.items()})

        changed_parents: Dict[str, Block] = {}

        def _get_order(b: Block) -> List[str]:
            return _child_order_get(b.data or {})

        for old_pid, new_pid, child_id in parent_moves:
            if old_pid and old_pid in parents_map:
                pb = parents_map[old_pid]
                order = [x for x in _get_order(pb) if x != child_id]
                pb.data = _child_order_set(pb.data or {}, order)
                changed_parents[str(pb.id)] = pb

            if new_pid and new_pid in parents_map:
                pb = parents_map[new_pid]
                order = _get_order(pb)
                if child_id not in order:
                    order = order + [child_id]
                    pb.data = _child_order_set(pb.data or {}, order)
                    changed_parents[str(pb.id)] = pb

        if changed_parents:
            Block.objects.bulk_update(list(changed_parents.values()), ["data"])
            touched_ids.update(changed_parents.keys())

    return touched_ids, parent_moves


# ========= Шаг 4. Ссылки =========

def _upsert_links(payload_blocks: List[dict], existing: Dict[str, Block], blocks_by_id: Dict[str, Block]) -> Tuple[
    int, Set[str]]:
    """Создаём дочерние link-блоки и связи BlockLink на основе поля links."""

    def _existing_link_targets(parent_id: str) -> Dict[str, Block]:
        return existing_links_map.setdefault(parent_id, {})

    def _extract_target(data: Optional[dict]) -> Optional[str]:
        if not isinstance(data, dict):
            return None
        target = data.get("target")
        if target:
            return str(target)
        source = data.get("source")
        if source:
            return str(source)
        return None

    parent_ids: Set[str] = set()
    for item in payload_blocks:
        links = item.get("links") or []
        if links:
            parent_ids.add(str(item["id"]))

    if not parent_ids:
        return 0, set()

    # Карта существующих линк-блоков (parent_id -> target_id -> Block)
    existing_link_blocks = Block.objects.filter(parent_id__in=parent_ids, data__view="link")
    existing_links_map: Dict[str, Dict[str, Block]] = {}
    for link_block in existing_link_blocks:
        target_id = _extract_target(link_block.data)
        if not target_id:
            continue
        existing_links_map.setdefault(str(link_block.parent_id), {})[target_id] = link_block

    # Кэш разрешений родителя
    parent_permissions: Dict[str, List[BlockPermission]] = {}

    inserted = 0
    touched: Set[str] = set()
    parents_to_update: Dict[str, Block] = {}

    for item in payload_blocks:
        parent_id = str(item["id"])
        links = item.get("links") or []
        if not links:
            continue

        parent_block = existing.get(parent_id) or blocks_by_id.get(parent_id)
        if not parent_block:
            continue

        parent_links = _existing_link_targets(parent_id)

        for target in links:
            target_id = str(target)
            if target_id == parent_id:
                continue
            if target_id not in blocks_by_id:
                continue
            if target_id in parent_links:
                continue

            link_block = Block.objects.create(
                parent=parent_block,
                creator_id=parent_block.creator_id,
                data={
                    "view": "link",
                    "target": target_id,
                    "source": target_id,
                },
            )

            BlockLink.objects.get_or_create(source_id=target_id, target=link_block)

            if parent_id not in parent_permissions:
                parent_permissions[parent_id] = list(
                    BlockPermission.objects.filter(block_id=parent_id)
                )
            perms_to_clone = parent_permissions[parent_id]
            if perms_to_clone:
                BlockPermission.objects.bulk_create(
                    [
                        BlockPermission(
                            block=link_block,
                            user_id=perm.user_id,
                            permission=perm.permission,
                        )
                        for perm in perms_to_clone
                    ],
                    ignore_conflicts=True,
                )

            order = _child_order_get(parent_block.data or {})
            link_block_id_str = str(link_block.id)
            if link_block_id_str not in order:
                order = order + [link_block_id_str]
                parent_block.data = _child_order_set(parent_block.data or {}, order)
                parents_to_update[parent_id] = parent_block

            parent_links[target_id] = link_block
            blocks_by_id[str(link_block.id)] = link_block

            inserted += 1
            touched.add(parent_id)

    if parents_to_update:
        Block.objects.bulk_update(list(parents_to_update.values()), ["data"])

    return inserted, touched


# ========= Шаг 5. Права =========

def _collect_principals(payload_blocks: List[dict]) -> Tuple[Set[int], Set[int]]:
    user_ids: Set[int] = set()
    group_ids: Set[int] = set()
    for item in payload_blocks:
        perms = item.get("permissions") or {}
        for u in perms.get("users", []):
            user_ids.add(u["user_id"])
        for g in perms.get("groups", []):
            group_ids.add(g["group_id"])
    return user_ids, group_ids


def _inherit_permissions_from_parent_if_needed(
        b: Block,
        item: dict,
        groups_by_id: Dict[int, Group],
) -> List[BlockPermission]:
    """
    Если для нового блока явные permissions не даны, а есть родитель — наследуем снимок прав родителя.
    (Простое копирование существующих user-пермов родителя.)
    """
    perms = item.get("permissions") or {}
    has_explicit = bool(perms.get("users") or perms.get("groups"))
    if has_explicit:
        return []

    parent_id = item.get("parent_id")
    if not parent_id:
        return []

    # Снимаем текущие user-права родителя
    inherited: List[BlockPermission] = []
    for up in BlockPermission.objects.filter(block_id=parent_id):
        inherited.append(BlockPermission(block_id=b.id, user_id=up.user_id, permission=up.permission))
    return inherited


def _upsert_permissions(payload_blocks, existing: Dict[str, Block], default_creator: Optional[User], rep: ImportReport):
    """
    Обновляем/создаём права.
    Возвращает (количество реально вставленных/обновлённых прав, множество touched_ids).
    """
    new_user_perms: List[BlockPermission] = []
    updated_user_perms: List[BlockPermission] = []
    touched: Set[str] = set()

    # Существующие user-perms для всех блоков в партии
    existing_up: Dict[Tuple[str, int], BlockPermission] = {
        (str(p.block_id), p.user_id): p
        for p in BlockPermission.objects.filter(block_id__in=list(existing.keys()))
    }

    for k, item in payload_blocks.items() if isinstance(payload_blocks, dict) else [(b["id"], b) for b in
                                                                                    payload_blocks]:
        b = existing.get(str(k))
        if not b:
            continue

        # --- permissions от payload ---
        for up in item.get("permissions", {}).get("users", []):
            uid = up.get("user_id")
            perm = up.get("permission")
            if not uid or not perm:
                continue
            key = (str(b.id), uid)
            obj = existing_up.get(key)
            if obj:
                if obj.permission != perm:
                    obj.permission = perm
                    updated_user_perms.append(obj)
                    touched.add(str(b.id))
            else:
                new_user_perms.append(BlockPermission(block_id=b.id, user_id=uid, permission=perm))
                touched.add(str(b.id))

        for gp in item.get("permissions", {}).get("groups", []):
            gid = gp.get("group_id")
            perm = gp.get("permission")
            if not gid or not perm:
                continue
            grp = Group.objects.filter(id=gid).first()
            if not grp:
                rep.problem_blocks.append(
                    ProblemItem(block_id=str(b.id), code="group_missing",
                                message=f"group_id={gid} not found; group permissions skipped")
                )
                continue
            for u in grp.users.all():
                key = (str(b.id), u.id)
                obj = existing_up.get(key)
                if obj:
                    if obj.permission != perm:
                        obj.permission = perm
                        updated_user_perms.append(obj)
                        touched.add(str(b.id))
                else:
                    new_user_perms.append(BlockPermission(block_id=b.id, user_id=u.id, permission=perm))
                    touched.add(str(b.id))

        # --- ensure creator permission ---
        creator_id = item.get("creator_id") or (
            b.creator_id if b and b.creator_id else (default_creator.id if default_creator else None)
        )
        if creator_id:
            key = (str(b.id), creator_id)
            obj = existing_up.get(key)
            if not obj:
                new_user_perms.append(
                    BlockPermission(block_id=b.id, user_id=creator_id, permission=DEFAULT_CREATOR_PERMISSION)
                )
                touched.add(str(b.id))
        else:
            rep.problem_blocks.append(
                ProblemItem(block_id=str(b.id), code="creator_missing_permission",
                            message="cannot ensure default creator permission (no creator resolved)")
            )

    # --- фактические изменения ---
    inserted = 0
    if new_user_perms:
        # bulk_create c ignore_conflicts=True не даёт понять сколько реально вставлено,
        # поэтому сравниваем до/после
        before_count = BlockPermission.objects.count()
        for batch in chunked(new_user_perms, CHUNK):
            BlockPermission.objects.bulk_create(batch, ignore_conflicts=True)
        after_count = BlockPermission.objects.count()
        inserted += (after_count - before_count)

    if updated_user_perms:
        BlockPermission.objects.bulk_update(updated_user_perms, ["permission"])
        inserted += len(updated_user_perms)

    return inserted, touched


# ========= Главная функция =========

@transaction.atomic
def import_blocks(
        payload_blocks: List[dict],
        *,
        default_creator: Optional[User],
        max_blocks: int = MAX_BLOCKS_DEFAULT,
) -> ImportReport:
    """
    Двухпроходный импорт/апдейт блоков с учётом:
      - связей parent (с цикло-детектом; не сбрасываем на None, если родителя нет),
      - ссылок (BlockLink) на внешние блоки,
      - прав (пользователи + группы -> snapshot в user-права) + дефолт создателю,
      - наследования прав от родителя для новых блоков (если явных прав не передано),
      - поддержки порядка детей в parent.data.childOrder/child_order,
    """
    rep = ImportReport(errors=[], problem_blocks=[])

    # 0) лимит и пустой вход
    if len(payload_blocks) > max_blocks:
        rep.errors.append(f"Too many blocks: {len(payload_blocks)} > {max_blocks}")
        return rep
    if not payload_blocks:
        return rep

    # 1) первичные мапы
    by_id, ids = _collect_ids(payload_blocks)
    existing = _load_existing_locked(ids)

    import_ids_uuid = _as_uuid_set(ids)
    ref_map = _load_external_refs(payload_blocks, import_ids_uuid)

    # единая мапа «знаем обо всех» (и партия, и внешние)
    blocks_by_id: Dict[str, Block] = {**ref_map, **existing}

    # 2) core upsert
    to_create, to_update, touched_core = _prepare_core_upsert(ids, by_id, existing, default_creator, rep)
    created_cnt = _apply_core_upsert(to_create, to_update)
    rep.created += created_cnt

    # перечитать существующие из партии после create
    if created_cnt:
        existing = _load_existing_locked(ids)
        blocks_by_id.update(existing)

    # 3) parents + childOrder
    touched_parent, _moves = _apply_parent_updates(ids, by_id, blocks_by_id, existing, rep)

    # 4) permissions
    perms_inserted, touched_perms = _upsert_permissions(payload_blocks, existing, default_creator, rep)
    rep.permissions_upserted += perms_inserted

    # 5) links
    links_inserted, touched_links = _upsert_links(payload_blocks, existing, blocks_by_id)
    rep.links_upserted += links_inserted

    # 6) метрики updated / unchanged
    touched_ids = set().union(touched_core, touched_parent, touched_links, touched_perms)
    existing_in_batch = {bid for bid in ids if bid in existing}
    rep.updated_ids = list(existing_in_batch & touched_ids)
    rep.updated = len(rep.updated_ids)
    rep.unchanged = len(existing_in_batch - touched_ids)

    return rep
