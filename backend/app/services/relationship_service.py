"""Relationships between tables: suggestions, user confirmation and join-path planning.

Join paths only ever use relationships that are declared in the database or confirmed
by the user. Suggested relationships are never used until they are confirmed.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.core.db import quote
from app.schemas import (
    DataSource,
    Join,
    JoinAmbiguity,
    JoinPlan,
    Relationship,
    RelationshipCreate,
    RelationshipSuggestion,
    SchemaInfo,
)
from app.services import registry
from app.services.connectors import Connector
from app.services.schema_service import build_schema, is_unique_key, singular

# Only integer and text columns are plausible keys.
KEY_TYPES = {"integer", "text"}
# Same-name matches on these are too common to be evidence of a relationship.
GENERIC_NAMES = {
    "id", "name", "title", "description", "type", "status", "date", "value", "amount",
    "price", "quantity", "notes", "email", "created_at", "updated_at", "category",
}
MIN_MATCH_RATIO = 0.9
MAX_PATHS_PER_TARGET = 8


class RelationshipError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ------------------------------------------------------------- suggestions

def _names_match(child_column: str, parent_table: str, parent_column: str) -> bool:
    if child_column == parent_column and parent_column not in GENERIC_NAMES:
        return True
    return parent_column == "id" and child_column in {
        f"{singular(parent_table)}_id", f"{parent_table}_id"}


def _match_ratio(engine: Engine, conn: Connection, child: tuple[str, str], parent: tuple[str, str]) -> tuple[int, float]:
    """(distinct non-null child values, share of them found in the parent column)."""
    ct, cc = quote(engine, child[0]), quote(engine, child[1])
    pt, pc = quote(engine, parent[0]), quote(engine, parent[1])
    total, matched = conn.execute(text(
        f"SELECT COUNT(*), COALESCE(SUM(CASE WHEN v IN (SELECT {pc} FROM {pt}) THEN 1 ELSE 0 END), 0) "
        f"FROM (SELECT DISTINCT {cc} AS v FROM {ct} WHERE {cc} IS NOT NULL)"
    )).one()
    return total, (matched / total if total else 0.0)


def suggest_relationships(datasource: DataSource, connector: Connector) -> list[RelationshipSuggestion]:
    schema = build_schema(datasource, connector)
    known = set()
    for r in schema.relationships:  # both directions, so a known link is never suggested backwards
        known.add((r.from_table, tuple(r.from_columns), r.to_table, tuple(r.to_columns)))
        known.add((r.to_table, tuple(r.to_columns), r.from_table, tuple(r.from_columns)))
    rows = {t.name: t.row_count for t in schema.tables}

    candidates = []
    for a in schema.tables:
        for c in a.columns:
            if c.type not in KEY_TYPES:
                continue
            for b in schema.tables:
                if b.name == a.name:
                    continue
                for p in b.columns:
                    if p.type == c.type and _names_match(c.name, b.name, p.name):
                        if (a.name, (c.name,), b.name, (p.name,)) not in known:
                            candidates.append((a.name, c.name, b.name, p.name))

    unique_cache: dict[tuple[str, str], bool] = {}
    best: dict[frozenset, RelationshipSuggestion] = {}
    with connector.engine() as engine, engine.connect() as conn:
        def unique(table: str, column: str) -> bool:
            key = (table, column)
            if key not in unique_cache:
                unique_cache[key] = is_unique_key(engine, conn, table, column)
            return unique_cache[key]

        for a, c, b, p in candidates:
            if not unique(b, p):
                continue  # the parent side of a relationship must identify its rows
            total, ratio = _match_ratio(engine, conn, (a, c), (b, p))
            if total == 0 or ratio < MIN_MATCH_RATIO:
                continue
            one_to_one = unique(a, c)
            suggestion = RelationshipSuggestion(
                from_table=a, from_column=c, to_table=b, to_column=p,
                cardinality="one_to_one" if one_to_one else "many_to_one",
                match_ratio=round(ratio, 4),
                reason=(f"Column names match, {ratio:.0%} of the {total} distinct values in "
                        f"{a}.{c} exist in {b}.{p}, which is unique."),
            )
            pair = frozenset({(a, c), (b, p)})
            prior = best.get(pair)
            # When both sides are unique the same link is found twice; keep the larger
            # table as the child so the result is deterministic.
            if prior is None or (rows[a], b) > (rows[prior.from_table], prior.to_table):
                best[pair] = suggestion

    return sorted(best.values(), key=lambda s: (-s.match_ratio, s.from_table, s.from_column))


# ----------------------------------------------------- user confirmation

def add_user_relationship(datasource: DataSource, connector: Connector, body: RelationshipCreate) -> Relationship:
    if datasource.kind == "sample":
        raise RelationshipError("The sample database cannot be modified.", 403)
    if body.from_table == body.to_table:
        raise RelationshipError("Self-referencing relationships are not supported yet.")

    schema = build_schema(datasource, connector)
    tables = {t.name: t for t in schema.tables}
    for table, column in ((body.from_table, body.from_column), (body.to_table, body.to_column)):
        if table not in tables or column not in {c.name for c in tables[table].columns}:
            raise RelationshipError(f"Column {table}.{column} was not found.", 404)

    child_type = next(c.type for c in tables[body.from_table].columns if c.name == body.from_column)
    parent_type = next(c.type for c in tables[body.to_table].columns if c.name == body.to_column)
    if child_type != parent_type:
        raise RelationshipError(
            f"Column types differ ({child_type} vs {parent_type}); relationships need matching types.")

    for r in schema.relationships:
        if (r.from_table, r.from_columns, r.to_table, r.to_columns) == (
                body.from_table, [body.from_column], body.to_table, [body.to_column]):
            raise RelationshipError("This relationship already exists.", 409)

    with connector.engine() as engine, engine.connect() as conn:
        if not is_unique_key(engine, conn, body.to_table, body.to_column):
            raise RelationshipError(
                f"{body.to_table}.{body.to_column} must contain unique, non-null values "
                "to be the parent side of a relationship.")
        total, ratio = _match_ratio(
            engine, conn, (body.from_table, body.from_column), (body.to_table, body.to_column))
        if total == 0 or ratio == 0:
            raise RelationshipError(
                f"No values in {body.from_table}.{body.from_column} match {body.to_table}.{body.to_column}.")
        cardinality = ("one_to_one" if is_unique_key(engine, conn, body.from_table, body.from_column)
                       else "many_to_one")

    relationship_id = registry.add_user_relationship(
        datasource.id, body.from_table, body.from_column, body.to_table, body.to_column, cardinality)
    return Relationship(
        id=relationship_id,
        from_table=body.from_table, from_columns=[body.from_column],
        to_table=body.to_table, to_columns=[body.to_column],
        source="user", cardinality=cardinality,
    )


# -------------------------------------------------------- join planning

def _orient(rel: Relationship, from_table: str) -> Join:
    if rel.from_table == from_table:
        return Join(left_table=rel.from_table, left_columns=rel.from_columns,
                    right_table=rel.to_table, right_columns=rel.to_columns, relationship_id=rel.id)
    return Join(left_table=rel.to_table, left_columns=rel.to_columns,
                right_table=rel.from_table, right_columns=rel.from_columns, relationship_id=rel.id)


def _adjacency(relationships: list[Relationship]) -> dict[str, list[tuple[str, Relationship]]]:
    adj: dict[str, list[tuple[str, Relationship]]] = {}
    # Declared keys sort before user-confirmed ones so default choices are deterministic.
    for rel in sorted(relationships, key=lambda r: (r.source != "declared", r.id)):
        if rel.from_table == rel.to_table:
            continue  # self-references do not help connect different tables
        adj.setdefault(rel.from_table, []).append((rel.to_table, rel))
        adj.setdefault(rel.to_table, []).append((rel.from_table, rel))
    return adj


def _shortest_paths(adj, sources: set[str], target: str, max_hops: int) -> list[list[Join]]:
    """All shortest join paths from any table in `sources` to `target` (capped)."""
    dist = {s: 0 for s in sources}
    parents: dict[str, list[tuple[str, Relationship]]] = {}
    frontier = sorted(sources)
    depth = 0
    while frontier and target not in dist and depth < max_hops:
        nxt: list[str] = []
        for node in frontier:
            for neighbour, rel in adj.get(node, []):
                if neighbour not in dist:
                    dist[neighbour] = depth + 1
                    parents[neighbour] = [(node, rel)]
                    nxt.append(neighbour)
                elif dist[neighbour] == depth + 1:
                    parents[neighbour].append((node, rel))
        frontier, depth = nxt, depth + 1
    if target not in dist:
        return []

    def build(node: str) -> list[list[Join]]:
        if node in sources:
            return [[]]
        paths: list[list[Join]] = []
        for prev, rel in parents[node]:
            for prefix in build(prev):
                paths.append(prefix + [_orient(rel, prev)])
                if len(paths) >= MAX_PATHS_PER_TARGET:
                    return paths
        return paths

    return build(target)


def find_join_plan(schema: SchemaInfo, tables: list[str], max_hops: int = 4) -> JoinPlan:
    """Connect the requested tables using known relationships.

    The plan is "ambiguous" when a table can be reached in more than one equally short way
    (for example two foreign keys between the same pair of tables), and "disconnected" when
    no known relationship connects it. A default choice is still returned for ambiguous plans.
    """
    requested = list(dict.fromkeys(tables))
    known = {t.name for t in schema.tables}
    unknown = [t for t in requested if t not in known]
    if unknown:
        raise ValueError(f"Unknown table(s): {', '.join(unknown)}")

    adj = _adjacency(schema.relationships)
    in_plan = [requested[0]]
    joins: list[Join] = []
    ambiguities: list[JoinAmbiguity] = []
    remaining = requested[1:]
    unreachable: list[str] = []

    while remaining:
        best: tuple[str, list[list[Join]]] | None = None
        for target in remaining:
            paths = _shortest_paths(adj, set(in_plan), target, max_hops)
            if paths and (best is None or len(paths[0]) < len(best[1][0])):
                best = (target, paths)
        if best is None:
            unreachable = remaining
            break
        target, paths = best
        if len(paths) > 1:
            ambiguities.append(JoinAmbiguity(table=target, options=paths))
        for join in paths[0]:
            if join.right_table not in in_plan:
                in_plan.append(join.right_table)
        joins.extend(paths[0])
        remaining = [t for t in remaining if t not in in_plan]

    if unreachable:
        status, message = "disconnected", (
            f"No known relationship connects: {', '.join(unreachable)}. "
            "Confirm a relationship between the tables first.")
    elif ambiguities:
        status, message = "ambiguous", (
            "More than one join path exists for: " + ", ".join(a.table for a in ambiguities) + ".")
    else:
        status, message = "ok", None
    return JoinPlan(status=status, tables=in_plan, joins=joins, ambiguities=ambiguities,
                    unreachable=unreachable, message=message)
