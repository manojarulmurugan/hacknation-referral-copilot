"""Pull the facilities table from Databricks SQL Warehouse into a local parquet file.

Usage:
    python pipeline/pull_facilities.py

Reads connection details from .env (DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH,
DATABRICKS_ACCESS_TOKEN). Output goes to data/facilities_local.parquet (gitignored).
"""

import os
from pathlib import Path

from databricks import sql
from dotenv import load_dotenv

load_dotenv()

SERVER_HOSTNAME = os.environ["DATABRICKS_SERVER_HOSTNAME"]
HTTP_PATH = os.environ["DATABRICKS_HTTP_PATH"]
ACCESS_TOKEN = os.environ["DATABRICKS_ACCESS_TOKEN"]

TABLE = "`databricks_virtue_foundation_dataset_dais_2026`.`virtue_foundation_dataset`.`facilities`"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "facilities_local.parquet"


def main() -> None:
    connection = sql.connect(
        server_hostname=SERVER_HOSTNAME,
        http_path=HTTP_PATH,
        access_token=ACCESS_TOKEN,
    )
    try:
        cursor = connection.cursor()
        cursor.execute(f"SELECT * FROM {TABLE}")
        df = cursor.fetchall_arrow().to_pandas()
    finally:
        connection.close()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT_PATH)
    print(f"Wrote {len(df)} rows x {len(df.columns)} cols to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
