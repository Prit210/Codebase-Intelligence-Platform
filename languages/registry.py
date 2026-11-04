"""Language plugin discovery and registry system."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from core.interfaces import BaseExtractor, BaseResolver


class LanguageRegistry:
    """Registry managing language extractors and path resolvers."""

    def __init__(self) -> None:
        self._extractors_by_lang: dict[str, Callable[[], BaseExtractor]] = {}
        self._extractors_by_ext: dict[str, Callable[[], BaseExtractor]] = {}
        self._resolvers_by_lang: dict[str, Callable[[], BaseResolver]] = {}
        self._resolvers_by_ext: dict[str, Callable[[], BaseResolver]] = {}
        self._instances: dict[str, BaseExtractor | BaseResolver] = {}

    def register_extractor(
        self,
        language: str,
        extensions: list[str],
        factory: Callable[[], BaseExtractor],
    ) -> None:
        """Register an extractor factory for a language and its file extensions."""
        lang_key = language.lower()
        self._extractors_by_lang[lang_key] = factory
        for ext in extensions:
            ext_key = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            self._extractors_by_ext[ext_key] = factory

    def register_resolver(
        self,
        language: str,
        extensions: list[str],
        factory: Callable[[], BaseResolver],
    ) -> None:
        """Register a resolver factory for a language and its file extensions."""
        lang_key = language.lower()
        self._resolvers_by_lang[lang_key] = factory
        for ext in extensions:
            ext_key = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            self._resolvers_by_ext[ext_key] = factory

    def get_extractor_for_file(self, file_path: str | Path) -> BaseExtractor | None:
        """Retrieve an extractor instance for a given file path based on its extension."""
        ext = Path(file_path).suffix.lower()
        factory = self._extractors_by_ext.get(ext)
        if factory is None:
            return None
        cache_key = f"extractor:{ext}"
        if cache_key not in self._instances:
            self._instances[cache_key] = factory()
        return self._instances[cache_key]  # type: ignore[return-value]

    def get_resolver_for_file(self, file_path: str | Path) -> BaseResolver | None:
        """Retrieve a resolver instance for a given file path based on its extension."""
        ext = Path(file_path).suffix.lower()
        factory = self._resolvers_by_ext.get(ext)
        if factory is None:
            return None
        cache_key = f"resolver:{ext}"
        if cache_key not in self._instances:
            self._instances[cache_key] = factory()
        return self._instances[cache_key]  # type: ignore[return-value]

    def get_extractor(self, language: str) -> BaseExtractor | None:
        """Retrieve an extractor instance by language name (e.g. 'python')."""
        factory = self._extractors_by_lang.get(language.lower())
        if factory is None:
            return None
        cache_key = f"extractor_lang:{language.lower()}"
        if cache_key not in self._instances:
            self._instances[cache_key] = factory()
        return self._instances[cache_key]  # type: ignore[return-value]

    def get_resolver(self, language: str) -> BaseResolver | None:
        """Retrieve a resolver instance by language name."""
        factory = self._resolvers_by_lang.get(language.lower())
        if factory is None:
            return None
        cache_key = f"resolver_lang:{language.lower()}"
        if cache_key not in self._instances:
            self._instances[cache_key] = factory()
        return self._instances[cache_key]  # type: ignore[return-value]


# Global singleton registry
default_registry = LanguageRegistry()


def _init_default_plugins() -> None:
    """Lazy initialize built-in plugins."""
    try:
        from languages.python.extractor import PythonExtractor

        default_registry.register_extractor(
            language="python",
            extensions=[".py", ".pyi"],
            factory=PythonExtractor,
        )
    except ImportError:
        pass


_init_default_plugins()
