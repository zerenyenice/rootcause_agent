"""Allow lists for function calls: typed sqlglot classes by family, plus per-dialect names.

Membership is by exact class, so a subclass added by a future sqlglot is rejected until
it is listed here. Unknown (``exp.Anonymous``) functions are allowed only by lowercase
name for the dialect in ``ALLOWED_ANONYMOUS``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Final

from sqlglot import exp

# Each group below is a family of read-only, side-effect-free functions. Membership is
# by exact class: a subclass sqlglot adds later is rejected until it is listed here.
_LOGIC_AND_CONVERSION: Final[tuple[type[exp.Expr], ...]] = (
    exp.And,
    exp.Or,
    exp.Xor,
    exp.Exists,
    exp.If,
    exp.Case,
    exp.Coalesce,
    exp.Nullif,
    exp.Nvl2,
    exp.Greatest,
    exp.Least,
    exp.Decode,
    exp.DecodeCase,
    exp.IsNan,
    exp.IsInf,
    exp.Cast,
    exp.TryCast,
    exp.Convert,
    exp.Collate,
    exp.ToNumber,
    exp.ToChar,
    exp.ToDouble,
    exp.ToBoolean,
    exp.String,
    exp.Float64,
    exp.Int64,
    exp.Date,
    exp.Datetime,
    exp.Time,
    exp.Timestamp,
    exp.Typeof,
)
_AGGREGATES: Final[tuple[type[exp.Expr], ...]] = (
    exp.AnyValue,
    exp.ApproxDistinct,
    exp.ApproxQuantile,
    exp.ApproxQuantiles,
    exp.ArgMax,
    exp.ArgMin,
    exp.ArrayAgg,
    exp.ArrayConcatAgg,
    exp.ArrayUnionAgg,
    exp.ArrayUniqueAgg,
    exp.Avg,
    exp.BitwiseAndAgg,
    exp.BitwiseOrAgg,
    exp.BitwiseXorAgg,
    exp.Corr,
    exp.Count,
    exp.CountIf,
    exp.CovarPop,
    exp.CovarSamp,
    exp.First,
    exp.GroupConcat,
    exp.Grouping,
    exp.GroupingId,
    exp.JSONArrayAgg,
    exp.JSONBObjectAgg,
    exp.JSONObjectAgg,
    exp.Kurtosis,
    exp.Last,
    exp.LogicalAnd,
    exp.LogicalOr,
    exp.Max,
    exp.Median,
    exp.Min,
    exp.Mode,
    exp.ObjectAgg,
    exp.PercentileCont,
    exp.PercentileDisc,
    exp.Quantile,
    exp.Skewness,
    exp.Stddev,
    exp.StddevPop,
    exp.StddevSamp,
    exp.Sum,
    exp.Variance,
    exp.VariancePop,
)
_WINDOW: Final[tuple[type[exp.Expr], ...]] = (
    exp.CumeDist,
    exp.DenseRank,
    exp.FirstValue,
    exp.Lag,
    exp.LastValue,
    exp.Lead,
    exp.NthValue,
    exp.Ntile,
    exp.PercentRank,
    exp.Rank,
    exp.RowNumber,
)
_STRING: Final[tuple[type[exp.Expr], ...]] = (
    exp.Ascii,
    exp.BitLength,
    exp.Chr,
    exp.Concat,
    exp.ConcatWs,
    exp.Contains,
    exp.EndsWith,
    exp.Encode,
    exp.Format,
    exp.Hex,
    exp.Initcap,
    exp.Left,
    exp.Length,
    exp.Levenshtein,
    exp.Lower,
    exp.MD5,
    exp.Overlay,
    exp.Pad,
    exp.RegexpExtract,
    exp.RegexpExtractAll,
    exp.RegexpILike,
    exp.RegexpLike,
    exp.RegexpReplace,
    exp.RegexpSplit,
    exp.Repeat,
    exp.Replace,
    exp.Reverse,
    exp.Right,
    exp.SHA,
    exp.SHA2,
    exp.Soundex,
    exp.Space,
    exp.Split,
    exp.SplitPart,
    exp.StartsWith,
    exp.StrPosition,
    exp.Substring,
    exp.SubstringIndex,
    exp.Translate,
    exp.Trim,
    exp.Unhex,
    exp.Upper,
    exp.Base64Encode,
    exp.Base64DecodeString,
    exp.Base64DecodeBinary,
)
_DATE_TIME: Final[tuple[type[exp.Expr], ...]] = (
    exp.AddMonths,
    exp.ConvertTimezone,
    exp.CurrentDate,
    exp.CurrentDatetime,
    exp.CurrentTime,
    exp.CurrentTimestamp,
    exp.DateAdd,
    exp.DateBin,
    exp.DateDiff,
    exp.DateFromParts,
    exp.DateFromUnixDate,
    exp.DateStrToDate,
    exp.DateSub,
    exp.DateToDateStr,
    exp.DateToDi,
    exp.DateTrunc,
    exp.DatetimeAdd,
    exp.DatetimeDiff,
    exp.DatetimeSub,
    exp.DatetimeTrunc,
    exp.Day,
    exp.DayOfMonth,
    exp.DayOfWeek,
    exp.DayOfWeekIso,
    exp.DayOfYear,
    exp.Dayname,
    exp.DiToDate,
    exp.Extract,
    exp.Hour,
    exp.JustifyDays,
    exp.JustifyHours,
    exp.JustifyInterval,
    exp.LastDay,
    exp.Localtime,
    exp.Localtimestamp,
    exp.MakeInterval,
    exp.Minute,
    exp.Month,
    exp.Monthname,
    exp.MonthsBetween,
    exp.NextDay,
    exp.ParseDatetime,
    exp.ParseTime,
    exp.PreviousDay,
    exp.Quarter,
    exp.Second,
    exp.StrToDate,
    exp.StrToTime,
    exp.StrToUnix,
    exp.TimeAdd,
    exp.TimeDiff,
    exp.TimeFromParts,
    exp.TimeStrToDate,
    exp.TimeStrToTime,
    exp.TimeStrToUnix,
    exp.TimeSub,
    exp.TimeToStr,
    exp.TimeToTimeStr,
    exp.TimeToUnix,
    exp.TimeTrunc,
    exp.TimestampAdd,
    exp.TimestampDiff,
    exp.TimestampFromParts,
    exp.TimestampSub,
    exp.TimestampTrunc,
    exp.ToDays,
    exp.TsOrDiToDi,
    exp.TsOrDsAdd,
    exp.TsOrDsDiff,
    exp.TsOrDsToDate,
    exp.TsOrDsToDateStr,
    exp.TsOrDsToDatetime,
    exp.TsOrDsToTime,
    exp.TsOrDsToTimestamp,
    exp.UnixToStr,
    exp.UnixToTime,
    exp.UnixToTimeStr,
    exp.Week,
    exp.WeekOfYear,
    exp.Year,
    exp.YearOfWeek,
)
_MATH: Final[tuple[type[exp.Expr], ...]] = (
    exp.Abs,
    exp.Acos,
    exp.Acosh,
    exp.Asin,
    exp.Asinh,
    exp.Atan,
    exp.Atan2,
    exp.Atanh,
    exp.Cbrt,
    exp.Ceil,
    exp.Cos,
    exp.Cosh,
    exp.Cot,
    exp.Coth,
    exp.Degrees,
    exp.Exp,
    exp.Factorial,
    exp.Floor,
    exp.Ln,
    exp.Log,
    exp.Negative,
    exp.Pi,
    exp.Pow,
    exp.Radians,
    exp.Rand,
    exp.Rint,
    exp.Round,
    exp.SafeAdd,
    exp.SafeDivide,
    exp.SafeMultiply,
    exp.SafeSubtract,
    exp.Sign,
    exp.Sin,
    exp.Sinh,
    exp.Sqrt,
    exp.Tan,
    exp.Tanh,
    exp.Trunc,
    exp.WidthBucket,
)
_JSON: Final[tuple[type[exp.Expr], ...]] = (
    exp.JSONArray,
    exp.JSONArrayContains,
    exp.JSONBContains,
    exp.JSONBExists,
    exp.JSONBExtract,
    exp.JSONBExtractScalar,
    exp.JSONExists,
    exp.JSONExtract,
    exp.JSONExtractArray,
    exp.JSONExtractScalar,
    exp.JSONFormat,
    exp.JSONKeys,
    exp.JSONObject,
    exp.JSONType,
    exp.JSONValueArray,
    exp.ParseJSON,
)
_ARRAY_STRUCT_MAP: Final[tuple[type[exp.Expr], ...]] = (
    exp.Array,
    exp.ArrayAll,
    exp.ArrayAny,
    exp.ArrayAppend,
    exp.ArrayCompact,
    exp.ArrayConcat,
    exp.ArrayContainedBy,
    exp.ArrayContains,
    exp.ArrayContainsAll,
    exp.ArrayDistinct,
    exp.ArrayExcept,
    exp.ArrayFilter,
    exp.ArrayFirst,
    exp.ArrayIntersect,
    exp.ArrayLast,
    exp.ArrayMax,
    exp.ArrayMin,
    exp.ArrayOverlaps,
    exp.ArrayPosition,
    exp.ArrayPrepend,
    exp.ArrayRemove,
    exp.ArrayReverse,
    exp.ArraySize,
    exp.ArraySlice,
    exp.ArraySort,
    exp.ArraySum,
    exp.ArrayToString,
    exp.ArraysZip,
    exp.Flatten,
    exp.GetExtract,
    exp.List,
    exp.Map,
    exp.MapContainsKey,
    exp.MapFromEntries,
    exp.MapKeys,
    exp.MapSize,
    exp.Reduce,
    exp.StringToArray,
    exp.Struct,
    exp.StructExtract,
    exp.Transform,
)
#: Row generators. Usable in table position as well (see ``TABLE_FUNCTION_TYPES``).
_SERIES_AND_UNNEST: Final[tuple[type[exp.Expr], ...]] = (
    exp.Explode,
    exp.ExplodingGenerateSeries,
    exp.GenerateDateArray,
    exp.GenerateSeries,
    exp.GenerateTimestampArray,
    exp.Posexplode,
    exp.Unnest,
)

ALLOWED_FUNCTION_TYPES: Final[tuple[type[exp.Expr], ...]] = (
    *_LOGIC_AND_CONVERSION,
    *_AGGREGATES,
    *_WINDOW,
    *_STRING,
    *_DATE_TIME,
    *_MATH,
    *_JSON,
    *_ARRAY_STRUCT_MAP,
    *_SERIES_AND_UNNEST,
)
ALLOWED_TYPE_SET: Final[frozenset[type[exp.Expr]]] = frozenset(ALLOWED_FUNCTION_TYPES)

#: Typed classes sqlglot produces for generate_series / unnest / range.
TABLE_FUNCTION_TYPES: Final[frozenset[type[exp.Expr]]] = frozenset(
    {exp.Unnest, exp.Explode, exp.GenerateSeries, exp.ExplodingGenerateSeries}
)
TABLE_FUNCTION_NAMES: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {"duckdb": frozenset({"generate_series", "unnest", "range"})}
)
DEFAULT_TABLE_FUNCTION_NAMES: Final[frozenset[str]] = frozenset({"generate_series", "unnest"})

#: Lower-cased names of functions sqlglot has no typed class for, per dialect. Keep small.
ALLOWED_ANONYMOUS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "mysql": frozenset({"now"}),
        "postgres": frozenset(
            {"string_agg", "percentile_cont", "percentile_disc", "date_part", "age", "cardinality"}
        ),
        "duckdb": frozenset(
            {"date_part", "list_aggregate", "strftime", "strptime", "epoch", "now"}
        ),
    }
)


# --- DuckDB: broad allow, explicit deny -------------------------------------------------
#
# DuckDB ships hundreds of pure scalar/aggregate/window functions (``date_sub``,
# ``regr_slope``, ``histogram``, ``list_*`` ...). The connection itself is locked down
# (read-only file, no external access, no extensions, no spill; ``duckdb_lockdown``), so
# the validator is defence in depth there: instead of enumerating every safe function it
# denies the ones that can read files/URLs, reach system state, write, sleep, run nested
# SQL, or generate rows, and allows the rest. Other dialects keep the strict allow lists.

#: Lower-cased DuckDB function names that are never allowed (anonymous calls).
DUCKDB_DENIED_FUNCTION_NAMES: Final[frozenset[str]] = frozenset(
    {
        # run nested SQL / read tables by name (would bypass the table checks)
        "query",
        "query_table",
        "json_deserialize_sql",
        "json_serialize_sql",
        "json_serialize_plan",
        # files, environment, configuration, variables, extensions
        "glob",
        "getenv",
        "current_setting",
        "getvariable",
        "load_extension",
        "install_extension",
        "which_secret",
        "write_log",
        "parse_duckdb_log_message",
        "hive_partition_component",
        # state-changing or blocking scalars
        "nextval",
        "currval",
        "setval",
        "setseed",
        "checkpoint",
        "force_checkpoint",
        "txid_current",
        # row generators (also refused by the executor's generator guard)
        "range",
        "generate_series",
        "generate_subscripts",
        "repeat",
        "unnest",
        "unpivot_list",
    }
)
#: Prefixes of function families that reach outside the catalog's tables.
DUCKDB_DENIED_FUNCTION_PREFIXES: Final[tuple[str, ...]] = (
    "read_",
    "write_",
    "copy_",
    "sniff_",
    "parquet_",
    "iceberg_",
    "delta_",
    "duckdb_",
    "pragma_",
    "sqlite_",
    "pg_",
    "st_read",
    "sleep",
    "load_",
    "install_",
    "export_",
    "import_",
    "attach",
    "detach",
)
#: Typed sqlglot function classes that read files, call services, expand ``COLUMNS(...)``
#: or generate rows. Every other typed function is allowed for DuckDB.
_DUCKDB_DENIED_TYPE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "ReadCSV",
        "ReadParquet",
        "ToFile",
        "Secret",
        "JSONTable",
        "OpenJSON",
        "XMLTable",
        "GenerateTable",
        "Generator",
        "Inline",
        "Stack",
        "SplitToTable",
        "GapFill",
        "Columns",  # COLUMNS('regex') expands to columns the selection check cannot see
        "Seq1",
        "Seq2",
        "Seq4",
        "Seq8",
        "Normal",
        "Uniform",
        "Zipf",
        "Randstr",
        "GenerateBool",
        "GenerateDouble",
        "GenerateInt",
        "GenerateText",
        "GenerateEmbedding",
        "VectorSearch",
        "FeaturesAtTime",
        "Search",
        "SearchIp",
        "Predict",
        "Host",
        "NextValueFor",
        "Repeat",
    }
)
_DUCKDB_DENIED_TYPE_PREFIXES: Final[tuple[str, ...]] = ("AI", "ML")


def _function_subclasses(base: type[exp.Expr]) -> Iterator[type[exp.Expr]]:
    for subclass in base.__subclasses__():
        yield subclass
        yield from _function_subclasses(subclass)


def _duckdb_allows_type(cls: type[exp.Expr]) -> bool:
    name = cls.__name__
    if name in _DUCKDB_DENIED_TYPE_NAMES or name.startswith(_DUCKDB_DENIED_TYPE_PREFIXES):
        return False
    return cls not in (exp.Anonymous, exp.AnonymousAggFunc)


DUCKDB_TYPE_SET: Final[frozenset[type[exp.Expr]]] = (
    frozenset(cls for cls in _function_subclasses(exp.Func) if _duckdb_allows_type(cls))
    | ALLOWED_TYPE_SET
)


def duckdb_function_name_allowed(name: str) -> bool:
    """Whether the (unknown to sqlglot) DuckDB function ``name`` may be called."""
    lowered = name.lower()
    return lowered not in DUCKDB_DENIED_FUNCTION_NAMES and not lowered.startswith(
        DUCKDB_DENIED_FUNCTION_PREFIXES
    )
