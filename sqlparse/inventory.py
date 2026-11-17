#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""Collect table and column references from SQL statements.

The module exposes a single public entry point, :func:`collect`, which
takes a SQL string and returns one :class:`StatementInventory` per
parsed statement, in statement order and therefore matching
:func:`sqlparse.split`.

Table names are normalized as follows:

* qualified names are split into separate segments instead of being
  joined into one string (``schema.table`` becomes ``('schema', 'table')``);
* unquoted segments are lower-cased;
* backtick, double-quote and square-bracket quoting is stripped while the
  quoted content itself is kept unchanged;
* an alias is recorded separately and never mixed into the table name.

Names defined by a ``WITH`` clause are common table expressions, not
physical tables.  They never appear in the table inventory; references
to them resolve to the physical tables used by their definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlparse import sql
from sqlparse import tokens as T

__all__ = ["ColumnRef", "StatementInventory", "TableRef", "collect"]

# Leaf token types that carry a (possibly quoted) name.
_NAME_TYPES = (T.Name, T.String.Symbol)

# Keyword values (normalized) that introduce read-source table lists.
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

# Keywords whose following identifiers are statement targets.
_TARGET_KEYWORDS = ("INTO", "UPDATE")

# Keywords that end a table-bearing region at the current query level.
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
    "WINDOW",
)


def _normalize_segment(value: str) -> str:
    """Normalize one name segment.

    Backtick, double-quote and square-bracket wrapping is removed while
    the wrapped content is kept unchanged; unquoted names are
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

    Only the name part before an alias (an ``AS`` keyword or a nested
    identifier/function) contributes, and only leaf name tokens are
    considered, so parentheses, typecasts and similar groups are
    ignored.
    """
    segments = []
    for child in token.tokens:
        if child.match(T.Keyword, "AS"):
            break
        if isinstance(child, (sql.Identifier, sql.Function)):
            # Everything from here on is an alias or a column list.  A
            # function still carries its own name in a nested
            # identifier (e.g. a CTE with an explicit column list).
            if isinstance(child, sql.Function):
                inner = child.token_next_by(i=sql.Identifier)[1]
                if inner is not None:
                    segments.extend(_identifier_segments(inner))
            break
        if not child.is_group and child.ttype in _NAME_TYPES:
            segments.append(_normalize_segment(child.value))
    return tuple(segments)


def _identifier_alias(token) -> str | None:
    """Return the normalized alias of an identifier, if any."""
    alias = token.get_alias()
    return None if alias is None else _normalize_segment(alias)


@dataclass
class ColumnRef:
    """A column reference inside a statement.

    Attributes:
        name: normalized column name.
        qualifier: qualifier prefix of a qualified column (e.g. the
            alias ``t`` of ``t.col``), or ``None`` for unqualified
            columns.
    """

    name: str
    qualifier: str | None = None


@dataclass
class TableRef:
    """A physical table reference inside a statement.

    Attributes:
        name: normalized name segments, outermost qualifier first.
        alias: alias used for this table, or ``None``.
        mode: ``'read'`` for query sources, ``'write'`` for targets.
        count: number of occurrences in the statement.
    """

    name: tuple[str, ...]
    alias: str | None = None
    mode: str = "read"
    count: int = 0

    @property
    def key(self) -> tuple[str, ...]:
        """Merge key: the normalized table name."""
        return self.name


@dataclass
class StatementInventory:
    """Reference inventory of a single statement.

    Attributes:
        type: statement type as returned by
            :meth:`sqlparse.sql.Statement.get_type`.
        tables: table references, ordered by first occurrence.
        columns: column references, ordered by first occurrence.
    """

    type: str
    tables: list[TableRef] = field(default_factory=list)
    columns: list[ColumnRef] = field(default_factory=list)


class _Collector:
    """Collects the references of one statement."""

    def __init__(self, statement: sql.Statement):
        self.statement = statement
        self.tables: list[TableRef] = []
        self._table_index: dict[tuple, TableRef] = {}
        self.columns: list[ColumnRef] = []
        self._column_keys: set[tuple] = set()

    def collect(self) -> StatementInventory:
        self._walk(self.statement, cte_scope=())
        return StatementInventory(
            type=self.statement.get_type(),
            tables=self.tables,
            columns=self.columns,
        )

    # -- table traversal -------------------------------------------------

    def _walk(self, token_list: sql.TokenList,
              cte_scope: tuple[str, ...],
              initial_state: str | None = None,
              precollect_ctes: bool = True) -> None:
        tokens = token_list.tokens
        scope = cte_scope
        first = token_list.token_first(skip_cm=True)
        if first is not None and first.ttype == T.Keyword.CTE:
            tokens, scope = self._consume_cte(token_list, tokens, cte_scope)
        elif precollect_ctes:
            # A WITH clause nested inside a group (e.g. a derived table)
            # defines names that also apply to the trailing reference at
            # this level; collect them before classifying identifiers.
            scope = self._collect_nested_cte_names(tokens, cte_scope)
        state = initial_state
        for token in tokens:
            if token.is_group:
                state = self._visit_group(token, state, scope, token_list)
                continue
            if not token.is_keyword:
                continue
            keyword = token.normalized
            if keyword in _FROM_KEYWORDS:
                if keyword == "FROM" and self._is_delete_context(token_list):
                    state = "write"
                else:
                    state = "read"
            elif keyword in _TARGET_KEYWORDS:
                state = "write"
            elif keyword in _REGION_END_KEYWORDS:
                state = None
            elif token.ttype in (
                    T.Keyword.DML, T.Keyword.DDL, T.Keyword.CTE):
                state = None

    def _collect_nested_cte_names(self, tokens,
                                  cte_scope: tuple[str, ...]) -> tuple[str, ...]:
        names = list(cte_scope)
        for token in tokens:
            if not token.is_group:
                continue
            for leaf in token.flatten():
                if leaf.ttype != T.Keyword.CTE:
                    continue
                for child in leaf.parent.tokens:
                    if isinstance(child, sql.IdentifierList):
                        definitions = child.get_identifiers()
                    elif isinstance(child, (sql.Identifier, sql.Function)):
                        definitions = [child]
                    else:
                        continue
                    for definition in definitions:
                        name = self._cte_name(definition)
                        if name is not None and name not in names:
                            names.append(name)
        return tuple(names)

    def _consume_cte(self, token_list, tokens, cte_scope):
        """Process a leading WITH clause.

        Returns the tokens of the trailing query and the extended CTE
        scope.  Definition names never enter the table inventory; only
        the tables referenced by the definition bodies are recorded.
        """
        scope = list(cte_scope)
        for token in tokens:
            if isinstance(token, sql.IdentifierList):
                definitions = list(token.get_identifiers())
            elif isinstance(token, (sql.Identifier, sql.Function)):
                definitions = [token]
            elif token.ttype in (T.Keyword.DML, T.Keyword.DDL):
                # Reached the trailing query: definitions are done.
                index = token_list.token_index(token)
                return tokens[index:], tuple(scope)
            else:
                # WITH / RECURSIVE keywords, whitespace, punctuation.
                continue
            for definition in definitions:
                name = self._cte_name(definition)
                body = definition.token_next_by(i=sql.Parenthesis)[1]
                if body is not None:
                    # The name is visible inside its own body (recursive
                    # self-reference) but not yet to sibling definitions.
                    body_scope = tuple(scope)
                    if name is not None and name not in body_scope:
                        body_scope = (*body_scope, name)
                    self._walk(body, body_scope, precollect_ctes=False)
                if name is not None and name not in scope:
                    scope.append(name)
        return [], tuple(scope)

    @staticmethod
    def _cte_name(definition) -> str | None:
        # A column-list form, ``WITH c (a, b) AS (...)``, is grouped as
        # a Function, possibly wrapped in an outer Identifier.
        if isinstance(definition, sql.Function):
            inner = definition.token_next_by(i=sql.Identifier)[1]
            if inner is None:
                return None
            segments = _identifier_segments(inner)
        else:
            segments = _identifier_segments(definition)
        return segments[0] if segments else None

    def _visit_group(self, token, state, cte_scope, parent):
        if isinstance(token, sql.IdentifierList):
            if state in ("read", "write"):
                for child in token.get_identifiers():
                    self._visit_identifier(child, state, cte_scope)
                return state
            self._walk(token, cte_scope)
            return None
        if isinstance(token, sql.Identifier):
            if state in ("read", "write"):
                self._visit_identifier(token, state, cte_scope)
                return state
            self._walk(token, cte_scope)
            return None
        if isinstance(token, sql.Function):
            # ``INSERT INTO t (a, b)`` is grouped as a Function whose
            # leading identifier is the target table.
            if state == "write":
                target = token.token_next_by(i=sql.Identifier)[1]
                if target is not None:
                    segments = _identifier_segments(target)
                    if segments:
                        self._record_table(
                            segments, _identifier_alias(target), "write",
                            cte_scope)
            self._walk(token, cte_scope)
            return None
        if isinstance(token, sql.Parenthesis):
            if self._parenthesis_is_subquery(token):
                # A subquery starts a new query: descend without
                # inheriting the current clause (a USING column list must
                # not be read as a table list).
                self._walk(token, cte_scope)
                return None
            if self._is_parenthesised_join_group(token):
                # A parenthesised JOIN group keeps the outer clause so
                # its leading table is classified too.
                self._walk(token, cte_scope, initial_state=state)
                return state
            self._walk(token, cte_scope)
            return None
        self._walk(token, cte_scope)
        return None

    def _visit_identifier(self, token, mode, cte_scope):
        first_group = next(
            (child for child in token.tokens if child.is_group), None)
        if isinstance(first_group, sql.Function):
            # A table function is not a physical table, but a subquery
            # among its arguments still is a query.
            for child in first_group.tokens:
                if isinstance(child, sql.Parenthesis) and \
                        self._parenthesis_is_subquery(child):
                    self._walk(child, cte_scope)
            return
        if isinstance(first_group, sql.Parenthesis):
            # A derived table: the alias is not a table name.
            self._walk(first_group, cte_scope)
            return
        segments = _identifier_segments(token)
        if not segments:
            return
        self._record_table(
            segments, _identifier_alias(token), mode, cte_scope)

    @staticmethod
    def _first_inner_token(parenthesis: sql.Parenthesis):
        for token in parenthesis.tokens:
            if token.is_whitespace or token.ttype in T.Comment:
                continue
            if token.match(T.Punctuation, "("):
                continue
            return token
        return None

    @classmethod
    def _parenthesis_is_subquery(cls, parenthesis) -> bool:
        first = cls._first_inner_token(parenthesis)
        return first is not None and first.ttype in (
            T.Keyword.DML, T.Keyword.DDL, T.Keyword.CTE)

    @staticmethod
    def _is_parenthesised_join_group(parenthesis) -> bool:
        return any(
            token.is_keyword and "JOIN" in token.normalized
            for token in parenthesis.flatten())

    @staticmethod
    def _is_delete_context(token_list) -> bool:
        for token in token_list.tokens:
            if token.is_whitespace or token.ttype in T.Comment:
                continue
            return token.match(T.Keyword.DML, "DELETE")
        return False

    # -- recording -------------------------------------------------------

    def _record_table(self, segments, alias, mode, cte_scope):
        if segments[0] in cte_scope:
            # A CTE name shadows a physical table inside its scope.
            return
        key = (segments, mode)
        existing = self._table_index.get(key)
        if existing is not None:
            existing.count += 1
            if existing.alias is None and alias is not None:
                existing.alias = alias
            return
        ref = TableRef(name=segments, alias=alias, mode=mode, count=1)
        self._table_index[key] = ref
        self.tables.append(ref)


def collect(sql_text: str) -> list[StatementInventory]:
    """Collect table and column references from *sql_text*.

    :param sql_text: A string containing one or more SQL statements.
    :returns: one :class:`StatementInventory` per statement, in the
        same order as :func:`sqlparse.split`.
    """
    # Imported lazily to avoid a circular import: the package
    # initializer imports this module.
    from sqlparse import parse

    return [_Collector(statement).collect() for statement in parse(sql_text)]
