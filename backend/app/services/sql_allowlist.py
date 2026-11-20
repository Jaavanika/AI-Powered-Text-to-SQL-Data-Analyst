"""What generated SQL is allowed to contain, per database dialect.

Allowing a construct is a security decision, so the sets are small and explicit. SQLGlot rewrites many
functions into its own node types (strftime becomes a "timetostr" node, string_agg a "groupconcat"
node), so allowed typed functions are not listed by hand: they are derived by parsing sample calls
of the functions we mean to allow. A function that is not in the samples cannot be derived, and
anything SQLGlot does not recognise is allowed only if its name is in the "anonymous" set.
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

# Non-function node types allowed in every dialect.
BASE_STRUCTURAL = {
    "select", "union", "except", "intersect", "with", "cte", "subquery", "from", "where", "group",
    "having", "order", "ordered", "limit", "offset", "join", "table", "tablealias", "alias",
    "column", "identifier", "star", "literal", "null", "boolean", "paren", "tuple", "distinct",
    "datatype", "datatypeparam", "window", "windowspec", "and", "or", "not", "xor", "neg",
    "eq", "neq", "gt", "gte", "lt", "lte", "add", "sub", "mul", "div", "mod", "dpipe", "like",
    "ilike", "glob", "escape", "is", "in", "between", "exists", "any", "all", "case", "if",
    "cast", "trycast", "bitwiseand", "bitwiseor", "bitwisenot", "bitwiseleftshift",
    "bitwiserightshift", "nullsafeeq", "nullsafeneq", "collate", "dot", "interval", "countif",
    "ignorenulls", "respectnulls", "filter", "dotdot", "kwarg", "var", "cube", "rollup",
}

# ------------------------------------------------------------------------- SQLite

SQLITE_SAMPLES = [
    "count(*)", "count(distinct x)", "sum(x)", "avg(x)", "min(x)", "max(x)", "min(x, y)", "max(x, y)",
    "abs(x)", "round(x, 2)", "coalesce(x, 0)", "ifnull(x, 0)", "nullif(x, 0)", "iif(x > 1, 1, 0)",
    "length(x)", "lower(x)", "upper(x)", "substr(x, 1, 2)", "substring(x, 1, 2)", "trim(x)",
    "ltrim(x)", "rtrim(x)", "replace(x, 'a', 'b')", "instr(x, 'a')", "strftime('%Y', x)",
    "date(x)", "datetime(x)", "time(x)", "julianday(x)", "group_concat(x, ',')",
    "row_number() over (partition by y order by x)", "rank() over (order by x)",
    "dense_rank() over (order by x)", "percent_rank() over (order by x)",
    "cume_dist() over (order by x)", "ntile(4) over (order by x)", "lag(x) over (order by y)",
    "lead(x) over (order by y)", "first_value(x) over (order by y)", "last_value(x) over (order by y)",
    "sum(x) over (partition by y)", "sum(x) over (order by y rows between unbounded preceding and current row)",
    "cast(x as integer)", "cast(x as real)", "cast(x as text)", "sign(x)", "ceil(x)", "floor(x)",
    "sqrt(x)", "power(x, 2)", "printf('%d', x)", "concat(x, y)", "x || y", "x like 'a%'",
    "x glob 'a*'", "x between 1 and 2", "x in (1, 2)", "x is null", "not x", "-x", "x % 2",
    "case when x then 1 else 2 end", "case x when 1 then 2 end", "exists (select 1)",
]

SQLITE_ANONYMOUS = {
    "total", "ifnull", "iif", "instr", "julianday", "unixepoch", "printf", "format", "concat_ws",
    "ltrim", "rtrim", "sign", "ceil", "ceiling", "floor", "sqrt", "power", "pow", "ln", "log",
    "log10", "exp", "mod", "hex", "char", "unicode", "typeof", "time", "datetime", "date", "strftime",
    "group_concat", "percent_rank", "cume_dist", "ntile", "nth_value", "likely", "unlikely",
    "trunc", "sin", "cos", "tan", "pi", "radians", "degrees", "coalesce", "nullif", "abs", "round",
    "length", "lower", "upper", "substr", "substring", "replace", "trim", "min", "max", "count",
    "sum", "avg", "lag", "lead", "first_value", "last_value", "row_number", "rank", "dense_rank",
}

# ---------------------------------------------------------------------- PostgreSQL

POSTGRES_SAMPLES = [
    # aggregates and math
    "count(*)", "count(distinct x)", "sum(x)", "avg(x)", "min(x)", "max(x)", "abs(x)", "round(x, 2)",
    "round(x::numeric, 2)", "ceil(x)", "floor(x)", "trunc(x)", "mod(x, 2)", "power(x, 2)", "sqrt(x)",
    "ln(x)", "log(x)", "exp(x)", "sign(x)", "coalesce(x, 0)", "nullif(x, 0)", "greatest(x, y)", "least(x, y)",
    "stddev(x)", "variance(x)", "bool_and(x)", "bool_or(x)", "string_agg(x, ',')",
    "string_agg(x, ',' order by y)", "array_agg(x)", "count(*) filter (where x > 1)",
    "percentile_cont(0.5) within group (order by x)", "percentile_disc(0.5) within group (order by x)",
    # text
    "length(x)", "char_length(x)", "lower(x)", "upper(x)", "initcap(x)", "trim(x)", "ltrim(x)", "rtrim(x)",
    "btrim(x)", "substr(x, 1, 2)", "substring(x, 1, 2)", "substring(x from 1 for 2)", "replace(x, 'a', 'b')",
    "left(x, 2)", "right(x, 2)", "split_part(x, ',', 1)", "concat(x, y)", "concat_ws(',', x, y)",
    "lpad(x, 5, '0')", "rpad(x, 5, '0')", "strpos(x, 'a')", "position('a' in x)", "regexp_replace(x, 'a', 'b')",
    "x || y", "x like 'a%'", "x ilike 'a%'", "x similar to 'a%'", "x ~ 'a'",
    # dates and casts
    "date_trunc('month', x)", "extract(year from x)", "date_part('year', x)", "to_char(x, 'YYYY-MM')",
    "to_date(x, 'YYYY-MM-DD')", "to_timestamp(x)", "age(x, y)", "make_date(2025, 1, 1)", "now()",
    "current_date", "current_timestamp", "x + interval '1 day'", "x::date", "x::numeric", "x::text",
    "cast(x as integer)",
    # windows
    "row_number() over (partition by y order by x)", "rank() over (order by x)", "dense_rank() over (order by x)",
    "percent_rank() over (order by x)", "cume_dist() over (order by x)", "ntile(4) over (order by x)",
    "lag(x) over (order by y)", "lead(x) over (order by y)", "first_value(x) over (order by y)",
    "last_value(x) over (order by y)", "sum(x) over (partition by y)",
    "sum(x) over (order by y rows between unbounded preceding and current row)",
    # operators and JSON access
    "x->>'k'", "x->'k'", "case when x then 1 else 2 end", "case x when 1 then 2 end", "x between 1 and 2",
    "x in (1, 2)", "x = any(array[1, 2])", "x is null", "x is distinct from y", "not x", "-x", "x % 2",
    "exists (select 1)",
]

POSTGRES_ANONYMOUS = {
    "count", "sum", "avg", "min", "max", "abs", "round", "ceil", "ceiling", "floor", "trunc", "mod", "power",
    "sqrt", "ln", "log", "exp", "sign", "coalesce", "nullif", "greatest", "least", "stddev", "stddev_samp",
    "stddev_pop", "variance", "var_samp", "var_pop", "bool_and", "bool_or", "string_agg", "array_agg",
    "percentile_cont", "percentile_disc", "mode", "length", "char_length", "character_length", "lower", "upper",
    "initcap", "trim", "ltrim", "rtrim", "btrim", "substr", "substring", "replace", "left", "right",
    "split_part", "concat", "concat_ws", "lpad", "rpad", "strpos", "regexp_replace", "date_trunc", "date_part",
    "to_char", "to_date", "to_timestamp", "age", "make_date", "now", "row_number", "rank", "dense_rank",
    "percent_rank", "cume_dist", "ntile", "lag", "lead", "first_value", "last_value", "nth_value",
}


@dataclass(frozen=True)
class Rules:
    function_keys: frozenset[str]    # typed function nodes that may appear
    anonymous: frozenset[str]        # function names (lowercase) SQLGlot leaves untyped
    structural: frozenset[str]       # other node types that may appear


def _derive(samples: list[str], dialect: str) -> tuple[set[str], set[str]]:
    """Node keys that appear when parsing the samples: (typed function keys, other keys)."""
    functions: set[str] = set()
    other: set[str] = set()
    for sample in samples:
        tree = sqlglot.parse_one(f"SELECT {sample} FROM t", read=dialect)
        for node in tree.walk():
            if isinstance(node, exp.Func):
                if not isinstance(node, exp.Anonymous):
                    functions.add(node.key)
            else:
                other.add(node.key)
    return functions, other


def _build() -> dict[str, Rules]:
    sqlite_functions, _ = _derive(SQLITE_SAMPLES, "sqlite")
    postgres_functions, postgres_other = _derive(POSTGRES_SAMPLES, "postgres")
    return {
        "sqlite": Rules(frozenset(sqlite_functions), frozenset(SQLITE_ANONYMOUS), frozenset(BASE_STRUCTURAL)),
        "postgres": Rules(frozenset(postgres_functions), frozenset(POSTGRES_ANONYMOUS),
                          frozenset(BASE_STRUCTURAL | postgres_other)),
    }


_RULES = _build()


def rules_for(dialect: str) -> Rules | None:
    return _RULES.get(dialect)
