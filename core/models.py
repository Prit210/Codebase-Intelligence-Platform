"""Data models and DTOs for Codebase Intelligence."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    """Types of graph nodes."""

    FOLDER = "folder"
    FILE = "file"
    SYMBOL = "symbol"


class RelationType(str, Enum):
    """Types of directed graph relationships."""

    CONTAINS = "CONTAINS"
    IMPORTS = "IMPORTS"
    CALLS = "CALLS"


@dataclass
class Node:
    """Represents a node in the codebase dependency graph.

    Attributes:
        path: Normalized path relative to repository root (e.g., 'core/models.py').
        type: The category of node (folder, file, or symbol).
        name: Short display name (e.g., 'models.py', 'core').
        id: Unique database identifier (assigned upon insertion).
        language: Programming language identifier (e.g., 'python', None for folders).
        content_hash: SHA-256 hash of the content for incremental updates.
        updated_at: Timestamp of last update.
    """

    path: str
    type: NodeType | str
    name: str
    id: int | None = None
    language: str | None = None
    content_hash: str | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.type, str):
            self.type = NodeType(self.type)


@dataclass
class Edge:
    """Represents a directed relationship between two nodes.

    Attributes:
        source_id: ID of the originating node.
        target_id: ID of the destination node.
        relation_type: The relationship category (e.g., CONTAINS, IMPORTS, CALLS).
        metadata: Arbitrary contextual attributes (e.g., line numbers, alias, symbol names).
    """

    source_id: int
    target_id: int
    relation_type: RelationType | str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.relation_type, str):
            self.relation_type = RelationType(self.relation_type)


@dataclass
class RawImport:
    """Represents an un-resolved import statement extracted from source AST.

    Attributes:
        module: The raw imported module path or string (e.g., 'os.path', '..utils', 'app.core').
        names: List of imported symbol names and optional aliases (e.g., [('Path', None), ('walk', 'os_walk')]).
        level: Relative import level (0 = absolute, 1 = '.', 2 = '..', etc.).
        line_number: Line in source file where the import appears (1-indexed).
        is_type_checking: True if this import is guarded inside `if TYPE_CHECKING:`.
    """

    module: str
    names: list[tuple[str, str | None]] = field(default_factory=list)
    level: int = 0
    line_number: int = 1
    is_type_checking: bool = False


@dataclass
class ResolvedTarget:
    """Result of attempting to resolve a RawImport to a concrete file or external package.

    Attributes:
        target_path: Repository-relative path to the target file if resolved internally, or None.
        is_external: True if import belongs to stdlib or external third-party package.
        module_name: Canonical module name (e.g., 'sys', 'numpy', 'my_package.submodule').
        symbol: Specific symbol name targeted if applicable (e.g., 'User').
    """

    target_path: str | None = None
    is_external: bool = False
    module_name: str = ""
    symbol: str | None = None


@dataclass
class Symbol:
    """Represents a defined code symbol (class, function, method, variable).

    Attributes:
        name: Symbol identifier (e.g., 'Database', 'get_downstream_impact').
        kind: Kind of symbol ('class', 'function', 'method', 'variable', 'interface').
        line_number: 1-indexed line number where the symbol definition begins.
        end_line: 1-indexed line number where the symbol definition ends.
        start_byte: 0-indexed byte offset of symbol start in file.
        end_byte: 0-indexed byte offset of symbol end in file.
        parent_symbol: Name of enclosing class or namespace if nested.
        qualified_name: Dot-separated path (e.g., 'Database.get_downstream_impact').
        signature: Full parameter signature if applicable.
        return_type: Declared return type annotation if available.
        docstring: Cleaned docstring text if present.
        scope: Visibility ('public', 'private', 'dunder').
    """

    name: str
    kind: str
    line_number: int = 1
    end_line: int = 1
    start_byte: int = 0
    end_byte: int = 0
    parent_symbol: str | None = None
    qualified_name: str = ""
    signature: str | None = None
    return_type: str | None = None
    docstring: str | None = None
    scope: str = "public"

    def __post_init__(self) -> None:
        if not self.qualified_name:
            if self.parent_symbol:
                self.qualified_name = f"{self.parent_symbol}.{self.name}"
            else:
                self.qualified_name = self.name
        if self.end_line < self.line_number:
            self.end_line = self.line_number

    @property
    def start_line(self) -> int:
        return self.line_number


@dataclass
class CodeChunk:
    """Represents a dynamically extracted slice of source code for a symbol or block.

    Attributes:
        file_path: Repository-relative path to the source file.
        symbol_name: Identifier or qualified name of the code chunk.
        start_line: 1-indexed start line.
        end_line: 1-indexed end line.
        start_byte: 0-indexed start byte offset.
        end_byte: 0-indexed end byte offset.
        content: The actual source code slice.
        context_header: Informative header string for LLM or search context.
    """

    file_path: str
    symbol_name: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    content: str
    context_header: str = ""


def extract_chunk(content: str | bytes, symbol: Symbol, file_path: str = "") -> CodeChunk:
    """Extract code chunk dynamically using symbol start_byte and end_byte offsets."""
    if isinstance(content, str):
        raw_bytes = content.encode("utf-8")
        chunk_text = raw_bytes[symbol.start_byte:symbol.end_byte].decode("utf-8", errors="replace")
    else:
        chunk_text = content[symbol.start_byte:symbol.end_byte].decode("utf-8", errors="replace")

    header = (
        f"file: {file_path} | {symbol.kind} {symbol.qualified_name}"
        if file_path
        else f"{symbol.kind} {symbol.qualified_name}"
    )
    return CodeChunk(
        file_path=file_path,
        symbol_name=symbol.qualified_name,
        start_line=symbol.line_number,
        end_line=symbol.end_line,
        start_byte=symbol.start_byte,
        end_byte=symbol.end_byte,
        content=chunk_text,
        context_header=header,
    )
