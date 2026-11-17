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
    "WINDOW",
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

    Only the name part before an alias (``AS`` keyword or a nested
    identifier/function) contributes, and only leaf name tokens are
    considered, so function parentheses, typecasts and similar
    sub-groups are ignored.
    """
    segments = []
    for child in token.tokens:
        if child.match(T.Keyword, "AS"):
            break
        if isinstance(child, (sql.Identifier, sql.Function)):
            # Everything from here on is an alias or a column-list /
            # function body; any leading name inside a function is the
            # defined name (e.g. CTE with an explicit column list).
            if isinstance(child, sql.Function):
                inner = child.token_next_by(i=sql.Identifier)[1]
                if inner is not None:
                    segments.extend(_identifier_segments(inner))
            break
        if not child.is_group and child.ttype in _NAME_TYPES:
            segments.append(_normalize_segment(child.value))
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
        self._table_index: dict[tuple, TableRef] = {}
        self.columns: list[ColumnRef] = []
        self._column_keys: set[tuple[str, ...]] = set()

    def collect(self) -> StatementInventory:
        self._walk(self.statement, cte_scope=())
        self._collect_columns(self.statement)
        return StatementInventory(
            type=self.statement.get_type(),
            tables=self.tables,
            columns=self.columns,
        )

    # -- token traversal -------------------------------------------------

    def _walk(self, token_list: sql.TokenList,
              cte_scope: tuple[str, ...]) -> tuple[str, ...]:
        tokens = token_list.tokens
        scope = cte_scope
        first = token_list.token_first(skip_cm=True)
        if first is not None and first.ttype == T.Keyword.CTE:
            tokens, scope = self._consume_cte(token_list, tokens, cte_scope)
        # CTEs defined inside nested groups enter the scope before the
        # current level's identifiers are classified, so a trailing
        # reference (e.g. the outer FROM of a derived table containing
        # a WITH clause) is resolved against them as well.
        scope = self._collect_nested_cte_names(tokens, scope)
        return self._walk_tokens(tokens, scope)

    def _collect_nested_cte_names(self, tokens,
                                  scope: tuple[str, ...]) -> tuple[str, ...]:
        names = list(scope)
        for token in tokens:
            if not token.is_group:
                continue
            for leaf in token.flatten():
                if leaf.ttype != T.Keyword.CTE:
                    continue
                parent = leaf.parent
                for child in parent.tokens:
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

        Returns the tokens of the trailing query and the CTE scope
        (outer scope plus the newly defined names).  CTE definition
        names never enter the table inventory; only the tables
        referenced inside the definition bodies are recorded.
        """
        scope = list(cte_scope)
        for token in tokens:
            if isinstance(token, sql.IdentifierList):
                definitions = list(token.get_identifiers())
            elif isinstance(token, (sql.Identifier, sql.Function)):
                definitions = [token]
            elif token.is_keyword and token.ttype != T.Keyword.CTE:
                # Reached the trailing DML/DDL keyword: definitions are
                # done, everything from here on belongs to the main query.
                index = token_list.token_index(token)
                return tokens[index:], tuple(scope)
            else:
                continue
            for definition in definitions:
                name = self._cte_name(definition)
                body = definition.token_next_by(i=sql.Parenthesis)[1]
                if body is not None:
                    self._walk(body, tuple(scope))
                if name is not None:
                    scope.append(name)
        return [], tuple(scope)

    @staticmethod
    def _cte_name(definition) -> str | None:
        # A column-list form ("WITH c (a, b) AS (...)") is grouped as a
        # Function, possibly wrapped in an outer Identifier.
        if isinstance(definition, sql.Function):
            inner = definition.token_next_by(i=sql.Identifier)[1]
            if inner is None:
                return None
            segments = _identifier_segments(inner)
        else:
            segments = _identifier_segments(definition)
        return segments[0] if segments else None

    def _walk_tokens(self, tokens, cte_scope: tuple[str, ...]):
        # Clause-aware walk at one grouping level.  ``state`` tracks the
        # table-bearing clause the following identifier belongs to.
        state = None
        scope = cte_scope
        for token in tokens:
            if token.is_group:
                scope = self._visit_group(token, state, scope)
                continue

            if not token.is_keyword:
                continue

            keyword = token.normalized
            if keyword in _FROM_KEYWORDS:
                # DELETE FROM is a write target; every other FROM/JOIN/
                # USING clause starts a read-source region.
                if keyword == "FROM" and self._is_delete_context_tokens(
                        tokens):
                    state = "write"
                else:
                    state = "read"
            elif keyword in _TARGET_KEYWORDS:
                state = "write"
            elif keyword in _REGION_END_KEYWORDS:
                state = None
            elif token.ttype in (T.Keyword.DML, T.Keyword.DDL, T.Keyword.CTE):
                # A new inner query starts; its FROM is found again by
                # the keyword scan below.
                state = None
        return scope

    def _visit_group(self, token: sql.TokenList, state,
                     cte_scope: tuple[str, ...]) -> tuple[str, ...]:
        if isinstance(token, sql.IdentifierList):
            if state in ("read", "write"):
                scope = cte_scope
                for child in token.get_identifiers():
                    scope = self._visit_identifier(child, state, scope)
                return scope
            return self._walk(token, cte_scope)
        elif isinstance(token, sql.Identifier):
            if state in ("read", "write"):
                return self._visit_identifier(token, state, cte_scope)
            return self._walk(token, cte_scope)
        elif isinstance(token, sql.Function):
            # INSERT INTO target with a column list is grouped as a
            # Function; its leading identifier is the target table.
            if state == "write":
                target = token.token_next_by(i=sql.Identifier)[1]
                if target is not None:
                    segments = _identifier_segments(target)
                    if segments:
                        self._record_table(
                            segments, _identifier_alias(target), "write",
                            cte_scope)
            return self._walk(token, cte_scope)
        elif isinstance(token, sql.Parenthesis):
            # Subquery: descend without inheriting the current clause,
            # e.g. the column list of USING (...) must not be read as
            # tables.
            return self._walk(token, cte_scope)
        else:
            return self._walk(token, cte_scope)

    def _visit_identifier(self, token: sql.Identifier, mode,
                          cte_scope: tuple[str, ...]) -> tuple[str, ...]:
        # A parenthesised body means a derived table (subquery in FROM);
        # its alias is not a table name, the inner query is walked.
        inner = token.token_next_by(i=sql.Parenthesis)[1]
        if inner is not None:
            return self._walk(inner, cte_scope)
        segments = _identifier_segments(token)
        if not segments:
            return cte_scope
        self._record_table(
            segments, _identifier_alias(token), mode, cte_scope)
        return cte_scope

    @staticmethod
    def _is_delete_context_tokens(tokens) -> bool:
        for token in tokens:
            if token.is_whitespace or token.ttype in T.Comment:
                continue
            return token.match(T.Keyword.DML, "DELETE")
        return False

    # -- reference recording ---------------------------------------------

    def _record_table(self, segments, alias, mode, cte_scope):
        # CTE names shadow physical tables inside their scope.
        if segments[0] in cte_scope:
            return
        # Read sources and write targets are inventoried separately even
        # when they happen to be the same physical table.
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

    def _record_column(self, identifier: sql.Identifier):
        if identifier.is_wildcard():
            # ``*`` and ``t.*`` select whole rows, they are not a named
            # column reference.
            return
        segments = _identifier_segments(identifier)
        if not segments:
            return
        name = segments[-1]
        qualifier = ".".join(segments[:-1]) or None
        key = (qualifier, name)
        if key in self._column_keys:
            return
        self._column_keys.add(key)
        self.columns.append(ColumnRef(name=name, qualifier=qualifier))

    # -- column traversal ------------------------------------------------

    def _collect_columns(self, token_list: sql.TokenList, state=None):
        """Walk the tree collecting column references.

        Table-position identifiers, function calls and VALUES lists are
        skipped; identifiers appearing anywhere else (select lists,
        WHERE/ON/GROUP BY/ORDER BY/HAVING, scalar subqueries, ...) are
        recorded as columns.
        """
        in_cte_header = False
        for token in token_list.tokens:
            if not token.is_group:
                if token.is_keyword:
                    if token.ttype == T.Keyword.CTE:
                        # The following groups are CTE definitions: their
                        # names and column-list headers are not columns.
                        in_cte_header = True
                        state = None
                        continue
                    if in_cte_header and token.ttype in (
                            T.Keyword.DML, T.Keyword.DDL):
                        in_cte_header = False
                    state = self._column_state(token, state)
                continue

            if in_cte_header and isinstance(
                    token, (sql.Identifier, sql.IdentifierList,
                            sql.Function)):
                self._collect_columns_from_cte_definitions(token)
                continue
            if isinstance(token, sql.Values):
                continue
            if isinstance(token, sql.Function):
                # The function name is not a column, but window
                # definitions and scalar function arguments may contain
                # some.
                self._collect_columns_in_function(token)
                continue
            if isinstance(token, sql.Identifier):
                if state in ("read", "write") and \
                        token.token_next_by(i=sql.Parenthesis)[1] is None:
                    # Table reference (or statement target), not a column.
                    continue
                self._record_column(token)
                # A column identifier may still contain a subquery
                # (derived tables are handled by the table-position
                # check above); descend to be safe.
                self._collect_columns(token, state)
                continue
            self._collect_columns(token, state)

    def _collect_columns_from_cte_definitions(self, group):
        """Walk only the query bodies of CTE definition groups."""
        if isinstance(group, sql.IdentifierList):
            for child in group.get_identifiers():
                self._collect_columns_from_cte_definitions(child)
            return
        # A definition holds its query body in the last parenthesised
        # group; the CTE name and an optional column-list header come
        # earlier and must be skipped.
        body = None
        for child in group.tokens:
            if isinstance(child, sql.Parenthesis):
                body = child
        if body is not None:
            self._collect_columns(body)

    def _collect_columns_in_function(self, function: sql.Function):
        for token in function.tokens:
            if isinstance(token, sql.Over):
                # PARTITION BY / ORDER BY inside a window definition
                # reference real columns.
                self._collect_columns(token)
            elif isinstance(token, sql.Parenthesis):
                # Function arguments may themselves be columns; literals
                # and wildcards simply do not form Identifier groups.
                self._collect_columns(token)
            # the function name Identifier and whitespace are ignored

    @staticmethod
    def _column_state(token: sql.Token, state):
        keyword = token.normalized
        if keyword in _TARGET_KEYWORDS:
            return "write"
        if keyword in _FROM_KEYWORDS:
            # DELETE FROM introduces the target table; either way the
            # following identifier is a table, never a column.
            return "read"
        if keyword in _REGION_END_KEYWORDS:
            return None
        if token.ttype in (T.Keyword.DML, T.Keyword.DDL, T.Keyword.CTE):
            return None
        return state


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
