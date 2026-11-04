"""SQLite graph database engine with recursive CTE traversal support."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from core.models import Edge, Node, NodeType, RelationType, Symbol


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    language TEXT,
    content_hash TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS edges (
    source_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    metadata JSON,
    PRIMARY KEY (source_id, target_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id, relation_type);

CREATE TABLE IF NOT EXISTS symbol_details (
    node_id INTEGER PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    signature TEXT,
    return_type TEXT,
    scope TEXT,
    docstring TEXT,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    start_byte INTEGER NOT NULL,
    end_byte INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_symbol_details_name ON symbol_details(name);
CREATE INDEX IF NOT EXISTS idx_symbol_details_qname ON symbol_details(qualified_name);
"""


class Database:
    """Embedded SQLite storage engine managing the codebase dependency graph.

    Supports foreign keys, WAL journaling, node/edge CRUD, and recursive CTE
    traversals for downstream impact analysis, upstream dependencies, and cycle audits.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        """Initialize the database connection.

        Args:
            db_path: Path to the SQLite database file, or ':memory:'.
        """
        self.db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._connect()
        self.init_schema()

    def _connect(self) -> None:
        """Establish connection with foreign key enforcement and row factory."""
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode by default; transactions used explicitly when needed
        )
        self._conn.row_factory = sqlite3.Row

        # Performance and integrity pragmas
        self._conn.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL;")
            self._conn.execute("PRAGMA synchronous = NORMAL;")

    @property
    def conn(self) -> sqlite3.Connection:
        """Return the active SQLite connection, reconnecting if needed."""
        if self._conn is None:
            self._connect()
        assert self._conn is not None
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Database:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def init_schema(self) -> None:
        """Execute DDL statements to create tables and indexes."""
        self.conn.executescript(SCHEMA_SQL)

    # -------------------------------------------------------------------------
    # Node CRUD
    # -------------------------------------------------------------------------

    def upsert_node(self, node: Node) -> int:
        """Insert or update a node identified by its unique path.

        Returns:
            The integer ID of the inserted or updated node.
        """
        sql = """
        INSERT INTO nodes (path, type, name, language, content_hash)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            type = excluded.type,
            name = excluded.name,
            language = excluded.language,
            content_hash = excluded.content_hash,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id;
        """
        node_type = node.type.value if isinstance(node.type, NodeType) else node.type
        cur = self.conn.execute(
            sql,
            (node.path, node_type, node.name, node.language, node.content_hash),
        )
        row = cur.fetchone()
        node_id = int(row["id"])
        node.id = node_id
        return node_id

    def get_node_by_path(self, path: str) -> Node | None:
        """Retrieve a node by its repository-relative normalized path."""
        sql = "SELECT id, path, type, name, language, content_hash, updated_at FROM nodes WHERE path = ?;"
        cur = self.conn.execute(sql, (path,))
        row = cur.fetchone()
        if row is None:
            return None
        return Node(
            id=row["id"],
            path=row["path"],
            type=NodeType(row["type"]),
            name=row["name"],
            language=row["language"],
            content_hash=row["content_hash"],
            updated_at=str(row["updated_at"]),
        )

    def get_node_by_id(self, node_id: int) -> Node | None:
        """Retrieve a node by its primary key ID."""
        sql = "SELECT id, path, type, name, language, content_hash, updated_at FROM nodes WHERE id = ?;"
        cur = self.conn.execute(sql, (node_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return Node(
            id=row["id"],
            path=row["path"],
            type=NodeType(row["type"]),
            name=row["name"],
            language=row["language"],
            content_hash=row["content_hash"],
            updated_at=str(row["updated_at"]),
        )

    def delete_node_by_path(self, path: str) -> bool:
        """Delete a node by path. Foreign keys cascade deletes to connected edges.

        Also deletes any child symbol nodes prefixed with path::.
        """
        self.conn.execute("DELETE FROM nodes WHERE path LIKE ? || '::%';", (path,))
        cur = self.conn.execute("DELETE FROM nodes WHERE path = ?;", (path,))
        return cur.rowcount > 0

    def delete_node_by_id(self, node_id: int) -> bool:
        """Delete a node by ID. Also removes child symbol nodes if it was a file."""
        node = self.get_node_by_id(node_id)
        if node:
            self.conn.execute("DELETE FROM nodes WHERE path LIKE ? || '::%';", (node.path,))
        cur = self.conn.execute("DELETE FROM nodes WHERE id = ?;", (node_id,))
        return cur.rowcount > 0

    def list_nodes(self, node_type: NodeType | str | None = None) -> list[Node]:
        """List all nodes, optionally filtered by node type."""
        if node_type is not None:
            type_val = node_type.value if isinstance(node_type, NodeType) else node_type
            cur = self.conn.execute(
                "SELECT id, path, type, name, language, content_hash, updated_at FROM nodes WHERE type = ? ORDER BY path;",
                (type_val,),
            )
        else:
            cur = self.conn.execute(
                "SELECT id, path, type, name, language, content_hash, updated_at FROM nodes ORDER BY path;"
            )
        return [
            Node(
                id=row["id"],
                path=row["path"],
                type=NodeType(row["type"]),
                name=row["name"],
                language=row["language"],
                content_hash=row["content_hash"],
                updated_at=str(row["updated_at"]),
            )
            for row in cur.fetchall()
        ]

    # -------------------------------------------------------------------------
    # Edge CRUD
    # -------------------------------------------------------------------------

    def upsert_edge(self, edge: Edge) -> None:
        """Insert or update an edge between two existing nodes."""
        sql = """
        INSERT INTO edges (source_id, target_id, relation_type, metadata)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_id, target_id, relation_type) DO UPDATE SET
            metadata = excluded.metadata;
        """
        rel_type = (
            edge.relation_type.value
            if isinstance(edge.relation_type, RelationType)
            else edge.relation_type
        )
        meta_json = json.dumps(edge.metadata) if edge.metadata is not None else "{}"
        self.conn.execute(sql, (edge.source_id, edge.target_id, rel_type, meta_json))

    def get_edges(
        self,
        source_id: int | None = None,
        target_id: int | None = None,
        relation_type: RelationType | str | None = None,
    ) -> list[Edge]:
        """Query edges filtered by source, target, or relation type."""
        clauses: list[str] = []
        params: list[Any] = []

        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        if target_id is not None:
            clauses.append("target_id = ?")
            params.append(target_id)
        if relation_type is not None:
            rel_val = (
                relation_type.value
                if isinstance(relation_type, RelationType)
                else relation_type
            )
            clauses.append("relation_type = ?")
            params.append(rel_val)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT source_id, target_id, relation_type, metadata FROM edges {where};"
        cur = self.conn.execute(sql, tuple(params))

        edges: list[Edge] = []
        for row in cur.fetchall():
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            edges.append(
                Edge(
                    source_id=row["source_id"],
                    target_id=row["target_id"],
                    relation_type=RelationType(row["relation_type"]),
                    metadata=meta,
                )
            )
        return edges

    def delete_edges_from_source(
        self,
        source_id: int,
        relation_type: RelationType | str | None = None,
    ) -> int:
        """Delete outgoing edges from a source node, optionally filtered by relation type."""
        if relation_type is not None:
            rel_val = (
                relation_type.value
                if isinstance(relation_type, RelationType)
                else relation_type
            )
            cur = self.conn.execute(
                "DELETE FROM edges WHERE source_id = ? AND relation_type = ?;",
                (source_id, rel_val),
            )
        else:
            cur = self.conn.execute(
                "DELETE FROM edges WHERE source_id = ?;",
                (source_id,),
            )
        return cur.rowcount

    # -------------------------------------------------------------------------
    # Recursive CTE Graph Traversals
    # -------------------------------------------------------------------------

    def get_downstream_impact(self, path: str) -> list[dict[str, Any]]:
        """Perform impact analysis for a file or node.

        Finds all files that directly or transitively depend on the given path by
        recursively walking incoming IMPORTS edges (target -> source).

        Args:
            path: Normalized path of the target node.

        Returns:
            List of dicts containing:
                id, path, type, name, language, depth
            ordered by shortest dependency depth, then path.
        """
        sql = """
        WITH RECURSIVE downstream(node_id, depth, path_chain) AS (
            SELECT n.id, 0, '/' || n.id || '/'
            FROM nodes n
            WHERE n.path = ?

            UNION

            SELECT e.source_id, d.depth + 1, d.path_chain || e.source_id || '/'
            FROM edges e
            JOIN downstream d ON e.target_id = d.node_id
            WHERE e.relation_type = 'IMPORTS'
              AND instr(d.path_chain, '/' || e.source_id || '/') = 0
        )
        SELECT
            n.id,
            n.path,
            n.type,
            n.name,
            n.language,
            MIN(d.depth) AS depth
        FROM downstream d
        JOIN nodes n ON d.node_id = n.id
        WHERE d.depth > 0
        GROUP BY n.id, n.path, n.type, n.name, n.language
        ORDER BY depth ASC, n.path ASC;
        """
        cur = self.conn.execute(sql, (path,))
        return [
            {
                "id": row["id"],
                "path": row["path"],
                "type": row["type"],
                "name": row["name"],
                "language": row["language"],
                "depth": row["depth"],
            }
            for row in cur.fetchall()
        ]

    def get_upstream_dependencies(self, path: str) -> list[dict[str, Any]]:
        """Find all files that the given path directly or transitively depends upon.

        Recursively walks outgoing IMPORTS edges (source -> target).

        Args:
            path: Normalized path of the source node.

        Returns:
            List of dicts containing:
                id, path, type, name, language, depth
            ordered by shortest dependency depth, then path.
        """
        sql = """
        WITH RECURSIVE upstream(node_id, depth, path_chain) AS (
            SELECT n.id, 0, '/' || n.id || '/'
            FROM nodes n
            WHERE n.path = ?

            UNION

            SELECT e.target_id, u.depth + 1, u.path_chain || e.target_id || '/'
            FROM edges e
            JOIN upstream u ON e.source_id = u.node_id
            WHERE e.relation_type = 'IMPORTS'
              AND instr(u.path_chain, '/' || e.target_id || '/') = 0
        )
        SELECT
            n.id,
            n.path,
            n.type,
            n.name,
            n.language,
            MIN(u.depth) AS depth
        FROM upstream u
        JOIN nodes n ON u.node_id = n.id
        WHERE u.depth > 0
        GROUP BY n.id, n.path, n.type, n.name, n.language
        ORDER BY depth ASC, n.path ASC;
        """
        cur = self.conn.execute(sql, (path,))
        return [
            {
                "id": row["id"],
                "path": row["path"],
                "type": row["type"],
                "name": row["name"],
                "language": row["language"],
                "depth": row["depth"],
            }
            for row in cur.fetchall()
        ]

    def detect_cycles(self) -> list[list[str]]:
        """Audit the graph for circular dependencies in IMPORTS relationships.

        Uses a recursive CTE with path tracking to detect cycles. Cycles are normalized
        to start at their minimum node ID to ensure each unique circular chain is returned
        once without duplicate cyclic permutations.

        Returns:
            List of cycles, where each cycle is an ordered list of file paths
            starting and ending at the origin (e.g., ['a.py', 'b.py', 'c.py', 'a.py']).
        """
        # Step 1: Self-loops (A -> A)
        self_loop_sql = """
        SELECT n.path AS start_path, n.path AS end_path
        FROM edges e
        JOIN nodes n ON e.source_id = n.id
        WHERE e.relation_type = 'IMPORTS' AND e.source_id = e.target_id;
        """
        cur = self.conn.execute(self_loop_sql)
        cycles: list[list[str]] = []
        for row in cur.fetchall():
            cycles.append([row["start_path"], row["end_path"]])

        # Step 2: Cycles of length >= 2
        # We start walks from source_id where target_id > source_id.
        # We ensure every node in the walk is >= start_id.
        # A cycle completes when e.target_id == start_id.
        multi_cycle_sql = """
        WITH RECURSIVE cycle_walk(start_id, current_id, depth, path_str) AS (
            SELECT e.source_id, e.target_id, 1, e.source_id || ',' || e.target_id
            FROM edges e
            WHERE e.relation_type = 'IMPORTS'
              AND e.target_id > e.source_id

            UNION ALL

            SELECT cw.start_id, e.target_id, cw.depth + 1, cw.path_str || ',' || e.target_id
            FROM edges e
            JOIN cycle_walk cw ON e.source_id = cw.current_id
            WHERE e.relation_type = 'IMPORTS'
              AND cw.depth < 50
              AND e.target_id >= cw.start_id
              AND (
                  e.target_id = cw.start_id
                  OR instr(',' || cw.path_str || ',', ',' || e.target_id || ',') = 0
              )
        )
        SELECT path_str
        FROM cycle_walk
        WHERE current_id = start_id;
        """
        cur = self.conn.execute(multi_cycle_sql)
        path_strs = [row["path_str"] for row in cur.fetchall()]

        if not path_strs:
            return cycles

        # Resolve node IDs to file paths
        all_ids: set[int] = set()
        parsed_id_lists: list[list[int]] = []
        for p in path_strs:
            ids = [int(x) for x in p.split(",")]
            parsed_id_lists.append(ids)
            all_ids.update(ids)

        placeholders = ",".join("?" for _ in all_ids)
        id_to_path: dict[int, str] = {}
        if all_ids:
            cur = self.conn.execute(
                f"SELECT id, path FROM nodes WHERE id IN ({placeholders});",
                list(all_ids),
            )
            for row in cur.fetchall():
                id_to_path[row["id"]] = row["path"]

        for id_list in parsed_id_lists:
            cycle_path = [id_to_path[i] for i in id_list if i in id_to_path]
            if len(cycle_path) == len(id_list):
                cycles.append(cycle_path)

        return cycles

    def get_contained_children(
        self, folder_path: str, recursive: bool = False
    ) -> list[Node]:
        """Retrieve items contained within a folder node using CONTAINS relations.

        Args:
            folder_path: Normalized path of the folder.
            recursive: If True, recursively retrieves all descendant items.

        Returns:
            List of child Node instances.
        """
        if not recursive:
            sql = """
            SELECT n.id, n.path, n.type, n.name, n.language, n.content_hash, n.updated_at
            FROM nodes n
            JOIN edges e ON e.target_id = n.id
            JOIN nodes parent ON e.source_id = parent.id
            WHERE parent.path = ? AND e.relation_type = 'CONTAINS'
            ORDER BY n.type DESC, n.path ASC;
            """
            cur = self.conn.execute(sql, (folder_path,))
        else:
            sql = """
            WITH RECURSIVE containment(node_id) AS (
                SELECT n.id
                FROM nodes n
                WHERE n.path = ?

                UNION

                SELECT e.target_id
                FROM edges e
                JOIN containment c ON e.source_id = c.node_id
                WHERE e.relation_type = 'CONTAINS'
            )
            SELECT n.id, n.path, n.type, n.name, n.language, n.content_hash, n.updated_at
            FROM containment c
            JOIN nodes n ON c.node_id = n.id
            WHERE n.path != ?
            ORDER BY n.type DESC, n.path ASC;
            """
            cur = self.conn.execute(sql, (folder_path, folder_path))

        return [
            Node(
                id=row["id"],
                path=row["path"],
                type=NodeType(row["type"]),
                name=row["name"],
                language=row["language"],
                content_hash=row["content_hash"],
                updated_at=str(row["updated_at"]),
            )
            for row in cur.fetchall()
        ]

    # -------------------------------------------------------------------------
    # Option 3 Hybrid Symbol Storage
    # -------------------------------------------------------------------------

    def upsert_symbol(self, file_node_id: int, file_path: str, symbol: Symbol) -> int:
        """Register a symbol as a graph node and store its rich details (Option 3 Hybrid).

        Args:
            file_node_id: Primary key ID of the containing file node.
            file_path: Normalized path of the file containing this symbol.
            symbol: The Symbol instance with syntax metadata.

        Returns:
            The integer ID of the symbol node in the nodes table.
        """
        symbol_node_path = f"{file_path}::{symbol.qualified_name}"
        node_id = self.upsert_node(
            Node(
                path=symbol_node_path,
                type=NodeType.SYMBOL,
                name=symbol.name,
                language="python",
            )
        )

        detail_sql = """
        INSERT INTO symbol_details (
            node_id, name, qualified_name, kind, signature, return_type,
            scope, docstring, start_line, end_line, start_byte, end_byte
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(node_id) DO UPDATE SET
            name = excluded.name,
            qualified_name = excluded.qualified_name,
            kind = excluded.kind,
            signature = excluded.signature,
            return_type = excluded.return_type,
            scope = excluded.scope,
            docstring = excluded.docstring,
            start_line = excluded.start_line,
            end_line = excluded.end_line,
            start_byte = excluded.start_byte,
            end_byte = excluded.end_byte;
        """
        self.conn.execute(
            detail_sql,
            (
                node_id,
                symbol.name,
                symbol.qualified_name,
                symbol.kind,
                symbol.signature,
                symbol.return_type,
                symbol.scope,
                symbol.docstring,
                symbol.line_number,
                symbol.end_line,
                symbol.start_byte,
                symbol.end_byte,
            ),
        )

        # Establish parent CONTAINS containment edge
        parent_id = file_node_id
        if symbol.parent_symbol:
            parent_path = f"{file_path}::{symbol.parent_symbol}"
            parent_node = self.get_node_by_path(parent_path)
            if parent_node and parent_node.id:
                parent_id = parent_node.id

        self.upsert_edge(
            Edge(
                source_id=parent_id,
                target_id=node_id,
                relation_type=RelationType.CONTAINS,
            )
        )

        return node_id

    def get_symbol_details(self, node_id: int) -> Symbol | None:
        """Retrieve rich symbol metadata by its node_id."""
        sql = """
        SELECT
            sd.name, sd.qualified_name, sd.kind, sd.signature, sd.return_type,
            sd.scope, sd.docstring, sd.start_line, sd.end_line, sd.start_byte, sd.end_byte
        FROM symbol_details sd
        WHERE sd.node_id = ?;
        """
        cur = self.conn.execute(sql, (node_id,))
        row = cur.fetchone()
        if row is None:
            return None

        # Deriving parent_symbol from qualified_name if applicable
        qname = row["qualified_name"]
        parent = qname.rsplit(".", 1)[0] if "." in qname else None

        return Symbol(
            name=row["name"],
            kind=row["kind"],
            line_number=row["start_line"],
            end_line=row["end_line"],
            start_byte=row["start_byte"],
            end_byte=row["end_byte"],
            parent_symbol=parent,
            qualified_name=row["qualified_name"],
            signature=row["signature"],
            return_type=row["return_type"],
            docstring=row["docstring"],
            scope=row["scope"],
        )

    def get_symbols_for_file(self, file_path: str) -> list[Symbol]:
        """Retrieve all symbols defined in a file, ordered by start line."""
        sql = """
        SELECT
            sd.name, sd.qualified_name, sd.kind, sd.signature, sd.return_type,
            sd.scope, sd.docstring, sd.start_line, sd.end_line, sd.start_byte, sd.end_byte
        FROM nodes n
        JOIN symbol_details sd ON sd.node_id = n.id
        WHERE n.path LIKE ? || '::%'
        ORDER BY sd.start_line ASC, sd.start_byte ASC;
        """
        cur = self.conn.execute(sql, (file_path,))
        symbols: list[Symbol] = []
        for row in cur.fetchall():
            qname = row["qualified_name"]
            parent = qname.rsplit(".", 1)[0] if "." in qname else None
            symbols.append(
                Symbol(
                    name=row["name"],
                    kind=row["kind"],
                    line_number=row["start_line"],
                    end_line=row["end_line"],
                    start_byte=row["start_byte"],
                    end_byte=row["end_byte"],
                    parent_symbol=parent,
                    qualified_name=row["qualified_name"],
                    signature=row["signature"],
                    return_type=row["return_type"],
                    docstring=row["docstring"],
                    scope=row["scope"],
                )
            )
        return symbols

    def find_symbols_by_name(self, name: str) -> list[dict[str, Any]]:
        """Search symbols across the codebase by symbol name."""
        sql = """
        SELECT
            n.id AS node_id,
            n.path AS symbol_path,
            sd.name,
            sd.qualified_name,
            sd.kind,
            sd.signature,
            sd.return_type,
            sd.scope,
            sd.docstring,
            sd.start_line,
            sd.end_line,
            sd.start_byte,
            sd.end_byte
        FROM symbol_details sd
        JOIN nodes n ON sd.node_id = n.id
        WHERE sd.name = ?
        ORDER BY n.path ASC;
        """
        cur = self.conn.execute(sql, (name,))
        return [dict(row) for row in cur.fetchall()]
