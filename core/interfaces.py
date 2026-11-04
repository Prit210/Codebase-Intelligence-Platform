"""Abstract base interfaces for language extractors and path resolvers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.models import RawImport, ResolvedTarget, Symbol


class BaseExtractor(ABC):
    """Abstract interface for language-specific AST/syntax extractors.

    An extractor operates purely on file content and does not interact with the filesystem.
    It produces raw import tokens and symbol definitions.
    """

    @abstractmethod
    def extract_imports(self, file_content: str | bytes, file_path: str) -> list[RawImport]:
        """Extract import statements from file content into RawImport tokens.

        Args:
            file_content: Raw source code as text or bytes.
            file_path: Relative or absolute path to the source file (for error reporting / context).

        Returns:
            List of extracted RawImport records.
        """
        ...

    @abstractmethod
    def extract_symbols(self, file_content: str | bytes, file_path: str) -> list[Symbol]:
        """Extract symbol definitions (classes, functions, etc.) from file content.

        Args:
            file_content: Raw source code as text or bytes.
            file_path: Relative or absolute path to the source file.

        Returns:
            List of extracted Symbol records.
        """
        ...


class BaseResolver(ABC):
    """Abstract interface for language-specific import path resolvers.

    A resolver functions as the semantic linker. It accepts raw import tokens
    and determines the concrete repository-relative file path or flags the import as external.
    """

    @abstractmethod
    def resolve_import(
        self,
        raw_import: RawImport,
        source_file_path: str,
        repo_root: str,
        all_project_files: set[str] | None = None,
    ) -> ResolvedTarget:
        """Resolve a raw import token into a concrete target file or external package.

        Args:
            raw_import: The raw import token to resolve.
            source_file_path: Normalized path of the file containing the import.
            repo_root: Absolute or normalized root path of the repository.
            all_project_files: Optional set of all known normalized file paths in the repo.

        Returns:
            A ResolvedTarget with target_path (if internal) or is_external=True.
        """
        ...
