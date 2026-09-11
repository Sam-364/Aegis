"""PostgreSQL adapters: SQLAlchemy 2.0 async models, repositories and unit of work.

Storage pattern: every aggregate is stored as a JSONB ``document`` plus projected scalar columns
for the fields we filter, join, order or constrain on. The document is the source of truth and is
validated through the domain model on read; projections carry the indexes and constraints.
"""
