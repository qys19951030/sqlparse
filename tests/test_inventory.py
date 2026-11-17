# Tests for sqlparse.inventory: table/column reference collection.

import sqlparse
from sqlparse.inventory import (
    ColumnRef,
    StatementInventory,
    TableRef,
    collect,
)


def test_result_structures_and_entry_point():
    inv = StatementInventory(type="SELECT")
    assert inv.type == "SELECT"
    assert inv.tables == []
    assert inv.columns == []
    ref = TableRef(name=("schema", "tbl"), alias="t", mode="read", count=1)
    assert ref.key == ("schema", "tbl")
    col = ColumnRef(name="id", qualifier="t")
    assert col.name == "id" and col.qualifier == "t"
    assert isinstance(collect("SELECT 1"), list)


# ---------------------------------------------------------------------------
# Step 1: statement traversal and type extraction
# ---------------------------------------------------------------------------

def test_collect_returns_one_entry_per_statement_like_split():
    sql_text = (
        "SELECT * FROM t1; "
        "INSERT INTO t2 VALUES (1); "
        "UPDATE t3 SET a = 1; "
        "DELETE FROM t4; "
    )
    result = collect(sql_text)
    assert len(result) == len(sqlparse.split(sql_text))
    assert [item.type for item in result] == [
        "SELECT", "INSERT", "UPDATE", "DELETE"]
    assert all(isinstance(item, StatementInventory) for item in result)


def test_collect_type_matches_statement_get_type():
    sql_text = (
        "WITH c AS (SELECT 1) SELECT * FROM c; "
        "CREATE TABLE t (a int); "
        "DROP TABLE t; "
        "MERGE INTO t USING s ON 1=1 "
        "WHEN MATCHED THEN UPDATE SET a=1; "
        "TRUNCATE TABLE t; "
    )
    statements = sqlparse.parse(sql_text)
    result = collect(sql_text)
    assert [item.type for item in result] == [
        stmt.get_type() for stmt in statements]


def test_collect_empty_and_whitespace_statements():
    result = collect(";;;   ;")
    assert len(result) == len(sqlparse.split(";;;   ;"))
    assert all(item.type == "UNKNOWN" for item in result)
    assert all(item.tables == [] and item.columns == [] for item in result)


# ---------------------------------------------------------------------------
# Step 2: FROM / JOIN table extraction
# ---------------------------------------------------------------------------

def _table_keys(inventory):
    return [table.name for table in inventory.tables]


def test_from_single_table():
    result = collect("SELECT * FROM t1;")
    assert _table_keys(result[0]) == [("t1",)]
    assert result[0].tables[0].mode == "read"
    assert result[0].tables[0].alias is None


def test_join_chain_and_on_columns_are_not_tables():
    sql_text = (
        "SELECT a.x FROM schema.tbl_a AS a "
        "JOIN tbl_b b ON a.id = b.id "
        "LEFT JOIN d.tbl_c c ON c.id = b.id;"
    )
    result = collect(sql_text)[0]
    assert _table_keys(result) == [
        ("schema", "tbl_a"), ("tbl_b",), ("d", "tbl_c")]
    assert [table.alias for table in result.tables] == ["a", "b", "c"]
    assert all(table.mode == "read" for table in result.tables)


def test_comma_separated_identifier_list():
    result = collect("SELECT * FROM t1, t2, schema3.t3;")[0]
    assert _table_keys(result) == [("t1",), ("t2",), ("schema3", "t3")]


def test_quoted_and_qualified_name_normalization():
    sql_text = (
        'SELECT * FROM "Mixed"."Tbl" AS m '
        "JOIN `back_t` b ON 1=1 "
        "JOIN [br_t] c ON 1=1;"
    )
    result = collect(sql_text)[0]
    assert _table_keys(result) == [
        ("Mixed", "Tbl"), ("back_t",), ("br_t",)]
    assert [table.alias for table in result.tables] == ["m", "b", "c"]


def test_derived_table_subquery_is_walked_not_named():
    result = collect("SELECT * FROM (SELECT id FROM inner_t) sub, outer_t;")[0]
    assert _table_keys(result) == [("inner_t",), ("outer_t",)]
    assert all("sub" not in name for name in _table_keys(result))


def test_deeply_nested_subqueries():
    result = collect(
        "select * from (select * from (select * from deep_t) x) y;")[0]
    assert _table_keys(result) == [("deep_t",)]


def test_insert_target_and_select_sources_are_separate_modes():
    sql_text = (
        "INSERT INTO target_t (a, b) SELECT x, y FROM src_t "
        "WHERE x IN (SELECT x FROM src2);"
    )
    result = collect(sql_text)[0]
    by_mode = {}
    for table in result.tables:
        by_mode.setdefault(table.mode, []).append(table.name)
    assert by_mode["write"] == [("target_t",)]
    assert by_mode["read"] == [("src_t",), ("src2",)]


def test_insert_without_column_list():
    result = collect("INSERT INTO t1 SELECT * FROM t2;")[0]
    by_mode = {}
    for table in result.tables:
        by_mode.setdefault(table.mode, []).append(table.name)
    assert by_mode == {"write": [("t1",)], "read": [("t2",)]}


def test_update_target_from_and_set_subquery():
    sql_text = (
        "UPDATE t1 SET a = (SELECT max(a) FROM t2) "
        "FROM t3 WHERE t1.id = t3.id;"
    )
    result = collect(sql_text)[0]
    by_mode = {}
    for table in result.tables:
        by_mode.setdefault(table.mode, []).append(table.name)
    assert by_mode == {"write": [("t1",)], "read": [("t2",), ("t3",)]}


def test_delete_from_and_using_with_join():
    sql_text = (
        "DELETE FROM t1 USING t2 JOIN t3 ON t2.id = t3.id "
        "WHERE t1.id = t2.id;"
    )
    result = collect(sql_text)[0]
    by_mode = {}
    for table in result.tables:
        by_mode.setdefault(table.mode, []).append(table.name)
    assert by_mode == {"write": [("t1",)], "read": [("t2",), ("t3",)]}


def test_values_insert_only_has_target():
    result = collect("INSERT INTO t1 VALUES (1, 2), (3, 4);")[0]
    assert [(t.name, t.mode) for t in result.tables] == [
        (("t1",), "write")]


def test_functions_are_not_tables():
    result = collect("SELECT count(*) FROM t1 WHERE x = my_func(1);")[0]
    assert _table_keys(result) == [("t1",)]


def test_merge_target_and_source():
    sql_text = (
        "MERGE INTO tgt USING src ON tgt.id = src.id "
        "WHEN MATCHED THEN UPDATE SET a = 1;"
    )
    result = collect(sql_text)[0]
    by_mode = {}
    for table in result.tables:
        by_mode.setdefault(table.mode, []).append(table.name)
    assert by_mode == {"write": [("tgt",)], "read": [("src",)]}


def test_union_collects_both_branches():
    result = collect("SELECT id FROM t1 UNION ALL SELECT id FROM t2;")[0]
    assert _table_keys(result) == [("t1",), ("t2",)]


def test_select_list_scalar_subquery_is_covered():
    sql_text = "SELECT (SELECT max(id) FROM t2) AS m, name FROM t1;"
    result = collect(sql_text)[0]
    assert _table_keys(result) == [("t2",), ("t1",)]


def test_parenthesised_join_group_collects_all_tables():
    result = collect(
        "SELECT * FROM a JOIN (b JOIN c ON b.id = c.id) "
        "ON a.id = b.id;")[0]
    assert _table_keys(result) == [("a",), ("b",), ("c",)]


def test_using_column_list_is_not_a_table():
    result = collect("SELECT * FROM a JOIN b USING (id, other_id);")[0]
    assert _table_keys(result) == [("a",), ("b",)]
    assert all("id" not in name for name in _table_keys(result))


def test_table_function_is_not_a_table():
    result = collect(
        "SELECT * FROM unnest(ARRAY[1, 2]) AS u(id) "
        "JOIN real_t r ON 1=1;")[0]
    assert _table_keys(result) == [("real_t",)]
    assert all("unnest" not in name for name in _table_keys(result))


def test_table_function_alias_column_list_not_inventoried():
    result = collect("SELECT * FROM generate_series(1, 5) AS g;")[0]
    assert result.tables == []


# ---------------------------------------------------------------------------
# Step 3: CTE expansion
# ---------------------------------------------------------------------------

def test_cte_name_is_not_a_table_but_its_sources_are():
    result = collect(
        "WITH cte1 AS (SELECT id FROM base_tbl) SELECT * FROM cte1;")[0]
    assert _table_keys(result) == [("base_tbl",)]
    assert result.tables[0].mode == "read"


def test_multi_level_cte_resolves_to_physical_tables():
    result = collect(
        "WITH cte1 AS (SELECT id FROM base_tbl), "
        "cte2 AS (SELECT id FROM cte1 JOIN other_tbl USING (id)) "
        "SELECT * FROM cte2;")[0]
    assert _table_keys(result) == [("base_tbl",), ("other_tbl",)]


def test_cte_shadows_physical_table_with_same_name():
    result = collect(
        "WITH t1 AS (SELECT id FROM real_t) SELECT * FROM t1;")[0]
    assert _table_keys(result) == [("real_t",)]


def test_cte_column_list_form():
    result = collect(
        "WITH c (a, b) AS (SELECT a, b FROM base) SELECT * FROM c;")[0]
    assert _table_keys(result) == [("base",)]


def test_nested_with_in_derived_table():
    result = collect(
        "SELECT * FROM "
        "(WITH x AS (SELECT id FROM deep_t) SELECT * FROM x) z;")[0]
    assert _table_keys(result) == [("deep_t",)]
    assert all("x" not in name and "z" not in name
               for name in _table_keys(result))


def test_cte_with_update_statement():
    result = collect(
        "WITH c AS (SELECT id FROM base) "
        "UPDATE t1 SET a=1 FROM c WHERE t1.id = c.id;")[0]
    by_mode = {}
    for table in result.tables:
        by_mode.setdefault(table.mode, []).append(table.name)
    assert by_mode == {"read": [("base",)], "write": [("t1",)]}


def test_cte_scope_is_per_statement():
    result = collect(
        "WITH c AS (SELECT id FROM t_a) SELECT * FROM c; "
        "SELECT * FROM c;")
    assert _table_keys(result[0]) == [("t_a",)]
    # the second statement has no CTE scope: c is a plain table
    assert _table_keys(result[1]) == [("c",)]


def test_cte_body_contains_subquery_and_join():
    result = collect(
        "WITH c AS ("
        "SELECT * FROM a JOIN (SELECT id FROM b) s ON a.id = s.id"
        ") SELECT * FROM c;")[0]
    assert _table_keys(result) == [("a",), ("b",)]


def test_recursive_cte_self_reference_resolves_to_base_table():
    result = collect(
        "WITH RECURSIVE r AS ("
        "SELECT id FROM base UNION ALL SELECT id FROM r"
        ") SELECT * FROM r;")[0]
    assert _table_keys(result) == [("base",)]


# ---------------------------------------------------------------------------
# Step 4: merging of repeated tables and occurrence counts
# ---------------------------------------------------------------------------

def test_same_table_merged_with_accumulated_count():
    result = collect(
        "SELECT * FROM t1 JOIN t1 AS x ON 1=1 "
        "WHERE id IN (SELECT id FROM t1);")[0]
    assert len(result.tables) == 1
    table = result.tables[0]
    assert table.name == ("t1",)
    assert table.count == 3


def test_different_writings_of_same_table_merge():
    result = collect(
        "SELECT * FROM `schema`.`tbl` "
        "JOIN SCHEMA.tbl t ON 1=1 JOIN schema.tbl ON 1=1;")[0]
    assert len(result.tables) == 1
    assert result.tables[0].name == ("schema", "tbl")
    assert result.tables[0].count == 3
    assert result.tables[0].alias == "t"


def test_quoted_content_is_case_preserved():
    result = collect(
        'SELECT * FROM schema."Tbl" JOIN schema.tbl ON 1=1;')[0]
    assert _table_keys(result) == [("schema", "Tbl"), ("schema", "tbl")]


def test_counts_are_per_statement():
    sql_text = (
        "SELECT * FROM t1 JOIN t1 ON 1=1; "
        "SELECT * FROM t1; "
        "SELECT * FROM other;"
    )
    result = collect(sql_text)
    assert result[0].tables[0].count == 2
    assert result[1].tables[0].count == 1
    assert result[2].tables[0].name == ("other",)


def test_read_and_write_of_same_table_are_separate_entries():
    result = collect(
        "INSERT INTO t1 SELECT * FROM t1 JOIN t1 ON 1=1;")[0]
    entries = [(t.name, t.mode, t.count) for t in result.tables]
    assert entries == [(("t1",), "write", 1), (("t1",), "read", 2)]


def test_first_occurrence_order_is_preserved():
    result = collect("SELECT * FROM zeta, alpha, zeta, beta, alpha;")[0]
    assert _table_keys(result) == [("zeta",), ("alpha",), ("beta",)]
    assert result.tables[0].count == 2
    assert result.tables[1].count == 2
    assert result.tables[2].count == 1


# ---------------------------------------------------------------------------
# Step 5: column reference extraction
# ---------------------------------------------------------------------------

def _column_refs(inventory):
    return [(column.qualifier, column.name) for column in inventory.columns]


def test_columns_in_select_where_and_order_by():
    result = collect("SELECT a, b FROM t1 WHERE a > 1 ORDER BY b;")[0]
    assert _column_refs(result) == [(None, "a"), (None, "b")]


def test_qualified_columns_split_qualifier_and_name():
    result = collect(
        "SELECT t.a, u.b FROM t1 t JOIN t2 u ON t.id = u.id "
        "WHERE t.x > 1;")[0]
    assert _column_refs(result) == [
        ("t", "a"), ("u", "b"), ("t", "id"), ("u", "id"), ("t", "x")]
    assert all(name not in (("t",), ("u",))
               for name in _table_keys(result))


def test_on_identifiers_are_columns_not_tables():
    result = collect("SELECT * FROM a JOIN b ON a.id = b.id;")[0]
    assert _table_keys(result) == [("a",), ("b",)]
    assert _column_refs(result) == [("a", "id"), ("b", "id")]


def test_columns_are_deduplicated():
    result = collect(
        "SELECT a, a, b FROM t1 WHERE a = 1 ORDER BY a, b;")[0]
    assert _column_refs(result) == [(None, "a"), (None, "b")]


def test_insert_column_list_and_select_columns():
    result = collect(
        "INSERT INTO t1 (a, b) SELECT x, y FROM t2 "
        "WHERE x IN (SELECT z FROM t3);")[0]
    assert _column_refs(result) == [
        (None, "a"), (None, "b"),
        (None, "x"), (None, "y"), (None, "z")]


def test_update_set_and_where_columns():
    result = collect("UPDATE t1 SET c = 1 WHERE d > 2;")[0]
    assert _column_refs(result) == [(None, "c"), (None, "d")]


def test_group_by_having_and_window_columns():
    result = collect(
        "SELECT row_number() OVER (PARTITION BY a ORDER BY b), max(c) "
        "FROM t1 GROUP BY a, b HAVING max(c) > 1;")[0]
    refs = _column_refs(result)
    assert (None, "a") in refs
    assert (None, "b") in refs
    assert (None, "c") in refs


def test_function_names_and_values_are_not_columns():
    result = collect(
        "SELECT count(*), my_func(a) FROM t1 WHERE b = my_func(2);")[0]
    refs = _column_refs(result)
    assert (None, "count") not in refs
    assert (None, "my_func") not in refs
    assert (None, "a") in refs and (None, "b") in refs
    assert collect("INSERT INTO t1 VALUES (1, 2);")[0].columns == []


def test_wildcards_are_not_columns():
    result = collect("SELECT *, t.* FROM t1 t;")[0]
    assert result.columns == []


def test_case_when_columns_are_collected():
    result = collect(
        "SELECT CASE WHEN a > 1 THEN b ELSE c END FROM t1;")[0]
    assert _column_refs(result) == [(None, "a"), (None, "b"), (None, "c")]


def test_multipart_qualified_column():
    result = collect("SELECT s.t.col FROM s.t;")[0]
    assert result.columns == [ColumnRef(name="col", qualifier="s.t")]


def test_columns_are_scoped_per_statement():
    result = collect("SELECT a FROM t1; SELECT b FROM t2;")
    assert _column_refs(result[0]) == [(None, "a")]
    assert _column_refs(result[1]) == [(None, "b")]


def test_cte_names_and_headers_are_not_columns():
    result = collect(
        "WITH cte1 AS (SELECT id FROM base_tbl), "
        "c (a, b) AS (SELECT a, b FROM base) "
        "SELECT x FROM cte1, c;")[0]
    refs = _column_refs(result)
    assert (None, "cte1") not in refs
    assert (None, "c") not in refs
    assert (None, "id") in refs
    assert (None, "x") in refs
    assert len([r for r in refs if r == (None, "a")]) == 1


def test_ddl_target_is_not_collected_as_column():
    result = collect(
        "CREATE TABLE new_t AS SELECT col1, col2 FROM old_t;")[0]
    assert (None, "new_t") not in _column_refs(result)
    assert _column_refs(result) == [(None, "col1"), (None, "col2")]
    assert _table_keys(result) == [("old_t",)]


def test_drop_and_truncate_targets_are_not_read_sources():
    drop = collect("DROP TABLE old_t;")[0]
    truncate = collect("TRUNCATE TABLE t1;")[0]
    assert drop.tables == [] and drop.columns == []
    assert truncate.tables == [] and truncate.columns == []


def test_merge_guard_names_are_not_columns():
    result = collect(
        "MERGE INTO tgt USING src ON tgt.id = src.id "
        "WHEN MATCHED THEN UPDATE SET a = src.b;")[0]
    refs = _column_refs(result)
    assert (None, "matched") not in refs
    assert ("tgt", "id") in refs and ("src", "id") in refs
    assert ("src", "b") in refs and (None, "a") in refs


def test_merge_guard_and_condition_keeps_real_columns():
    result = collect(
        "MERGE INTO tgt USING src ON tgt.id = src.id "
        "WHEN MATCHED AND tgt.x = 1 THEN UPDATE SET a = 2;")[0]
    refs = _column_refs(result)
    assert (None, "matched") not in refs
    assert ("tgt", "x") in refs


def test_qualified_insert_target_with_column_list():
    result = collect(
        "INSERT INTO sch.dest (a, b) SELECT x FROM src;")[0]
    entries = [(t.name, t.mode) for t in result.tables]
    assert entries == [(("sch", "dest"), "write"), (("src",), "read")]
    assert _column_refs(result) == [
        (None, "a"), (None, "b"), (None, "x")]


def test_insert_values_column_list_is_not_a_table():
    result = collect("INSERT INTO t1 (a,b) VALUES (1, 2);")[0]
    assert [(t.name, t.mode) for t in result.tables] == [(("t1",), "write")]
    assert _column_refs(result) == [(None, "a"), (None, "b")]


def test_window_definition_is_not_a_table():
    result = collect(
        "SELECT count(*) OVER (PARTITION BY a) FROM t1;")[0]
    assert _table_keys(result) == [("t1",)]
    assert _column_refs(result) == [(None, "a")]


def test_malformed_sql_does_not_raise():
    for sql_text in (
        "JOIN FROM , t2 t2 DELETE DELETE SELECT JOIN DELETE DELETE FROM",
        "SELECT = 1 WITH a , * INTO , DELETE DELETE WITH t1",
        "DELETE JOIN WHERE b ( b SET , USING WITH *",
        "",
        ";;;",
    ):
        assert isinstance(collect(sql_text), list)


def test_delete_target_is_write_with_leading_cte():
    result = collect(
        "WITH c AS (SELECT id FROM base) "
        "DELETE FROM t1 USING c WHERE t1.id = c.id;")[0]
    by_mode = {}
    for table in result.tables:
        by_mode.setdefault(table.mode, []).append(table.name)
    assert by_mode == {"read": [("base",)], "write": [("t1",)]}


def test_insert_select_with_cte_source():
    result = collect(
        "WITH c AS (SELECT * FROM base) "
        "INSERT INTO t SELECT * FROM c;")[0]
    by_mode = {}
    for table in result.tables:
        by_mode.setdefault(table.mode, []).append(table.name)
    assert by_mode == {"read": [("base",)], "write": [("t",)]}


def test_exists_subquery_tables_and_columns():
    result = collect(
        "SELECT * FROM outer_t o WHERE EXISTS "
        "(SELECT 1 FROM inner_t i WHERE i.x = o.x);")[0]
    assert _table_keys(result) == [("outer_t",), ("inner_t",)]
    assert _column_refs(result) == [("i", "x"), ("o", "x")]
