"""Start a local PostgreSQL server preloaded with the sample business data, to try the app against.

    cd backend
    venv\\Scripts\\python.exe scripts\\dev_postgres.py

No PostgreSQL installation or admin rights are needed (it uses the embedded server from
requirements-dev.txt). It prints the values to type into the "Connect a database" form, then runs
until you press Ctrl+C. Data is kept in data/dev-postgres between runs; the port changes each time.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.devtools import postgres_sample as ps  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)      # show the connection details at once, even when piped
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    default_dir = Path(__file__).resolve().parents[2] / "data" / "dev-postgres"
    parser.add_argument("--dir", type=Path, default=default_dir, help="where the server keeps its data")
    args = parser.parse_args()

    try:
        server = ps.start_embedded_server(args.dir)
    except ImportError:
        sys.exit("The embedded server is not installed. Run: pip install -r requirements-dev.txt")

    admin_uri = server.get_uri()
    ps.load_sample(admin_uri)
    ps.create_role(admin_uri, "analyst", password="analyst")      # read-only: SELECT on the shop schema only
    connection = ps.split_uri(admin_uri)

    print("\nPostgreSQL is running with the sample business data. In the app, open Data > Connect a database:\n")
    print(f"    Host      {connection['host']}")
    print(f"    Port      {connection['port']}")
    print(f"    Database  {connection['database']}")
    print(f"    Username  analyst        (a read-only role)")
    print(f"    Password  analyst")
    print(f"    Schema    {ps.SCHEMA}")
    print("    SSL mode  disable\n")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        server.cleanup()


if __name__ == "__main__":
    main()
