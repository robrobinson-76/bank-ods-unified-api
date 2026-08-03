"""Populate the legacy CRM Postgres database from the seeded ODS accounts.

The CRM is meant to be the SOURCE of the account master, so its rows must
correspond to what the ODS already holds — otherwise CDC curation would look
like it were inventing accounts. Deriving the rows from Mongo guarantees that
correspondence without duplicating the faker logic.

    python scripts/crm_seed.py           # create/refresh rows
    python scripts/crm_seed.py --reset   # truncate first

Values are written in the CRM's own vocabulary (lowercase code lists,
comma-joined tax residencies) — mapping them back to ODS enums is curation's
job, not the source's.
"""
from __future__ import annotations

import argparse
import sys

import psycopg
import pymongo

from ods_ingest import config

CLASSIFICATION = {
    "RETAIL": "retail",
    "PROFESSIONAL": "professional",
    "ELIGIBLE_COUNTERPARTY": "eligible_counterparty",
}


def _scalar(cur, sql: str) -> int:
    """Single-value query; a COUNT always returns a row."""
    cur.execute(sql)
    row = cur.fetchone()
    return int(row[0]) if row else 0


def load_accounts() -> list[dict]:
    client: pymongo.MongoClient = pymongo.MongoClient(config.MONGODB_URI)
    accounts = list(client[config.MONGODB_DB]["accounts"].find({}))
    client.close()
    if not accounts:
        raise SystemExit("No seeded accounts — run scripts/seed_data.py first")
    return accounts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="truncate before loading")
    parser.add_argument("--dsn", default=config.CRM_DSN)
    args = parser.parse_args(argv)

    accounts = load_accounts()
    clients: dict[str, dict] = {}
    for acct in accounts:
        client_master = acct["client"]
        clients.setdefault(client_master["clientId"], client_master)

    with psycopg.connect(args.dsn) as conn, conn.cursor() as cur:
        if args.reset:
            cur.execute("TRUNCATE accounts, clients RESTART IDENTITY CASCADE")

        # Parents first: accounts reference clients, and clients may reference
        # a parent client.
        ordered = sorted(clients.values(), key=lambda c: c.get("parentClientId") is not None)
        for c in ordered:
            cur.execute(
                """
                INSERT INTO clients (client_id, client_name, lei, country_domicile,
                                     country_incorp, tax_residencies, classification,
                                     kyc_status, risk_rating, legal_entity_type,
                                     parent_client_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_id) DO UPDATE SET
                    client_name = EXCLUDED.client_name,
                    kyc_status  = EXCLUDED.kyc_status,
                    updated_at  = now()
                """,
                (
                    c["clientId"], c["clientName"], c["lei"], c["countryOfDomicile"],
                    c["countryOfIncorporation"], ",".join(c["taxResidencies"]),
                    CLASSIFICATION.get(c["classification"], "retail"),
                    c["kycStatus"].lower(), c["riskRating"].lower(),
                    c["legalEntityType"].lower(), c.get("parentClientId"),
                ),
            )

        for acct in accounts:
            cur.execute(
                """
                INSERT INTO accounts (account_nbr, client_id, account_name, account_type,
                                      base_ccy, status, open_date, close_date, branch)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (account_nbr) DO UPDATE SET
                    account_name = EXCLUDED.account_name,
                    status       = EXCLUDED.status,
                    updated_at   = now()
                """,
                (
                    acct["accountId"], acct["client"]["clientId"], acct["accountName"],
                    acct["accountType"].lower(), acct["baseCurrency"],
                    acct["status"].lower(), acct["openDate"].date(),
                    acct["closeDate"].date() if acct.get("closeDate") else None,
                    acct["custodianBranch"],
                ),
            )
        conn.commit()

        n_clients = _scalar(cur, "SELECT count(*) FROM clients")
        n_accounts = _scalar(cur, "SELECT count(*) FROM accounts")

    print(f"CRM loaded: {n_clients} clients, {n_accounts} accounts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
