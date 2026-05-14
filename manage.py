#!/usr/bin/env python
"""Utilitario de linha de comando do Django para tarefas administrativas."""

import os
import sys


def main():
    """Define o modulo de configuracoes e repassa o comando para o Django."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "finanpy.settings")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Nao foi possivel importar o Django. Verifique se ele esta instalado "
            "e se o ambiente virtual foi ativado corretamente."
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
