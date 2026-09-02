#! /usr/bin/env bash

set -e
set -x

# The database itself is provisioned separately. This command waits for it,
# applies migrations, and creates the first superuser.
python app/backend_pre_start.py
alembic upgrade head
python app/initial_data.py
