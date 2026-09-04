#!/usr/bin/env python3
"""Print a table of the 20 most recent MYOB invoices.

Talks to MYOB directly via myob_client, using the one full-access OAuth
grant in tokens.json - bypassing mag's proxy and its per-token scoping
entirely (see ../list_invoices.py for the version that goes through mag
like a real client would). That makes this a tool for whoever runs mag
itself: exploring the raw API, or one-off reporting, typically run on the
server where tokens.json lives.

This is a first exploratory call against the API, so if MYOB's actual field
names differ from what's assumed below, the script prints the raw keys of
the first invoice to make it easy to adjust.

Reads MYOB_CLIENT_ID / MYOB_CLIENT_SECRET from .env at the repo root (the
same file setup.sh maintains); set them in the environment instead to
override.

Usage:
    python3 examples/no_proxy/list_invoices.py
"""

import os
import sys

import _env

# Include the path to the mag package.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)
from mag.lib.myob_client import api_get

_env.load()

COLUMNS = [
    # (header, function to pull a display value out of one invoice record)
    ("Number", lambda inv: inv.get("Number", "")),
    ("Date", lambda inv: (inv.get("Date") or "")[:10]),
    ("Customer", lambda inv: (inv.get("Customer") or {}).get("Name", "")),
    ("Total", lambda inv: f"{inv['TotalAmount']:,.2f}" if "TotalAmount" in inv else ""),
    ("Status", lambda inv: inv.get("Status", "")),
    ("InvoiceType", lambda inv: inv.get("InvoiceType", "")),
]


def main():
    data = api_get("/Sale/Invoice", params={"$orderby": "Date desc", "$top": 20})
    invoices = data if isinstance(data, list) else data.get("Items", [])
    
    if not invoices:
        print("No invoices found.")
        return
    
    rows = [[getter(inv) for _, getter in COLUMNS] for inv in invoices]
    headers = [h for h, _ in COLUMNS]
    widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(headers)]
    
    def format_row(row):
        return "  ".join(cell.ljust(w) for cell, w in zip(row, widths))
    
    print(format_row(headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(format_row(row))
    
    print()
    print("First invoice's raw fields:")
    print(sorted(invoices[0].keys()))


if __name__ == "__main__":
    main()
