"""Shared PEP 562 lazy-export helper for package ``__init__`` modules.

Stdlib only, and it must never import a ``server`` subpackage, so any package
can use it without creating an import cycle.
"""

from collections.abc import Callable, Mapping, MutableMapping
from importlib import import_module


def lazy_exports(
    namespace: MutableMapping[str, object],
    exports: Mapping[str, str],
) -> tuple[Callable[[str], object], Callable[[], list[str]]]:
    """Return module ``(__getattr__, __dir__)`` for a table of lazy exports.

    ``namespace`` is the package's ``globals()``; ``exports`` maps each exported
    name to the relative submodule that defines it. Resolved values are cached
    in ``namespace`` so ``__getattr__`` runs once per name.
    """
    package = str(namespace["__name__"])

    def __getattr__(name: str) -> object:
        module_name = exports.get(name)
        if module_name is None:
            raise AttributeError(f"module {package!r} has no attribute {name!r}")
        value = getattr(import_module(module_name, package), name)
        namespace[name] = value
        return value

    def __dir__() -> list[str]:
        return sorted({*namespace, *exports})

    return __getattr__, __dir__
