"""Generate change traffic in the legacy CRM.

Named, deterministic scenarios so a test (or a person watching the pipeline)
can assert a specific downstream outcome:

    python scripts/crm_mutate.py new-client        # insert a client + 2 accounts
    python scripts/crm_mutate.py kyc-flip          # update a client's kyc_status
    python scripts/crm_mutate.py close-account     # status -> closed
    python scripts/crm_mutate.py delete-account    # SQL DELETE
    python scripts/crm_mutate.py offboard-client   # DELETE the client's row
    python scripts/crm_mutate.py churn --n 20      # mixed volume
    python scripts/crm_mutate.py add-column        # DDL drift exercise

Each prints what it did, in a form the caller can parse.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from typing import Any

import psycopg

from ods_ingest import config


def _conn(dsn: str):
    return psycopg.connect(dsn, autocommit=True)


def make_lei(entity_part: str) -> str:
    """Build a format-valid LEI (ISO 17442) with correct check digits.

    4-char LOU prefix + "00" + 12 entity chars + 2 ISO 7064 MOD 97-10 check
    digits. Real sources emit valid LEIs, and tests/test_master_data.py
    verifies the check pair — a made-up one would fail the suite.
    """
    body = ("5493" + "00" + entity_part.upper().ljust(12, "0"))[:18]
    digits = "".join(str(int(ch, 36)) if ch.isalpha() else ch for ch in body)
    check = 98 - (int(digits + "00") % 97)
    return f"{body}{check:02d}"


def new_client(cur, suffix: str) -> dict:
    client_id = f"CLT-NEW-{suffix}"
    cur.execute(
        """
        INSERT INTO clients (client_id, client_name, lei, country_domicile, country_incorp,
                             tax_residencies, classification, kyc_status, risk_rating,
                             legal_entity_type)
        VALUES (%s, %s, %s, 'GB', 'GB', 'GB,US', 'professional', 'pending_review', 'medium',
                'fund')
        """,
        (client_id, f"Northwind Capital {suffix}", make_lei(f"NW{suffix}")),
    )
    accounts = []
    for i in (1, 2):
        account_nbr = f"ACC-NEW-{suffix}-{i}"
        cur.execute(
            """
            INSERT INTO accounts (account_nbr, client_id, account_name, account_type,
                                  base_ccy, status, open_date, branch)
            VALUES (%s, %s, %s, 'custody', 'GBP', 'active', CURRENT_DATE, 'London')
            """,
            (account_nbr, client_id, f"Northwind Capital {suffix} - Fund {i}"),
        )
        accounts.append(account_nbr)
    return {"scenario": "new-client", "clientId": client_id, "accounts": accounts}


def kyc_flip(cur, client_id: str | None) -> dict:
    if client_id is None:
        cur.execute("SELECT client_id FROM clients WHERE kyc_status <> 'expired' "
                    "ORDER BY client_id LIMIT 1")
        row = cur.fetchone()
        if not row:
            raise SystemExit("no client to update")
        client_id = row[0]
    cur.execute(
        "UPDATE clients SET kyc_status = 'expired', updated_at = now() WHERE client_id = %s",
        (client_id,),
    )
    cur.execute("SELECT count(*) FROM accounts WHERE client_id = %s", (client_id,))
    return {"scenario": "kyc-flip", "clientId": client_id, "kycStatus": "expired",
            "affectedAccounts": cur.fetchone()[0]}


def close_account(cur, account_nbr: str | None) -> dict:
    if account_nbr is None:
        cur.execute("SELECT account_nbr FROM accounts WHERE status = 'active' "
                    "ORDER BY account_nbr LIMIT 1")
        row = cur.fetchone()
        if not row:
            raise SystemExit("no active account to close")
        account_nbr = row[0]
    cur.execute(
        "UPDATE accounts SET status = 'closed', close_date = CURRENT_DATE, updated_at = now() "
        "WHERE account_nbr = %s",
        (account_nbr,),
    )
    return {"scenario": "close-account", "accountNbr": account_nbr, "status": "closed"}


def delete_account(cur, account_nbr: str | None) -> dict:
    if account_nbr is None:
        cur.execute("SELECT account_nbr FROM accounts ORDER BY account_nbr DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            raise SystemExit("no account to delete")
        account_nbr = row[0]
    cur.execute("DELETE FROM accounts WHERE account_nbr = %s", (account_nbr,))
    return {"scenario": "delete-account", "accountNbr": account_nbr}


def offboard_client(cur, client_id: str | None) -> dict:
    """Delete a client outright — accounts go first to satisfy the FK.

    Downstream this must become a soft delete: the ODS never removes documents,
    so every one of the client's accounts transitions to CLOSED.
    """
    if client_id is None:
        cur.execute("SELECT client_id FROM clients ORDER BY client_id DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            raise SystemExit("no client to offboard")
        client_id = row[0]
    cur.execute("SELECT account_nbr FROM accounts WHERE client_id = %s", (client_id,))
    accounts = [r[0] for r in cur.fetchall()]
    cur.execute("DELETE FROM accounts WHERE client_id = %s", (client_id,))
    cur.execute("DELETE FROM clients WHERE client_id = %s", (client_id,))
    return {"scenario": "offboard-client", "clientId": client_id, "accounts": accounts}


def churn(cur, n: int, seed: int) -> dict:
    """Mixed traffic — the shape a real source produces."""
    rng = random.Random(seed)
    cur.execute("SELECT account_nbr FROM accounts ORDER BY account_nbr")
    accounts = [r[0] for r in cur.fetchall()]
    done: list[str] = []
    for i in range(n):
        choice = rng.choice(["rename", "branch", "status"])
        account_nbr = rng.choice(accounts)
        if choice == "rename":
            cur.execute("UPDATE accounts SET account_name = account_name || ' *', "
                        "updated_at = now() WHERE account_nbr = %s", (account_nbr,))
        elif choice == "branch":
            cur.execute("UPDATE accounts SET branch = %s, updated_at = now() "
                        "WHERE account_nbr = %s",
                        (rng.choice(["Toronto", "London", "New York", "Dublin"]), account_nbr))
        else:
            cur.execute("UPDATE accounts SET status = %s, updated_at = now() "
                        "WHERE account_nbr = %s",
                        (rng.choice(["active", "suspended"]), account_nbr))
        done.append(f"{choice}:{account_nbr}")
    return {"scenario": "churn", "changes": len(done), "detail": done[:5]}


def add_column(cur) -> dict:
    """DDL drift: the legacy app gains a column without telling anyone.

    BACKWARD compatibility means the new field lands with a default and
    existing consumers keep working — the point of the exercise.
    """
    cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS relationship_mgr TEXT")
    cur.execute("UPDATE clients SET relationship_mgr = 'R. Chen' WHERE relationship_mgr IS NULL")
    return {"scenario": "add-column", "column": "relationship_mgr"}


def drop_column(cur) -> dict:
    cur.execute("ALTER TABLE clients DROP COLUMN IF EXISTS relationship_mgr")
    return {"scenario": "drop-column", "column": "relationship_mgr"}


SCENARIOS = ("new-client", "kyc-flip", "close-account", "delete-account",
             "offboard-client", "churn", "add-column", "drop-column")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=SCENARIOS)
    parser.add_argument("--dsn", default=config.CRM_DSN)
    parser.add_argument("--id", default=None, help="target client_id / account_nbr")
    parser.add_argument("--suffix", default="001", help="suffix for new-client ids")
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    with _conn(args.dsn) as conn, conn.cursor() as cur:
        result: dict[str, Any]
        if args.scenario == "new-client":
            result = new_client(cur, args.suffix)
        elif args.scenario == "kyc-flip":
            result = kyc_flip(cur, args.id)
        elif args.scenario == "close-account":
            result = close_account(cur, args.id)
        elif args.scenario == "delete-account":
            result = delete_account(cur, args.id)
        elif args.scenario == "offboard-client":
            result = offboard_client(cur, args.id)
        elif args.scenario == "churn":
            result = churn(cur, args.n, args.seed)
        elif args.scenario == "add-column":
            result = add_column(cur)
        else:
            result = drop_column(cur)

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
