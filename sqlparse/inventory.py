#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""Collect table and column references from SQL statements.

The module exposes a single public entry point, :func:`collect`, which
takes a SQL string and returns one :class:`StatementInventory` per
parsed statement (in statement order, matching :func:`sqlparse.split`).

Table name normalization rules (see :class:`TableRef`):

* qualified names are split into segments instead of being joined into
  a single string (``schema.table`` -> ``['schema', 'table']``);
* every segment is lower-cased;
* backtick, double-quote and square-bracket quoting is stripped, the
  quoted content itself is kept unchanged;
* an alias is recorded separately and never mixed into the table name.

Names introduced by ``WITH`` clauses are common table expressions, not
physical tables: they never appear in the table inventory.  References
to CTE names inside the statement (including nested CTEs that reference
earlier ones) are resolved against the enclosing CTE scope and only the
underlying physical tables are counted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlparse import parse, sql
from sqlparse import tokens as T

__all__ = ["ColumnRef", "StatementInventory", "TableRef", "collect"]

# ttypes that carry a name (possibly with surrounding quote characters).
_NAME_TYPES = (T.Name, T.String.Symbol)

# Keyword (normalized, upper-case) values that start a table-bearing
# clause when they appear at statement / subquery level.
_FROM_KEYWORDS = (
    "FROM",
    "JOIN",
    "INNER JOIN",
    "LEFT JOIN",
    "RIGHT JOIN",
    "FULL JOIN",
    "LEFT OUTER JOIN",
    "RIGHT OUTER JOIN",
    "FULL OUTER JOIN",
    "CROSS JOIN",
    "NATURAL JOIN",
    "USING",
)

# Clauses whose following identifiers are statement targets rather than
# read sources.
_TARGET_KEYWORDS = ("INTO", "UPDATE")

# Keywords that end a FROM/JOIN region at the current query level.
_REGION_END_KEYWORDS = (
    "WHERE",
    "GROUP BY",
    "HAVING",
    "ORDER BY",
    "LIMIT",
    "UNION",
    "UNION ALL",
    "EXCEPT",
    "INTERSECT",
    "ON",
    "WHEN",
    "THEN",
    "ELSE",
    "END",
    "SET",
    "RETURNING",
    "VALUES",
)


def _normalize_segment(value: str) -> str:
    """Normalize one name segment.

    Backtick, double-quote and square-bracket wrapping is removed while
    the quoted content is kept unchanged; unquoted names are
    lower-cased.
    """
    if len(value) >= 2 and (
        (value[0] == "`" and value[-1] == "`")
        or (value[0] == '"' and value[-1] == '"')
        or (value[0] == "[" and value[-1] == "]")
    ):
        return value[1:-1]
    return value.lower()


def _identifier_segments(token: sql.TokenList) -> tuple[str, ...]:
    """Return the normalized name segments of an identifier.

    Only leaf name tokens contribute, so function parentheses, typecasts
    and similar sub-groups are ignored.
    """
    segments = []
    for leaf in token.flatten():
        if leaf.ttype in _NAME_TYPES:
            segments.append(_normalize_segment(leaf.value))
    return tuple(segments)


def _identifier_alias(token: sql.Identifier) -> str | None:
    """Return the normalized alias of an identifier, if any."""
    alias = token.get_alias()
    if alias is None:
        return None
    return _normalize_segment(alias)


class _Collector:
    """Collects references of one statement."""

    def __init__(self, statement: sql.Statement):
        self.statement = statement
        self.tables: list[TableRef] = []
        self._table_index: dict[tuple[str, ...], TableRef] = {}
        self.columns: list[ColumnRef] = []
        self._column_keys: set[tuple[str, ...]] = set()

    def collect(self) -> StatementInventory:
        self._walk(self.statement, cte_scope=())
        return StatementInventory(
            type=self.statement.get_type(),
            tables=self.tables,
            columns=self.columns,
        )

    # -- token traversal -------------------------------------------------

    def _walk(self, token_list: sql.TokenList, cte_scope: tuple[str, ...]):
        # Traverse the token tree without collecting anything yet;
        # later steps add clause-aware table/column extraction here.
        for token in token_list.tokens:
            if token.is_group:
                self._walk(token, cte_scope)

    # -- reference recording ---------------------------------------------

    def _record_table(self, segments, alias, mode):
        raise NotImplementedError

    def _record_column(self, identifier: sql.Identifier):
        raise NotImplementedError


@dataclass
class ColumnRef:
    """A column reference inside a statement.

    Attributes:
        name: normalized column name (lower-cased, quoting stripped;
            for qualified columns such as ``t.col`` only the column
            part after the last dot is kept).
        qualifier: qualifier prefix of a qualified column (e.g. the
            table alias ``t`` of ``t.col``), normalized the same way,
            or ``None`` for unqualified columns.
    """

    name: str
    qualifier: str | None = None


@dataclass
class TableRef:
    """A physical table reference inside a statement.

    Attributes:
        name: normalized name segments of the table, from the outermost
            qualifier to the table name (e.g. ``['schema', 'tbl']`` for
            ``schema.tbl``).
        alias: alias under which the table is referenced in this
            occurrence, normalized like a name segment, or ``None``.
        mode: ``'read'`` for references in query clauses (FROM/JOIN/
            USING/...) or ``'write'`` for statement targets
            (INSERT INTO / UPDATE / DELETE FROM / MERGE INTO / ...).
        count: number of occurrences of the table in the statement.
    """

    name: tuple[str, ...]
    alias: str | None = None
    mode: str = "read"
    count: int = 0

    @property
    def key(self) -> tuple[str, ...]:
        """Merge key: normalized table name without alias or mode."""
        return self.name


@dataclass
class StatementInventory:
    """Reference inventory of a single SQL statement.

    Attributes:
        type: statement type as returned by
            :meth:`sqlparse.sql.Statement.get_type`.
        tables: table references, ordered by first occurrence.
        columns: column references, ordered by first occurrence and
            de-duplicated.
    """

    type: str
    tables: list[TableRef] = field(default_factory=list)
    columns: list[ColumnRef] = field(default_factory=list)


def collect(sql_text: str) -> list[StatementInventory]:
    """Collect table and column references from *sql_text*.

    :param sql_text: A string containing one or more SQL statements.
    :returns: A list of :class:`StatementInventory` instances, one per
        statement, in the same order as :func:`sqlparse.split`.
    """
    return [_Collector(statement).collect() for statement in parse(sql_text)]
