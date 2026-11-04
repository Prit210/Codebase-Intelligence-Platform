"""Unit tests for core models and DTOs."""

from __future__ import annotations

import unittest

from core.models import (
    Edge,
    Node,
    NodeType,
    RawImport,
    RelationType,
    ResolvedTarget,
    Symbol,
)


class TestModels(unittest.TestCase):
    def test_node_initialization(self) -> None:
        node = Node(
            path="core/models.py",
            type="file",
            name="models.py",
            language="python",
            content_hash="abc123hash",
        )
        self.assertEqual(node.path, "core/models.py")
        self.assertEqual(node.type, NodeType.FILE)
        self.assertIsInstance(node.type, NodeType)
        self.assertEqual(node.name, "models.py")
        self.assertEqual(node.language, "python")
        self.assertEqual(node.content_hash, "abc123hash")
        self.assertIsNone(node.id)

    def test_edge_initialization(self) -> None:
        edge = Edge(
            source_id=1,
            target_id=2,
            relation_type="IMPORTS",
            metadata={"line": 42, "alias": "np"},
        )
        self.assertEqual(edge.source_id, 1)
        self.assertEqual(edge.target_id, 2)
        self.assertEqual(edge.relation_type, RelationType.IMPORTS)
        self.assertEqual(edge.metadata["line"], 42)

    def test_raw_import(self) -> None:
        imp = RawImport(
            module="core.models",
            names=[("Node", None), ("Edge", "GraphEdge")],
            level=0,
            line_number=10,
            is_type_checking=True,
        )
        self.assertEqual(imp.module, "core.models")
        self.assertEqual(len(imp.names), 2)
        self.assertEqual(imp.names[1], ("Edge", "GraphEdge"))
        self.assertEqual(imp.level, 0)
        self.assertEqual(imp.line_number, 10)
        self.assertTrue(imp.is_type_checking)

    def test_resolved_target(self) -> None:
        internal_target = ResolvedTarget(
            target_path="core/models.py",
            is_external=False,
            module_name="core.models",
        )
        self.assertEqual(internal_target.target_path, "core/models.py")
        self.assertFalse(internal_target.is_external)

        external_target = ResolvedTarget(
            target_path=None,
            is_external=True,
            module_name="json",
        )
        self.assertTrue(external_target.is_external)
        self.assertIsNone(external_target.target_path)

    def test_symbol(self) -> None:
        sym = Symbol(
            name="get_downstream_impact",
            kind="method",
            line_number=210,
            parent_symbol="Database",
        )
        self.assertEqual(sym.name, "get_downstream_impact")
        self.assertEqual(sym.kind, "method")
        self.assertEqual(sym.line_number, 210)
        self.assertEqual(sym.parent_symbol, "Database")


if __name__ == "__main__":
    unittest.main()
