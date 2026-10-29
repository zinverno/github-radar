"""Fail closed on effective application privileges, including PUBLIC and column grants."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PUBLIC_TABLES = (
    "alembic_version",
    "developers",
    "repositories",
    "topics",
    "repository_topics",
    "repository_contributors",
    "developer_snapshots",
    "repository_contributor_snapshots",
    "repository_snapshots",
)
COLLECTOR_WRITE = (
    "repositories",
    "repository_snapshots",
    "topics",
    "discovery_provenance",
    "collection_runs",
)
COLLECTOR_SEQUENCES = {"repositories_id_seq", "repository_snapshots_id_seq", "topics_id_seq"}
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


async def audit_application_role(session: AsyncSession, *, collector: bool = False) -> None:
    """No mutations. Unknown relations, memberships, functions and defaults require review."""
    expected = "github_radar_collector" if collector else "radar_public"
    safe = await session.scalar(
        text("""
        SELECT rolname = :expected
          AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
          AND NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member = r.oid)
          AND NOT has_database_privilege(current_user, current_database(), 'CREATE,TEMP')
          AND NOT EXISTS (SELECT 1 FROM pg_database WHERE datdba = r.oid)
          AND NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = r.oid)
          AND NOT EXISTS (SELECT 1 FROM pg_class WHERE relowner = r.oid)
          AND NOT EXISTS (SELECT 1 FROM pg_proc WHERE proowner = r.oid)
          AND NOT EXISTS (SELECT 1 FROM pg_type WHERE typowner = r.oid)
          AND NOT EXISTS (
            SELECT 1 FROM pg_namespace n
            WHERE left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema'
              AND has_schema_privilege(current_user, n.oid, 'CREATE'))
          AND NOT EXISTS (
            SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema'
              AND has_function_privilege(current_user, p.oid, 'EXECUTE'))
          AND NOT EXISTS (
            SELECT 1 FROM pg_default_acl d, LATERAL aclexplode(d.defaclacl) a
            WHERE a.grantee IN (0, r.oid))
          AND NOT EXISTS (
            SELECT 1 FROM pg_class c, LATERAL aclexplode(c.relacl) a
            WHERE a.grantee IN (0, r.oid) AND a.is_grantable)
          AND NOT EXISTS (
            SELECT 1 FROM pg_attribute c, LATERAL aclexplode(c.attacl) a
            WHERE a.grantee IN (0, r.oid) AND a.is_grantable)
          AND NOT EXISTS (
            SELECT 1 FROM pg_namespace n, LATERAL aclexplode(n.nspacl) a
            WHERE a.grantee IN (0, r.oid) AND a.is_grantable)
          AND NOT EXISTS (
            SELECT 1 FROM pg_database d, LATERAL aclexplode(d.datacl) a
            WHERE a.grantee IN (0, r.oid) AND a.is_grantable)
          AND NOT EXISTS (
            SELECT 1 FROM pg_parameter_acl p, LATERAL aclexplode(p.paracl) a
            WHERE a.grantee IN (0, r.oid))
        FROM pg_roles r WHERE rolname = current_user
    """),
        {"expected": expected},
    )
    if not safe:
        raise ValueError("Application role has administrative, inherited or unreviewed privileges")

    allowed = {name: {"SELECT"} for name in PUBLIC_TABLES}
    if collector:
        allowed.update({name: {"SELECT", "INSERT", "UPDATE"} for name in COLLECTOR_WRITE})
        allowed["repository_topics"] = {"SELECT", "INSERT", "DELETE"}
    privileges = list(PRIVILEGES)
    version = await session.scalar(text("SELECT current_setting('server_version_num')::int"))
    if version >= 170000:
        privileges.append("MAINTAIN")
    rows = await session.execute(
        text("""
        SELECT n.nspname, c.relname, c.relkind::text, p.privilege,
          has_table_privilege(current_user, c.oid, p.privilege) AS table_grant,
          CASE WHEN p.privilege IN ('SELECT','INSERT','UPDATE','REFERENCES')
            THEN has_any_column_privilege(current_user, c.oid, p.privilege)
            ELSE false END AS column_grant
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(CAST(:privileges AS text[])) AS p(privilege)
        WHERE left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema'
          AND c.relkind IN ('r','v','m','f','p')
    """),
        {"privileges": privileges},
    )
    found = set()
    for schema, name, kind, privilege, table_grant, column_grant in rows:
        required = privilege in allowed.get(name, set()) and schema == "public" and kind == "r"
        if required:
            found.add(name)
        if (required and not table_grant) or (not required and (table_grant or column_grant)):
            raise ValueError("Application table privileges differ from the reviewed allowlist")
    if found != set(allowed):
        raise ValueError("Application tables are missing")

    sequences = await session.execute(
        text("""
        SELECT n.nspname, c.relname,
          has_sequence_privilege(current_user, c.oid, 'USAGE'),
          has_sequence_privilege(current_user, c.oid, 'SELECT,UPDATE')
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'S'
          AND left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema'
    """)
    )
    found_sequences = set()
    for schema, name, usage, other in sequences:
        required = collector and schema == "public" and name in COLLECTOR_SEQUENCES
        if required:
            found_sequences.add(name)
        if usage != required or other:
            raise ValueError("Application sequence privileges differ from the reviewed allowlist")
    if collector and found_sequences != COLLECTOR_SEQUENCES:
        raise ValueError("Collection sequences are missing")
