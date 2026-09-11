#!/bin/bash
# Create the Temporal databases inside the shared Postgres instance (auto-setup creates schemas).
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    CREATE DATABASE temporal;
    CREATE DATABASE temporal_visibility;
    CREATE EXTENSION IF NOT EXISTS vector;
SQL
