"""Responsibilities: Provide the Django management command entry point.
Implementation: Select the settings module and delegate command arguments to Django unchanged.
Related Modules: config.settings supplies application configuration; Django management handles
commands.
Declaration Index:
- main: Set the settings module and invoke Django's command-line dispatcher.
Variable Index:
None
"""

import os
import sys


def main():
    """Functionality: Configure Django settings and delegate the management command.
    Inputs: Command arguments from sys.argv and the process environment.
    Outputs: Returns through Django's command dispatcher.
    Logic: Set DJANGO_SETTINGS_MODULE only when absent, then pass sys.argv unchanged.
    Constraints: Does not alter command arguments or overwrite an existing environment setting.
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
