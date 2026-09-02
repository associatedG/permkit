#!/usr/bin/env python
"""Run the dummy domain, so the admin UI can actually be opened.

    PERMKIT_DB=demo.sqlite3 python manage.py migrate
    PERMKIT_DB=demo.sqlite3 python manage.py permkit_sync
    PERMKIT_DB=demo.sqlite3 python manage.py seed_dummy_roles
    PERMKIT_DB=demo.sqlite3 python manage.py runserver

Without ``PERMKIT_DB`` the database is in memory, which is right for the test
suite and useless for a server.
"""

import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
