-- psql -X -v ON_ERROR_STOP=1 -v target_database=radar_beta -f deploy/roles.sql
-- Owner only, on a NEW migrated database. Existing roles are intentionally refused.
BEGIN;
SELECT set_config('radar.expected_database', :'target_database', true);
DO $$ BEGIN
  IF current_database() <> current_setting('radar.expected_database') THEN
    RAISE EXCEPTION 'Wrong database; no grants applied';
  END IF;
  IF (SELECT array_agg(version_num::text) FROM public.alembic_version)
      IS DISTINCT FROM ARRAY['0006'] THEN
    RAISE EXCEPTION 'Expected migration 0006';
  END IF;
END $$;
CREATE ROLE radar_public LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 4;
CREATE ROLE github_radar_collector LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO radar_public, github_radar_collector',
              current_database()) \gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO radar_public, github_radar_collector;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
GRANT SELECT ON public.alembic_version, public.developers, public.repositories, public.topics,
  public.repository_topics, public.repository_contributors, public.developer_snapshots,
  public.repository_contributor_snapshots, public.repository_snapshots TO radar_public;
GRANT SELECT ON public.alembic_version, public.developers, public.repositories, public.topics,
  public.repository_topics, public.repository_contributors, public.developer_snapshots,
  public.repository_contributor_snapshots, public.repository_snapshots,
  public.discovery_provenance, public.collection_runs TO github_radar_collector;
GRANT INSERT, UPDATE ON public.repositories, public.repository_snapshots, public.topics,
  public.discovery_provenance, public.collection_runs TO github_radar_collector;
GRANT INSERT, DELETE ON public.repository_topics TO github_radar_collector;
GRANT USAGE ON SEQUENCE public.repositories_id_seq, public.repository_snapshots_id_seq,
  public.topics_id_seq TO github_radar_collector;
ALTER ROLE radar_public SET default_transaction_read_only = on;
ALTER ROLE radar_public SET statement_timeout = '5s';
ALTER ROLE radar_public SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE github_radar_collector SET statement_timeout = '15s';
ALTER ROLE github_radar_collector SET idle_in_transaction_session_timeout = '30s';
COMMIT;
-- Set passwords interactively with \password; never put them in this file.
