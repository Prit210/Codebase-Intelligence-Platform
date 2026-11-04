"""Unit and integration tests for SQLite graph database engine."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.db import Database
from core.models import Edge, Node, NodeType, RelationType


class TestDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")

    def tearDown(self) -> None:
        self.db.close()

    def test_schema_creation_and_reconnection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_file = Path(tmp_dir) / "test_graph.db"
            db = Database(db_file)
            n_id = db.upsert_node(
                Node(path="a.py", type=NodeType.FILE, name="a.py", language="python")
            )
            self.assertGreater(n_id, 0)
            db.close()

            # Re-open database file and verify persistence
            db2 = Database(db_file)
            loaded = db2.get_node_by_path("a.py")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.path, "a.py")
            db2.close()

    def test_node_upsert_and_retrieval(self) -> None:
        # Initial insert
        node = Node(
            path="core/db.py",
            type=NodeType.FILE,
            name="db.py",
            language="python",
            content_hash="hash1",
        )
        node_id = self.db.upsert_node(node)
        self.assertGreater(node_id, 0)
        self.assertEqual(node.id, node_id)

        # Retrieve by path and ID
        by_path = self.db.get_node_by_path("core/db.py")
        self.assertIsNotNone(by_path)
        self.assertEqual(by_path.id, node_id)
        self.assertEqual(by_path.content_hash, "hash1")

        by_id = self.db.get_node_by_id(node_id)
        self.assertIsNotNone(by_id)
        self.assertEqual(by_id.path, "core/db.py")

        # Update on conflict
        node.content_hash = "hash2"
        updated_id = self.db.upsert_node(node)
        self.assertEqual(updated_id, node_id)

        reloaded = self.db.get_node_by_path("core/db.py")
        self.assertEqual(reloaded.content_hash, "hash2")

    def test_foreign_key_and_cascade_delete(self) -> None:
        id_a = self.db.upsert_node(
            Node(path="a.py", type=NodeType.FILE, name="a.py")
        )
        id_b = self.db.upsert_node(
            Node(path="b.py", type=NodeType.FILE, name="b.py")
        )

        # Foreign key failure on invalid node ID
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.upsert_edge(
                Edge(source_id=id_a, target_id=99999, relation_type=RelationType.IMPORTS)
            )

        # Valid edge insert
        self.db.upsert_edge(
            Edge(
                source_id=id_a,
                target_id=id_b,
                relation_type=RelationType.IMPORTS,
                metadata={"line": 5},
            )
        )
        edges = self.db.get_edges(source_id=id_a)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].metadata["line"], 5)

        # Deleting node A should cascade delete the edge
        self.db.delete_node_by_path("a.py")
        edges_after = self.db.get_edges()
        self.assertEqual(len(edges_after), 0)

    def test_downstream_impact_and_upstream_dependencies(self) -> None:
        """Test linear dependency chain: app.py -> service.py -> db.py."""
        id_app = self.db.upsert_node(
            Node(path="app.py", type=NodeType.FILE, name="app.py")
        )
        id_service = self.db.upsert_node(
            Node(path="service.py", type=NodeType.FILE, name="service.py")
        )
        id_db = self.db.upsert_node(
            Node(path="db.py", type=NodeType.FILE, name="db.py")
        )

        # app imports service; service imports db
        self.db.upsert_edge(Edge(source_id=id_app, target_id=id_service, relation_type=RelationType.IMPORTS))
        self.db.upsert_edge(Edge(source_id=id_service, target_id=id_db, relation_type=RelationType.IMPORTS))

        # Downstream impact of db.py (who is affected if db.py changes?)
        impact = self.db.get_downstream_impact("db.py")
        self.assertEqual(len(impact), 2)
        # service.py is direct downstream (depth 1)
        self.assertEqual(impact[0]["path"], "service.py")
        self.assertEqual(impact[0]["depth"], 1)
        # app.py is transitive downstream (depth 2)
        self.assertEqual(impact[1]["path"], "app.py")
        self.assertEqual(impact[1]["depth"], 2)

        # Upstream dependencies of app.py (what does app.py depend on?)
        upstream = self.db.get_upstream_dependencies("app.py")
        self.assertEqual(len(upstream), 2)
        self.assertEqual(upstream[0]["path"], "service.py")
        self.assertEqual(upstream[0]["depth"], 1)
        self.assertEqual(upstream[1]["path"], "db.py")
        self.assertEqual(upstream[1]["depth"], 2)

    def test_diamond_dependencies(self) -> None:
        """Test diamond: A -> B, A -> C, B -> D, C -> D."""
        id_a = self.db.upsert_node(Node(path="a.py", type=NodeType.FILE, name="a.py"))
        id_b = self.db.upsert_node(Node(path="b.py", type=NodeType.FILE, name="b.py"))
        id_c = self.db.upsert_node(Node(path="c.py", type=NodeType.FILE, name="c.py"))
        id_d = self.db.upsert_node(Node(path="d.py", type=NodeType.FILE, name="d.py"))

        self.db.upsert_edge(Edge(source_id=id_a, target_id=id_b, relation_type=RelationType.IMPORTS))
        self.db.upsert_edge(Edge(source_id=id_a, target_id=id_c, relation_type=RelationType.IMPORTS))
        self.db.upsert_edge(Edge(source_id=id_b, target_id=id_d, relation_type=RelationType.IMPORTS))
        self.db.upsert_edge(Edge(source_id=id_c, target_id=id_d, relation_type=RelationType.IMPORTS))

        # Downstream of d.py should include b.py and c.py at depth 1, and a.py at depth 2 (no duplicates)
        impact = self.db.get_downstream_impact("d.py")
        self.assertEqual(len(impact), 3)
        depth_1_paths = {item["path"] for item in impact if item["depth"] == 1}
        depth_2_paths = {item["path"] for item in impact if item["depth"] == 2}
        self.assertEqual(depth_1_paths, {"b.py", "c.py"})
        self.assertEqual(depth_2_paths, {"a.py"})

    def test_cycle_detection(self) -> None:
        """Test detecting circular dependency chains: A -> B -> C -> A."""
        id_a = self.db.upsert_node(Node(path="a.py", type=NodeType.FILE, name="a.py"))
        id_b = self.db.upsert_node(Node(path="b.py", type=NodeType.FILE, name="b.py"))
        id_c = self.db.upsert_node(Node(path="c.py", type=NodeType.FILE, name="c.py"))
        id_d = self.db.upsert_node(Node(path="d.py", type=NodeType.FILE, name="d.py"))

        # No cycles initially
        self.db.upsert_edge(Edge(source_id=id_a, target_id=id_b, relation_type=RelationType.IMPORTS))
        self.db.upsert_edge(Edge(source_id=id_b, target_id=id_c, relation_type=RelationType.IMPORTS))
        self.db.upsert_edge(Edge(source_id=id_c, target_id=id_d, relation_type=RelationType.IMPORTS))
        self.assertEqual(self.db.detect_cycles(), [])

        # Close the cycle: C -> A (A -> B -> C -> A)
        self.db.upsert_edge(Edge(source_id=id_c, target_id=id_a, relation_type=RelationType.IMPORTS))
        cycles = self.db.detect_cycles()
        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        # Start and end must match
        self.assertEqual(cycle[0], cycle[-1])
        # The cycle path contains a, b, c, a
        self.assertEqual(set(cycle), {"a.py", "b.py", "c.py"})
        self.assertEqual(len(cycle), 4)

    def test_self_loop_cycle_detection(self) -> None:
        """Test self loop detection: A -> A."""
        id_a = self.db.upsert_node(Node(path="recursive_module.py", type=NodeType.FILE, name="recursive_module.py"))
        self.db.upsert_edge(Edge(source_id=id_a, target_id=id_a, relation_type=RelationType.IMPORTS))
        cycles = self.db.detect_cycles()
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0], ["recursive_module.py", "recursive_module.py"])

    def test_list_nodes_filter_and_delete_edges(self) -> None:
        id_f = self.db.upsert_node(Node(path="folder1", type=NodeType.FOLDER, name="folder1"))
        id_file = self.db.upsert_node(Node(path="folder1/file.py", type=NodeType.FILE, name="file.py"))

        folders = self.db.list_nodes(node_type=NodeType.FOLDER)
        self.assertEqual(len(folders), 1)
        self.assertEqual(folders[0].path, "folder1")

        files = self.db.list_nodes(node_type=NodeType.FILE)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].path, "folder1/file.py")

        # Delete edges from source
        self.db.upsert_edge(Edge(source_id=id_f, target_id=id_file, relation_type=RelationType.CONTAINS))
        self.assertEqual(len(self.db.get_edges(source_id=id_f)), 1)
        deleted_count = self.db.delete_edges_from_source(id_f, RelationType.CONTAINS)
        self.assertEqual(deleted_count, 1)
        self.assertEqual(len(self.db.get_edges(source_id=id_f)), 0)

    def test_folder_containment(self) -> None:
        id_root = self.db.upsert_node(Node(path="src", type=NodeType.FOLDER, name="src"))
        id_sub = self.db.upsert_node(Node(path="src/utils", type=NodeType.FOLDER, name="utils"))
        id_file1 = self.db.upsert_node(Node(path="src/main.py", type=NodeType.FILE, name="main.py"))
        id_file2 = self.db.upsert_node(Node(path="src/utils/helpers.py", type=NodeType.FILE, name="helpers.py"))

        self.db.upsert_edge(Edge(source_id=id_root, target_id=id_sub, relation_type=RelationType.CONTAINS))
        self.db.upsert_edge(Edge(source_id=id_root, target_id=id_file1, relation_type=RelationType.CONTAINS))
        self.db.upsert_edge(Edge(source_id=id_sub, target_id=id_file2, relation_type=RelationType.CONTAINS))

        # Direct children
        direct = self.db.get_contained_children("src", recursive=False)
        self.assertEqual(len(direct), 2)
        direct_paths = {c.path for c in direct}
        self.assertEqual(direct_paths, {"src/utils", "src/main.py"})

        # Recursive descendants
        descendants = self.db.get_contained_children("src", recursive=True)
        self.assertEqual(len(descendants), 3)
        desc_paths = {c.path for c in descendants}
        self.assertEqual(desc_paths, {"src/utils", "src/main.py", "src/utils/helpers.py"})


if __name__ == "__main__":
    unittest.main()
