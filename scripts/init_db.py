"""Apply SQL migrations in order. Safe to re-run: applied files are recorded in schema_migrations.

    python -m scripts.init_db                 apply any pending migrations
    python -m scripts.init_db --status        list applied / pending
    python -m scripts.init_db --reset --yes   DROP EVERYTHING in the public schema, then re-apply (dev only)
"""
import argparse
import hashlib
import sys

import psycopg

from app.config import settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reset", action="store_true", help="drop the public schema first (destroys all data)")
    ap.add_argument("--yes", action="store_true", help="confirm --reset")
    args = ap.parse_args()

    files = sorted(settings.migrations_dir.glob("*.sql"))
    if not files:
        print(f"No migration files in {settings.migrations_dir}")
        return 1

    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        info = conn.info
        print(f"Connected to {info.dbname} at {info.host}:{info.port} as {info.user}")
        if args.reset:
            if not args.yes:
                print("--reset destroys all data in this database. Re-run with --reset --yes to confirm.")
                return 2
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
            print("Dropped and recreated schema 'public'.")
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                          filename TEXT PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        applied = {r[0]: r[1] for r in conn.execute("SELECT filename, sha256 FROM schema_migrations")}
        for f in files:
            sha = hashlib.sha256(f.read_bytes()).hexdigest()
            if f.name in applied:
                note = "" if applied[f.name] == sha else "   WARNING: file changed since it was applied"
                print(f"  applied  {f.name}{note}")
                continue
            if args.status:
                print(f"  PENDING  {f.name}")
                continue
            print(f"  applying {f.name} ...", end=" ", flush=True)
            with conn.transaction():
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (filename, sha256) VALUES (%s, %s)", (f.name, sha))
            print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
