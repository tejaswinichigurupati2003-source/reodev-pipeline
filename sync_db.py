"""Push the pipeline's results (data/) to the dashboard database (Neon if DATABASE_URL is set).

    python sync_db.py
"""
import db
import discover
import export


def main():
    rows = export.build_rows(discover.load_accounts())
    n = db.sync(rows)
    print(f"Synced {n} lead rows to {'Neon (DATABASE_URL)' if db.configured() else 'local SQLite (data/local.db)'}.")


if __name__ == "__main__":
    main()
