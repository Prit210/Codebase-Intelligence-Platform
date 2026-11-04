;; Tree-sitter query patterns for Python extraction

;; Import statements
(import_statement) @import.statement
(import_from_statement) @import_from.statement

;; Class definitions
(class_definition
  name: (identifier) @class.name) @class.def

;; Function and method definitions
(function_definition
  name: (identifier) @function.name) @function.def
