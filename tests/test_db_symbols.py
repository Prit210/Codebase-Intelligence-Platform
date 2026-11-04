"""Unit tests for Option 3 Hybrid Symbol Storage in Database."""

from __future__ import annotations

import unittest

from core.db import Database
from core.models import Node, NodeType, RelationType, Symbol


class TestDatabaseSymbols(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")

    def tearDown(self) -> None:
        self.db.close()

    def test_upsert_symbol_and_retrieval(self) -> None:
        file_id = self.db.upsert_node(
            Node(path="core/db.py", type=NodeType.FILE, name="db.py", language="python")
        )

        sym_class = Symbol(
            name="Database",
            kind="class",
            line_number=35,
            end_line=250,
            start_byte=1000,
            end_byte=8000,
            signature="(object)",
            docstring="SQLite graph engine.",
        )
        class_node_id = self.db.upsert_symbol(file_id, "core/db.py", sym_class)
        self.assertGreater(class_node_id, 0)

        # Check that it exists in nodes table as type=symbol
        class_node = self.db.get_node_by_path("core/db.py::Database")
        self.assertIsNotNone(class_node)
        self.assertEqual(class_node.type, NodeType.SYMBOL)

        # Check symbol_details table
        details = self.db.get_symbol_details(class_node_id)
        self.assertIsNotNone(details)
        self.assertEqual(details.name, "Database")
        self.assertEqual(details.kind, "class")
        self.assertEqual(details.docstring, "SQLite graph engine.")
        self.assertEqual(details.signature, "(object)")

        # Upsert a method belonging to Database
        sym_method = Symbol(
            name="upsert_symbol",
            kind="method",
            line_number=120,
            end_line=150,
            start_byte=3500,
            end_byte=4500,
            parent_symbol="Database",
            qualified_name="Database.upsert_symbol",
            signature="def upsert_symbol(self, file_node_id: int) -> int",
            return_type="int",
            docstring="Register symbol in DB.",
        )
        method_node_id = self.db.upsert_symbol(file_id, "core/db.py", sym_method)
        self.assertGreater(method_node_id, 0)

        # Check CONTAINS edge: Database (class) contains upsert_symbol (method)
        edges = self.db.get_edges(source_id=class_node_id, target_id=method_node_id, relation_type=RelationType.CONTAINS)
        self.assertEqual(len(edges), 1)

        # Check get_symbols_for_file
        file_symbols = self.db.get_symbols_for_file("core/db.py")
        self.assertEqual(len(file_symbols), 2)
        self.assertEqual(file_symbols[0].name, "Database")
        self.assertEqual(file_symbols[1].name, "upsert_symbol")

        # Check find_symbols_by_name
        found = self.db.find_symbols_by_name("upsert_symbol")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["qualified_name"], "Database.upsert_symbol")
        self.assertEqual(found[0]["symbol_path"], "core/db.py::Database.upsert_symbol")

    def test_cascading_delete_on_symbols(self) -> None:
        file_id = self.db.upsert_node(
            Node(path="service.py", type=NodeType.FILE, name="service.py", language="python")
        )
        sym = Symbol(
            name="handle_request",
            kind="function",
            line_number=10,
            end_line=20,
            start_byte=200,
            end_byte=400,
        )
        sym_id = self.db.upsert_symbol(file_id, "service.py", sym)

        # Deleting the parent file must cascade delete symbol node and symbol_details
        self.db.delete_node_by_path("service.py")

        # Symbol node should be gone
        self.assertIsNone(self.db.get_node_by_path("service.py::handle_request"))
        # Details should be gone
        self.assertIsNone(self.db.get_symbol_details(sym_id))
        # Edge should be gone
        self.assertEqual(len(self.db.get_edges(source_id=file_id)), 0)


if __name__ == "__main__":
    unittest.main()
