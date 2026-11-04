"""Unit tests for PythonExtractor and LanguageRegistry."""

from __future__ import annotations

import unittest

from core.models import extract_chunk
from languages.python.extractor import PythonExtractor
from languages.registry import LanguageRegistry, default_registry


class TestPythonExtractor(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = PythonExtractor()

    def test_standard_imports(self) -> None:
        code = """
import os
import sys, math as m
"""
        imports = self.extractor.extract_imports(code, "test.py")
        self.assertEqual(len(imports), 3)

        self.assertEqual(imports[0].module, "os")
        self.assertEqual(imports[0].level, 0)
        self.assertEqual(imports[0].names, [])
        self.assertFalse(imports[0].is_type_checking)

        self.assertEqual(imports[1].module, "sys")
        self.assertEqual(imports[1].level, 0)

        self.assertEqual(imports[2].module, "math")
        self.assertEqual(imports[2].names, [("math", "m")])

    def test_from_imports(self) -> None:
        code = """
from pathlib import Path, PurePath as PP
from typing import (
    Dict,
    List as MyList,
)
"""
        imports = self.extractor.extract_imports(code, "test.py")
        self.assertEqual(len(imports), 2)

        self.assertEqual(imports[0].module, "pathlib")
        self.assertEqual(imports[0].names, [("Path", None), ("PurePath", "PP")])
        self.assertEqual(imports[0].level, 0)

        self.assertEqual(imports[1].module, "typing")
        self.assertEqual(imports[1].names, [("Dict", None), ("List", "MyList")])

    def test_relative_and_wildcard_imports(self) -> None:
        code = """
from . import sibling
from ..parent.sub import child as c
from ... import ancestor
from foo.bar import *
"""
        imports = self.extractor.extract_imports(code, "pkg/module.py")
        self.assertEqual(len(imports), 4)

        # from . import sibling
        self.assertEqual(imports[0].level, 1)
        self.assertEqual(imports[0].module, "")
        self.assertEqual(imports[0].names, [("sibling", None)])

        # from ..parent.sub import child as c
        self.assertEqual(imports[1].level, 2)
        self.assertEqual(imports[1].module, "parent.sub")
        self.assertEqual(imports[1].names, [("child", "c")])

        # from ... import ancestor
        self.assertEqual(imports[2].level, 3)
        self.assertEqual(imports[2].module, "")
        self.assertEqual(imports[2].names, [("ancestor", None)])

        # wildcard import
        self.assertEqual(imports[3].level, 0)
        self.assertEqual(imports[3].module, "foo.bar")
        self.assertEqual(imports[3].names, [("*", None)])

    def test_type_checking_guarded_imports(self) -> None:
        code = """
import regular_module

if TYPE_CHECKING:
    import heavy_type
    from typing_extensions import Protocol
"""
        imports = self.extractor.extract_imports(code, "test.py")
        self.assertEqual(len(imports), 3)

        self.assertFalse(imports[0].is_type_checking)
        self.assertTrue(imports[1].is_type_checking)
        self.assertTrue(imports[2].is_type_checking)

    def test_symbol_extraction_and_docstrings(self) -> None:
        code = '''
"""Module docstring."""

class Engine(Machine, Runnable):
    """Core computational engine."""

    def __init__(self, mode: str = "fast") -> None:
        """Initialize engine."""
        self.mode = mode

    async def _compute_internal(self, x: int) -> int:
        return x * 2

def standalone_func(val: float) -> bool:
    def nested_func():
        pass
    return val > 0.0
'''
        symbols = self.extractor.extract_symbols(code, "engine.py")
        self.assertEqual(len(symbols), 5)

        # 1. Class Engine
        s_class = symbols[0]
        self.assertEqual(s_class.name, "Engine")
        self.assertEqual(s_class.kind, "class")
        self.assertEqual(s_class.signature, "(Machine, Runnable)")
        self.assertEqual(s_class.docstring, "Core computational engine.")
        self.assertEqual(s_class.scope, "public")
        self.assertIsNone(s_class.parent_symbol)
        self.assertEqual(s_class.qualified_name, "Engine")

        # 2. Method __init__
        s_init = symbols[1]
        self.assertEqual(s_init.name, "__init__")
        self.assertEqual(s_init.kind, "method")
        self.assertEqual(s_init.parent_symbol, "Engine")
        self.assertEqual(s_init.qualified_name, "Engine.__init__")
        self.assertEqual(s_init.scope, "dunder")
        self.assertEqual(s_init.docstring, "Initialize engine.")
        self.assertEqual(s_init.signature, 'def __init__(self, mode: str = "fast") -> None')

        # 3. Async Method _compute_internal
        s_method = symbols[2]
        self.assertEqual(s_method.name, "_compute_internal")
        self.assertEqual(s_method.kind, "method")
        self.assertEqual(s_method.parent_symbol, "Engine")
        self.assertEqual(s_method.qualified_name, "Engine._compute_internal")
        self.assertEqual(s_method.scope, "private")
        self.assertEqual(s_method.signature, "async def _compute_internal(self, x: int) -> int")

        # 4. Standalone function
        s_func = symbols[3]
        self.assertEqual(s_func.name, "standalone_func")
        self.assertEqual(s_func.kind, "function")
        self.assertEqual(s_func.scope, "public")
        self.assertEqual(s_func.signature, "def standalone_func(val: float) -> bool")

        # 5. Nested function
        s_nested = symbols[4]
        self.assertEqual(s_nested.name, "nested_func")
        self.assertEqual(s_nested.kind, "function")
        self.assertEqual(s_nested.parent_symbol, "standalone_func")
        self.assertEqual(s_nested.qualified_name, "standalone_func.nested_func")

    def test_dynamic_code_chunk_slicing(self) -> None:
        code = """def calculate(a: int, b: int) -> int:
    return a + b
"""
        symbols = self.extractor.extract_symbols(code, "math_utils.py")
        self.assertEqual(len(symbols), 1)

        chunk = extract_chunk(code, symbols[0], "math_utils.py")
        self.assertEqual(chunk.file_path, "math_utils.py")
        self.assertEqual(chunk.symbol_name, "calculate")
        self.assertEqual(chunk.start_line, 1)
        self.assertEqual(chunk.end_line, 2)
        self.assertEqual(chunk.content.strip(), "def calculate(a: int, b: int) -> int:\n    return a + b")

    def test_fault_tolerant_parsing(self) -> None:
        # Half-written or syntax-broken code
        broken_code = """
import os
from pathlib import

class BrokenClass
    def intact_method(self):
        return 42
"""
        # Should not crash and should still extract valid constructs
        imports = self.extractor.extract_imports(broken_code, "broken.py")
        self.assertTrue(any(i.module == "os" for i in imports))

        symbols = self.extractor.extract_symbols(broken_code, "broken.py")
        self.assertTrue(any(s.name == "intact_method" for s in symbols))

    def test_language_registry_dispatch(self) -> None:
        ext_py = default_registry.get_extractor_for_file("core/db.py")
        self.assertIsNotNone(ext_py)
        self.assertIsInstance(ext_py, PythonExtractor)

        ext_pyi = default_registry.get_extractor_for_file("types/stubs.pyi")
        self.assertIsNotNone(ext_pyi)
        self.assertIsInstance(ext_pyi, PythonExtractor)

        ext_unknown = default_registry.get_extractor_for_file("app.rs")
        self.assertIsNone(ext_unknown)


if __name__ == "__main__":
    unittest.main()
