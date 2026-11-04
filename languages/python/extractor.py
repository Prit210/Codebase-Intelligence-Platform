"""Python syntax extractor using Tree-sitter for imports, symbols, and docstrings."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import tree_sitter
import tree_sitter_python

from core.interfaces import BaseExtractor
from core.models import RawImport, Symbol


def _clean_docstring(raw_text: str) -> str:
    """Strip string literal quote delimiters and leading/trailing whitespace."""
    raw = raw_text.strip()
    for quote in ('"""', "'''", '"', "'"):
        if raw.startswith(quote) and raw.endswith(quote) and len(raw) >= 2 * len(quote):
            return raw[len(quote) : -len(quote)].strip()
    return raw


def _determine_scope(name: str) -> str:
    """Determine scope classification for a Python identifier."""
    if name.startswith("__") and name.endswith("__"):
        return "dunder"
    if name.startswith("_"):
        return "private"
    return "public"


class PythonExtractor(BaseExtractor):
    """Tree-sitter based syntax extractor for Python source files."""

    def __init__(self, queries_path: str | Path | None = None) -> None:
        """Initialize the Tree-sitter Python language, parser, and queries."""
        self.language = tree_sitter.Language(tree_sitter_python.language())
        self.parser = tree_sitter.Parser(self.language)

        if queries_path is None:
            queries_path = Path(__file__).parent / "queries.scm"

        self.queries_path = Path(queries_path)
        if self.queries_path.exists():
            query_content = self.queries_path.read_text(encoding="utf-8")
            self.query = tree_sitter.Query(self.language, query_content)
        else:
            self.query = None

    def _is_type_checking(self, node: tree_sitter.Node) -> bool:
        """Check if an AST node is enclosed in an `if TYPE_CHECKING:` guard."""
        curr = node.parent
        while curr:
            if curr.type == "if_statement":
                cond = curr.child_by_field_name("condition")
                if cond and b"TYPE_CHECKING" in cond.text:
                    return True
            curr = curr.parent
        return False

    def extract_imports(self, file_content: str | bytes, file_path: str = "") -> list[RawImport]:
        """Extract all import statements into structured RawImport tokens.

        Args:
            file_content: Raw Python source code as text or bytes.
            file_path: Relative or absolute path to the source file.

        Returns:
            List of extracted RawImport records.
        """
        if isinstance(file_content, str):
            content_bytes = file_content.encode("utf-8")
        else:
            content_bytes = file_content

        tree = self.parser.parse(content_bytes)
        imports: list[RawImport] = []

        def _traverse(node: tree_sitter.Node) -> None:
            if node.type == "import_statement":
                # e.g., import os, sys as s, math.sin
                line_no = node.start_point[0] + 1
                is_tc = self._is_type_checking(node)

                for child in node.children:
                    if child.type == "dotted_name":
                        mod_name = child.text.decode("utf-8", errors="replace")
                        imports.append(
                            RawImport(
                                module=mod_name,
                                names=[],
                                level=0,
                                line_number=line_no,
                                is_type_checking=is_tc,
                            )
                        )
                    elif child.type == "aliased_import":
                        name_node = child.child_by_field_name("name")
                        alias_node = child.child_by_field_name("alias")
                        if name_node and alias_node:
                            mod_name = name_node.text.decode("utf-8", errors="replace")
                            alias = alias_node.text.decode("utf-8", errors="replace")
                            imports.append(
                                RawImport(
                                    module=mod_name,
                                    names=[(mod_name, alias)],
                                    level=0,
                                    line_number=line_no,
                                    is_type_checking=is_tc,
                                )
                            )
                return

            elif node.type == "import_from_statement":
                # e.g., from foo.bar import baz as b, qux, *
                # e.g., from ..pkg.sub import something
                # e.g., from . import sibling
                line_no = node.start_point[0] + 1
                is_tc = self._is_type_checking(node)

                module_name = ""
                level = 0
                names: list[tuple[str, str | None]] = []

                # Find module / relative prefix and names
                is_after_import_kw = False
                for child in node.children:
                    if child.type == "from":
                        continue
                    elif child.type == "import":
                        is_after_import_kw = True
                        continue

                    if not is_after_import_kw:
                        if child.type == "dotted_name":
                            module_name = child.text.decode("utf-8", errors="replace")
                            level = 0
                        elif child.type == "relative_import":
                            # e.g. . or .. or ..parent.sub
                            prefix_node = None
                            sub_node = None
                            for sub in child.children:
                                if sub.type == "import_prefix":
                                    prefix_node = sub
                                elif sub.type == "dotted_name":
                                    sub_node = sub

                            if prefix_node:
                                level = len(prefix_node.text.decode("utf-8", errors="replace"))
                            else:
                                level = 1

                            if sub_node:
                                module_name = sub_node.text.decode("utf-8", errors="replace")
                            else:
                                module_name = ""
                    else:
                        # Extract imported names
                        if child.type == "dotted_name":
                            name_str = child.text.decode("utf-8", errors="replace")
                            names.append((name_str, None))
                        elif child.type == "aliased_import":
                            name_node = child.child_by_field_name("name")
                            alias_node = child.child_by_field_name("alias")
                            if name_node and alias_node:
                                n_str = name_node.text.decode("utf-8", errors="replace")
                                a_str = alias_node.text.decode("utf-8", errors="replace")
                                names.append((n_str, a_str))
                        elif child.type == "wildcard_import":
                            names.append(("*", None))

                imports.append(
                    RawImport(
                        module=module_name,
                        names=names,
                        level=level,
                        line_number=line_no,
                        is_type_checking=is_tc,
                    )
                )
                return

            # Continue traversing child nodes (e.g. into if_statements, try_except, functions)
            for child in node.children:
                if child.is_named:
                    _traverse(child)

        _traverse(tree.root_node)
        return imports

    def extract_symbols(self, file_content: str | bytes, file_path: str = "") -> list[Symbol]:
        """Extract all classes, functions, and methods with rich metadata.

        Captures signatures, return types, docstrings, exact byte & line ranges,
        parent hierarchies, and scope classification.

        Args:
            file_content: Raw source code as text or bytes.
            file_path: Relative or absolute path to the source file.

        Returns:
            List of Symbol records in topological order of definition.
        """
        if isinstance(file_content, str):
            content_bytes = file_content.encode("utf-8")
        else:
            content_bytes = file_content

        tree = self.parser.parse(content_bytes)
        symbols: list[Symbol] = []

        def _extract_docstring(body_node: tree_sitter.Node | None) -> str | None:
            if not body_node:
                return None
            for child in body_node.named_children:
                if child.type == "expression_statement":
                    for sub in child.named_children:
                        if sub.type == "string":
                            raw = sub.text.decode("utf-8", errors="replace")
                            return _clean_docstring(raw)
                    break
                else:
                    # First statement is not an expression statement
                    break
            return None

        def _walk(
            node: tree_sitter.Node,
            parent_name: str | None = None,
            parent_qname: str = "",
            parent_is_class: bool = False,
        ) -> None:
            for child in node.children:
                if not child.is_named:
                    continue

                if child.type == "class_definition":
                    name_node = child.child_by_field_name("name")
                    if not name_node:
                        continue
                    class_name = name_node.text.decode("utf-8", errors="replace")
                    qname = f"{parent_qname}.{class_name}" if parent_qname else class_name

                    # Superclasses for signature
                    super_node = child.child_by_field_name("superclasses")
                    sig = super_node.text.decode("utf-8", errors="replace") if super_node else None

                    body_node = child.child_by_field_name("body")
                    doc = _extract_docstring(body_node)

                    sym = Symbol(
                        name=class_name,
                        kind="class",
                        line_number=child.start_point[0] + 1,
                        end_line=child.end_point[0] + 1,
                        start_byte=child.start_byte,
                        end_byte=child.end_byte,
                        parent_symbol=parent_name,
                        qualified_name=qname,
                        signature=sig,
                        return_type=None,
                        docstring=doc,
                        scope=_determine_scope(class_name),
                    )
                    symbols.append(sym)

                    # Walk methods/nested classes inside body
                    if body_node:
                        _walk(body_node, parent_name=class_name, parent_qname=qname, parent_is_class=True)

                elif child.type == "function_definition":
                    name_node = child.child_by_field_name("name")
                    if not name_node:
                        continue
                    fn_name = name_node.text.decode("utf-8", errors="replace")
                    qname = f"{parent_qname}.{fn_name}" if parent_qname else fn_name
                    kind = "method" if parent_is_class else "function"

                    params_node = child.child_by_field_name("parameters")
                    params_str = (
                        params_node.text.decode("utf-8", errors="replace")
                        if params_node
                        else "()"
                    )

                    ret_node = child.child_by_field_name("return_type")
                    ret_str = (
                        ret_node.text.decode("utf-8", errors="replace")
                        if ret_node
                        else None
                    )

                    # Check for async
                    is_async = any(c.type == "async" for c in child.children)
                    prefix = "async def " if is_async else "def "
                    full_sig = f"{prefix}{fn_name}{params_str}"
                    if ret_str:
                        full_sig = f"{full_sig} -> {ret_str}"

                    body_node = child.child_by_field_name("body")
                    doc = _extract_docstring(body_node)

                    sym = Symbol(
                        name=fn_name,
                        kind=kind,
                        line_number=child.start_point[0] + 1,
                        end_line=child.end_point[0] + 1,
                        start_byte=child.start_byte,
                        end_byte=child.end_byte,
                        parent_symbol=parent_name,
                        qualified_name=qname,
                        signature=full_sig,
                        return_type=ret_str,
                        docstring=doc,
                        scope=_determine_scope(fn_name),
                    )
                    symbols.append(sym)

                    # Walk nested functions/classes inside function body
                    if body_node:
                        _walk(body_node, parent_name=fn_name, parent_qname=qname, parent_is_class=False)

                else:
                    # Recurse into blocks, if_statements, try blocks, etc.
                    _walk(
                        child,
                        parent_name=parent_name,
                        parent_qname=parent_qname,
                        parent_is_class=parent_is_class,
                    )

        _walk(tree.root_node)
        return symbols
