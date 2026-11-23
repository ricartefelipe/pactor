"""Ponto de entrada do subprocesso de inspeção de arquivos.

Uso interno: ``python -I -m pactor.files.sandbox <pdf|docx> <caminho>``.
Antes de ler o arquivo, o processo reduz os próprios limites: tempo de CPU,
memória endereçável, tamanho de arquivos que pode escrever (zero) e número de
descritores. O resultado sai em JSON pela saída padrão.
"""

from __future__ import annotations

import json
import resource
import sys
from pathlib import Path

from pactor.files.inspection import FileKind, inspect

CPU_SECONDS = 30
MEMORY_BYTES = 1024 * 1024 * 1024
OPEN_FILES = 64


def _limit() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (OPEN_FILES, OPEN_FILES))


def main(argv: list[str]) -> int:
    _limit()
    kind, path = FileKind(argv[1]), Path(argv[2])
    result = inspect(kind, path.read_bytes())
    sys.stdout.write(
        json.dumps(
            {
                "ok": result.ok,
                "reason": result.reason.value if result.reason else None,
                "detail": result.detail,
                "pages": result.pages,
                "text": result.text,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
