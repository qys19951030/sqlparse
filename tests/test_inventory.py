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
