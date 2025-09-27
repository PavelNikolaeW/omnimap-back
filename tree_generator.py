# gen_blocks.py
from __future__ import annotations

import argparse
import json
import random
import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date
from typing import Any, Dict, List, Optional, Tuple, Iterable, Union
from uuid import uuid4

ISO = "%Y-%m-%dT%H:%M:%SZ"


# ---------- общие утилиты ----------

def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(ISO)


def make_uuid() -> str:
    return str(uuid4())


def as_block(
    _id: str,
    title: str,
    data: dict,
    parent_id: Optional[str],
    updated_at: str,
    creator_id: Optional[str] = None,
) -> dict:
    block = {
        "id": _id,
        "title": title,
        "data": data,
        "parent_id": parent_id,
        "links": [],              # оставляем поле, но не генерируем ссылки
        "permissions": {},
        "updated_at": updated_at,
    }
    if creator_id:
        block["creator_id"] = creator_id
    return block


# ---------- вспомогалки по childOrder ----------

def apply_child_orders(blocks: List[dict]) -> None:
    """Проставляет data.childOrder для всех родителей на основе текущих parent_id."""
    children_map: Dict[str, List[str]] = {}
    for b in blocks:
        pid = b.get("parent_id")
        if pid is not None:
            children_map.setdefault(pid, []).append(b["id"])
    by_id = {b["id"]: b for b in blocks}
    for parent_id, child_ids in children_map.items():
        parent = by_id.get(parent_id)
        if not parent:
            continue
        d = dict(parent.get("data") or {})
        d["childOrder"] = list(child_ids)
        parent["data"] = d


# ---------- режим: произвольное дерево ----------

def generate_tree(
    total: int,
    max_children: int,
    max_depth: int,
    creator_id: Optional[str],
    start_dt: Optional[datetime] = None,
) -> Dict[str, List[dict]]:
    """
    Строит сбалансированное дерево ширины до max_children и глубины до max_depth.
    Без генерации ссылок.
    """
    if total < 1:
        raise ValueError("total must be >= 1")
    if max_children < 1:
        max_children = 1
    if max_depth < 1:
        raise ValueError("max_depth must be >= 1")

    now = datetime.now(timezone.utc)
    base = start_dt.astimezone(timezone.utc) if start_dt else now

    nodes = [{"id": make_uuid()} for _ in range(total)]
    nodes[0]["title"] = "Root"
    nodes[0]["parent_id"] = None

    current_level: List[int] = [0]
    created = 1
    depth = 1

    while created < total and depth < max_depth:
        next_level: List[int] = []
        parents = current_level

        for pi, p in enumerate(parents):
            if created >= total:
                break
            parents_left = len(parents) - pi - 1
            min_c = 1 if (total - created) > parents_left else 0
            max_c = min(max_children, (total - created) - parents_left)
            if max_c < min_c:
                max_c = min_c
            cnum = random.randint(min_c, max_c) if max_c > min_c else max_c
            for _ in range(cnum):
                if created >= total:
                    break
                idx = created
                nodes[idx]["parent_id"] = nodes[p]["id"]
                nodes[idx]["title"] = f"Node {idx}"
                next_level.append(idx)
                created += 1

        if not next_level:
            # цепочка вниз
            while created < total and depth < max_depth:
                parent_idx = current_level[-1]
                idx = created
                nodes[idx]["parent_id"] = nodes[parent_idx]["id"]
                nodes[idx]["title"] = f"Node {idx}"
                next_level = [idx]
                created += 1
                depth += 1
                current_level = next_level
                next_level = []
            break

        current_level = next_level
        depth += 1

    # добросыпка на последний уровень
    if created < total:
        i = 0
        while created < total:
            parent_idx = current_level[i % len(current_level)]
            idx = created
            nodes[idx]["parent_id"] = nodes[parent_idx]["id"]
            nodes[idx]["title"] = f"Node {idx}"
            created += 1
            i += 1

    # атрибуты
    for i, n in enumerate(nodes):
        if "title" not in n:
            n["title"] = f"Node {i}"
        if "parent_id" not in n and i != 0:
            n["parent_id"] = nodes[0]["id"]
        n["data"] = {"text": f"auto-generated node #{i}"}
        n["updated_at"] = iso(base + timedelta(minutes=i))
        if creator_id is not None:
            n["creator_id"] = creator_id

    blocks = [
        as_block(
            _id=n["id"],
            title=n["title"],
            data=n["data"],
            parent_id=n.get("parent_id"),
            updated_at=n["updated_at"],
            creator_id=n.get("creator_id"),
        )
        for n in nodes
    ]
    apply_child_orders(blocks)
    return {"blocks": blocks}


# ---------- режим: реальный календарь ----------

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def iter_iso_weeks_of_month(year: int, month: int) -> List[int]:
    """Возвращает отсортированный список ISO-номеров недель, которые покрывают любой день данного месяца."""
    weeks: set[int] = set()
    days_in_month = calendar.monthrange(year, month)[1]
    for d in range(1, days_in_month + 1):
        w = date(year, month, d).isocalendar().week
        weeks.add(w)
    # ISO-недели могут «перекрываться» на границе годов — это нормально
    return sorted(weeks)


def generate_calendar_real(
    year: int,
    include_quarters: bool = True,
    include_weeks: bool = True,
    include_days: bool = True,
    creator_id: Optional[str] = None,
    start_dt: Optional[datetime] = None,
) -> Dict[str, List[dict]]:
    """
    Реальная календарная структура:
      year
        └── (optional) Q1..Q4
            └── months (12 штук)
                └── (optional) weeks (ISO с учётом пересечений)
                    └── (optional) days (реальное количество)
    """
    now = datetime.now(timezone.utc)
    base = start_dt.astimezone(timezone.utc) if start_dt else now

    blocks: List[dict] = []
    id_by_path: Dict[Tuple[str, ...], str] = {}
    t = 0

    def new_node(path: Tuple[str, ...], title: str, parent_path: Optional[Tuple[str, ...]], extra_data: Optional[dict] = None):
        nonlocal t
        _id = make_uuid()
        parent_id = id_by_path[parent_path] if parent_path else None
        data = {"text": "/".join(path)}
        if extra_data:
            data.update(extra_data)
        block = as_block(
            _id=_id,
            title=title,
            data=data,
            parent_id=parent_id,
            updated_at=iso(base + timedelta(minutes=t)),
            creator_id=creator_id,
        )
        blocks.append(block)
        id_by_path[path] = _id
        t += 1

    # root (year)
    y_path = (str(year),)
    new_node(y_path, str(year), None, {"kind": "year"})

    quarters = [("Q1", [0,1,2]), ("Q2", [3,4,5]), ("Q3", [6,7,8]), ("Q4", [9,10,11])]

    if include_quarters:
        for qname, month_idxs in quarters:
            q_path = y_path + (qname,)
            new_node(q_path, qname, y_path, {"kind": "quarter"})
            parent_for_months = q_path
            for mi in month_idxs:
                mname = MONTHS[mi]
                m_path = parent_for_months + (mname,)
                new_node(m_path, mname, parent_for_months, {"kind": "month", "year": year, "month_index": mi+1})
                _add_weeks_days(year, mi+1, m_path, include_weeks, include_days, new_node)
    else:
        # месяцы сразу под годом
        for mi in range(12):
            mname = MONTHS[mi]
            m_path = y_path + (mname,)
            new_node(m_path, mname, y_path, {"kind": "month", "year": year, "month_index": mi+1})
            _add_weeks_days(year, mi+1, m_path, include_weeks, include_days, new_node)

    apply_child_orders(blocks)
    return {"blocks": blocks}


def _add_weeks_days(
    year: int,
    month_num: int,
    parent_path: Tuple[str, ...],
    include_weeks: bool,
    include_days: bool,
    new_node_fn,
):
    """Вспомогалка для generate_calendar_real: добавляет недели/дни под месяц."""
    if include_weeks:
        weeks = iter_iso_weeks_of_month(year, month_num)
        for w in weeks:
            wname = f"ISO Week {w}"
            w_path = parent_path + (wname,)
            new_node_fn(w_path, wname, parent_path, {"kind": "iso_week", "iso_week": w, "year": year, "month_index": month_num})
            if include_days:
                # дни, попадающие в эту ISO-неделю (даже если часть дней с соседних месяцев)
                days_in_month = calendar.monthrange(year, month_num)[1]
                for d in range(1, days_in_month + 1):
                    the_date = date(year, month_num, d)
                    if the_date.isocalendar().week == w:
                        dname = the_date.strftime("Day %d (%Y-%m-%d)")
                        d_path = w_path + (dname,)
                        new_node_fn(d_path, dname, w_path, {"kind": "day", "date": the_date.isoformat()})
    elif include_days:
        days_in_month = calendar.monthrange(year, month_num)[1]
        for d in range(1, days_in_month + 1):
            the_date = date(year, month_num, d)
            dname = the_date.strftime("Day %d (%Y-%m-%d)")
            d_path = parent_path + (dname,)
            new_node_fn(d_path, dname, parent_path, {"kind": "day", "date": the_date.isoformat()})


# ---------- режим: правила ----------

"""
Правила — это JSON-описание структуры. Мини-DSL:

Корень файла правил — объект с полем "root" (объект) и, опционально, "start_data" (dict).
Каждое правило описывает, как под данным узлом создавать дочерние узлы.

Общие поля узла при генерации:
- title: строка или шаблон (поддерживаются {i}, {index}, {value}, {path})
- data: dict (можно использовать те же плейсхолдеры как в title)
- children: список правил (применяются к каждому созданному узлу)
- kind: произвольная строка (попадает в data["kind"])

Способы генерации множества детей (выбери один из):
1) {"repeat": N} — создаст N узлов. В шаблонах доступны {i} или {index} от 1..N.
2) {"from_list": ["A","B","C"]} — по элементу списка. В шаблонах {value}.
3) {"calendar": {"level": "quarters"|"months"|"weeks"|"days", "year": 2025, "month": 9}}
   - "quarters" создаёт Q1..Q4
   - "months" создаёт 12 месяцев
   - "weeks" требует {"year":Y,"month":M} и создаёт ISO-недели месяца
   - "days" требует {"year":Y,"month":M} и создаёт дни месяца

Пример очень коротких правил см. после кода.
"""

@dataclass
class Rule:
    title: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    kind: Optional[str] = None
    children: Optional[List["Rule"]] = None
    repeat: Optional[int] = None
    from_list: Optional[List[Any]] = None
    calendar: Optional[Dict[str, Any]] = None


def render_template(s: str, ctx: Dict[str, Any]) -> str:
    try:
        return s.format(**ctx)
    except Exception:
        return s  # не роняем генерацию из-за шаблона


def deep_format(obj: Any, ctx: Dict[str, Any]) -> Any:
    if isinstance(obj, str):
        return render_template(obj, ctx)
    if isinstance(obj, list):
        return [deep_format(x, ctx) for x in obj]
    if isinstance(obj, dict):
        return {k: deep_format(v, ctx) for k, v in obj.items()}
    return obj


def parse_rule(obj: Dict[str, Any]) -> Rule:
    children = [parse_rule(c) for c in obj.get("children", [])]
    return Rule(
        title=obj.get("title"),
        data=obj.get("data"),
        kind=obj.get("kind"),
        children=children or None,
        repeat=obj.get("repeat"),
        from_list=obj.get("from_list"),
        calendar=obj.get("calendar"),
    )


def generate_by_rules(
    rules_doc: Dict[str, Any],
    creator_id: Optional[str],
    start_dt: Optional[datetime] = None,
) -> Dict[str, List[dict]]:
    """
    Ожидает JSON вида:
    {
      "root": { ...rule... },
      "start_data": { ... }  # опц., попадёт в корневой data (поверх авто-текста)
    }
    """
    now = datetime.now(timezone.utc)
    base = start_dt.astimezone(timezone.utc) if start_dt else now

    blocks: List[dict] = []
    t = 0

    def new_block(title: str, parent_id: Optional[str], data: dict) -> str:
        nonlocal t
        _id = make_uuid()
        block = as_block(
            _id=_id,
            title=title,
            data=data,
            parent_id=parent_id,
            updated_at=iso(base + timedelta(minutes=t)),
            creator_id=creator_id,
        )
        blocks.append(block)
        t += 1
        return _id

    def expand_calendar(cal: Dict[str, Any]) -> List[Dict[str, Any]]:
        level = cal.get("level")
        year = cal.get("year")
        month = cal.get("month")
        out: List[Dict[str, Any]] = []
        if level == "quarters":
            for qname in ("Q1", "Q2", "Q3", "Q4"):
                out.append({"title": qname, "data": {"kind": "quarter"}})
        elif level == "months":
            for idx, mname in enumerate(MONTHS, start=1):
                out.append({"title": mname, "data": {"kind": "month", "month_index": idx, "year": year}})
        elif level == "weeks":
            if year is None or month is None:
                raise ValueError("calendar level 'weeks' requires 'year' and 'month'")
            for w in iter_iso_weeks_of_month(year, month):
                out.append({"title": f"ISO Week {w}", "data": {"kind": "iso_week", "iso_week": w, "year": year, "month_index": month}})
        elif level == "days":
            if year is None or month is None:
                raise ValueError("calendar level 'days' requires 'year' and 'month'")
            for d in range(1, calendar.monthrange(year, month)[1] + 1):
                the_date = date(year, month, d)
                out.append({"title": the_date.strftime("Day %d (%Y-%m-%d)"),
                            "data": {"kind": "day", "date": the_date.isoformat()}})
        else:
            raise ValueError("unknown calendar level")
        return out

    def expand_rule_instances(rule: Rule, ctx_base: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Возвращает список «инстансов» (title, data) для данного правила на одном уровне.
        """
        instances: List[Dict[str, Any]] = []
        if rule.repeat:
            for i in range(1, rule.repeat + 1):
                ctx = dict(ctx_base)
                ctx.update({"i": i, "index": i})
                title = rule.title or "Item {index}"
                title = render_template(title, ctx)
                data = deep_format(rule.data or {}, ctx)
                if rule.kind:
                    data = dict(data or {})
                    data["kind"] = rule.kind
                instances.append({"title": title, "data": data, "value": i})
        elif rule.from_list:
            for idx, val in enumerate(rule.from_list, start=1):
                ctx = dict(ctx_base)
                ctx.update({"i": idx, "index": idx, "value": val})
                title = rule.title or "{value}"
                title = render_template(title, ctx)
                data = deep_format(rule.data or {}, ctx)
                if rule.kind:
                    data = dict(data or {})
                    data["kind"] = rule.kind
                instances.append({"title": title, "data": data, "value": val})
        elif rule.calendar:
            cal_items = expand_calendar(rule.calendar)
            for idx, it in enumerate(cal_items, start=1):
                ctx = dict(ctx_base)
                ctx.update({"i": idx, "index": idx, "value": it["title"]})
                title = render_template(rule.title or it["title"], ctx)
                data = dict(it.get("data") or {})
                # накладываем/переопределяем полем data из правила
                if rule.data:
                    data = deep_format({**data, **rule.data}, ctx)
                if rule.kind:
                    data["kind"] = rule.kind
                instances.append({"title": title, "data": data, "value": it})
        else:
            # одиночный узел
            ctx = dict(ctx_base)
            title = render_template(rule.title or "Node", ctx)
            data = deep_format(rule.data or {}, ctx)
            if rule.kind:
                data = dict(data or {})
                data["kind"] = rule.kind
            instances.append({"title": title, "data": data, "value": None})
        return instances

    # корень
    if "root" not in rules_doc:
        raise ValueError("Rules JSON must contain 'root' object")
    root_rule = parse_rule(rules_doc["root"])
    start_data = rules_doc.get("start_data") or {}

    # создаём корневой блок (одиночный)
    root_instances = expand_rule_instances(root_rule, ctx_base={"path": []})
    if len(root_instances) != 1:
        raise ValueError("root rule must produce exactly one node")
    root_inst = root_instances[0]
    root_data = {"text": root_inst["title"], **root_inst["data"], **start_data}
    root_id = new_block(root_inst["title"], None, root_data)

    # рекурсивная генерация детей
    def build_children(parent_id: str, parent_rule: Rule, parent_ctx_path: List[str], calendar_context: Dict[str, Any]):
        """
        Для каждого дочернего правила из parent_rule.children порождаем набор узлов.
        calendar_context — позволяет прокидывать year/month вниз, если надо.
        """
        if not parent_rule.children:
            return
        for child_rule in parent_rule.children:
            # контекст доступный в шаблонах
            ctx_base = {
                "path": "/".join(parent_ctx_path),
                **calendar_context
            }
            instances = expand_rule_instances(child_rule, ctx_base)
            new_ids: List[str] = []
            for idx, inst in enumerate(instances, start=1):
                title = inst["title"]
                data = dict(inst["data"] or {})
                data.setdefault("text", title)
                child_id = new_block(title, parent_id, data)
                new_ids.append(child_id)

                # пересчитать calendar контекст, если этот узел задаёт год/месяц
                cal_ctx = dict(calendar_context)
                if isinstance(inst.get("value"), dict):
                    v = inst["value"]
                    if "year" in v:
                        cal_ctx["year"] = v["year"]
                    if "month_index" in v:
                        cal_ctx["month"] = v["month_index"]

                build_children(child_id, child_rule, parent_ctx_path + [title], cal_ctx)

    build_children(root_id, root_rule, [root_inst["title"]], {})
    apply_child_orders(blocks)
    return {"blocks": blocks}


# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser(description="Generate JSON blocks tree")
    ap.add_argument("--mode", choices=["tree", "calendar", "rules"], default="tree", help="Generation mode")
    # tree mode
    ap.add_argument("--nodes", type=int, default=20, help="[tree] total number of nodes (>=1)")
    ap.add_argument("--max-children", type=int, default=3, help="[tree] max children per node")
    ap.add_argument("--max-depth", type=int, default=5, help="[tree] max depth")
    # calendar mode (real)
    ap.add_argument("--year", type=int, default=datetime.now().year, help="[calendar] year")
    ap.add_argument("--no-quarters", action="store_true", help="[calendar] do not include quarters")
    ap.add_argument("--no-weeks", action="store_true", help="[calendar] do not include ISO weeks")
    ap.add_argument("--no-days", action="store_true", help="[calendar] do not include days")
    # rules mode
    ap.add_argument("--rules-file", type=str, default=None, help="[rules] path to JSON rules file")

    # common
    ap.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility (tree only)")
    ap.add_argument("--with-creator", type=str, default=None, help="creator_id for every block")
    ap.add_argument("--start-date", type=str, default=None,
                    help="ISO8601 base date for updated_at (e.g. 2025-09-21T10:00:00Z)")
    ap.add_argument("--out", type=str, default=None, help="Output file; if omitted, prints to stdout")

    args = ap.parse_args()
    if args.seed is not None:
        random.seed(args.seed)

    start_dt = None
    if args.start_date:
        try:
            start_dt = datetime.strptime(args.start_date, ISO).replace(tzinfo=timezone.utc)
        except Exception:
            raise SystemExit("Invalid --start-date, expected like 2025-09-21T10:00:00Z")

    if args.mode == "tree":
        payload = generate_tree(
            total=args.nodes,
            max_children=args.max_children,
            max_depth=args.max_depth,
            creator_id=args.with_creator,
            start_dt=start_dt,
        )
    elif args.mode == "calendar":
        payload = generate_calendar_real(
            year=args.year,
            include_quarters=not args.no_quarters,
            include_weeks=not args.no_weeks,
            include_days=not args.no_days,
            creator_id=args.with_creator,
            start_dt=start_dt,
        )
    else:
        if not args.rules_file:
            raise SystemExit("--rules-file is required for --mode rules")
        with open(args.rules_file, "r", encoding="utf-8") as f:
            rules_doc = json.load(f)
        payload = generate_by_rules(
            rules_doc=rules_doc,
            creator_id=args.with_creator,
            start_dt=start_dt,
        )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    # python tree_generator.py --mode tree --nodes 50 --max-children 4 --max-depth 5 --with-creator "1" --out blocks.json
    # python tree_generator.py --mode calendar --year 2025 --out calendar.json
    # python tree_generator.py --mode calendar --year 2025 --no-weeks --no-days --out calendar_months.json
    main()
