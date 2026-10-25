"""Class-level allow list for syntax-tree nodes.

Every node of a parsed statement must be an instance of exactly one of the classes named
in ``ALLOWED_NODE_CLASSES`` (query shapes, clauses, operators, literals, names) or an
``exp.Func`` subclass, which the function allow list in ``allowlist`` judges separately.
Anything else, including every statement, control-flow, stage, hint and command class
sqlglot has or adds later, is rejected as not read-only. Classes are listed by name and
resolved at import, so a sqlglot upgrade that renames one fails loudly instead of
silently shrinking the list.
"""

from __future__ import annotations

from typing import Final

from sqlglot import exp

_ALLOWED_NAMES: Final[tuple[str, ...]] = (
    # query shapes
    "Select",
    "Union",
    "Intersect",
    "Except",
    "Subquery",
    "CTE",
    "With",
    "Values",
    "Lateral",
    "TableFromRows",
    "TableSample",
    # clauses
    "From",
    "Join",
    "Where",
    "PreWhere",
    "Group",
    "Having",
    "Order",
    "Ordered",
    "Limit",
    "LimitOptions",
    "Offset",
    "Fetch",
    "Qualify",
    "Window",
    "WindowSpec",
    "Distinct",
    "Rollup",
    "Cube",
    "GroupingSets",
    "Filter",
    "WithinGroup",
    "IgnoreNulls",
    "RespectNulls",
    "Final",
    # names and values
    "Tuple",
    "Paren",
    "Alias",
    "TableAlias",
    "Star",
    "Column",
    "Identifier",
    "Table",
    "Dot",
    "Var",
    "Literal",
    "Null",
    "Boolean",
    "BitString",
    "HexString",
    "ByteString",
    "RawString",
    "National",
    "Heredoc",
    "Bracket",
    "Slice",
    "Interval",
    "IntervalSpan",
    "DataType",
    "DataTypeParam",
    "ColumnDef",
    "Kwarg",
    "PropertyEQ",
    "Lambda",
    "AtTimeZone",
    "JSONPath",
    "JSONPathRoot",
    "JSONPathKey",
    "JSONPathSubscript",
    "JSONPathWildcard",
    "JSONPathSlice",
    "JSONPathUnion",
    "JSONPathRecursive",
    # operators and predicates
    "Not",
    "Neg",
    "EQ",
    "NEQ",
    "GT",
    "GTE",
    "LT",
    "LTE",
    "NullSafeEQ",
    "NullSafeNEQ",
    "Is",
    "In",
    "Between",
    "Like",
    "ILike",
    "SimilarTo",
    "Escape",
    "Any",
    "All",
    "Add",
    "Sub",
    "Mul",
    "Div",
    "IntDiv",
    "Mod",
    "DPipe",
    "BitwiseAnd",
    "BitwiseOr",
    "BitwiseXor",
    "BitwiseNot",
    "BitwiseLeftShift",
    "BitwiseRightShift",
)

#: Classes that are only meaningful as part of a ``DataType`` (struct fields, parameters).
_DATATYPE_ONLY_NAMES: Final[tuple[str, ...]] = ("ColumnDef", "DataTypeParam")

_VARIABLE_PREFIXES: Final[tuple[str, ...]] = ("@", "$")


def _resolve(names: tuple[str, ...]) -> frozenset[type[exp.Expr]]:
    """Map class names to sqlglot classes, failing at import if any is missing."""
    resolved: set[type[exp.Expr]] = set()
    missing: list[str] = []
    for name in names:
        candidate = getattr(exp, name, None)
        if isinstance(candidate, type) and issubclass(candidate, exp.Expr):
            resolved.add(candidate)
        else:
            missing.append(name)
    if missing:
        raise ImportError(f"sqlglot.exp has no expression class named: {', '.join(missing)}")
    return frozenset(resolved)


ALLOWED_NODE_CLASSES: Final[frozenset[type[exp.Expr]]] = _resolve(_ALLOWED_NAMES)
_DATATYPE_ONLY: Final[frozenset[type[exp.Expr]]] = _resolve(_DATATYPE_ONLY_NAMES)


def node_description(node: exp.Expr) -> str | None:
    """Describe why ``node`` is not permitted, or return ``None`` when it is acceptable.

    ``exp.Func`` subclasses return ``None`` here: they are judged by the function allow
    list, which also covers their arguments because the whole tree is walked.
    """
    if isinstance(node, exp.Func):
        return None
    node_type = type(node)
    if _is_replaced_limit_operand(node):
        return None
    if node_type not in ALLOWED_NODE_CLASSES:
        return f"Syntax node {node_type.__name__.upper()}"
    if node_type in _DATATYPE_ONLY and not isinstance(node.parent, exp.DataType):
        return f"Syntax node {node_type.__name__.upper()} outside a data type"
    if isinstance(node, exp.DataType):
        return _data_type_description(node)
    if isinstance(node, exp.Var) and node.name.startswith(_VARIABLE_PREFIXES):
        return "Variable or stage reference"
    return None


def _data_type_description(node: exp.DataType) -> str | None:
    """Only built-in types: no user-defined, schema-qualified or unknown type names."""
    kind = node.this
    if not isinstance(kind, exp.DataType.Type) or kind is exp.DataType.Type.USERDEFINED:
        return "Cast to a user-defined or schema-qualified type"
    if node.args.get("kind") is not None:
        return "Cast to a user-defined or schema-qualified type"
    return None


def _is_replaced_limit_operand(node: exp.Expr) -> bool:
    """A placeholder as the outermost ``LIMIT`` operand: ``apply_limit`` replaces it."""
    if not isinstance(node, (exp.Placeholder, exp.Parameter)):
        return False
    limit = node.parent
    if not isinstance(limit, exp.Limit) or limit.expression is not node:
        return False
    root = limit.parent
    return root is not None and root.parent is None and root.args.get("limit") is limit
