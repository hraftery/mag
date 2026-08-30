#!/usr/bin/env python3
"""Print a table of the 20 most recent MYOB invoices.

Reads tokens.json (produced by oauth_callback.py) via myob_client, which
transparently refreshes the access token if it has expired.

This is a first exploratory call against the API, so if MYOB's actual field
names differ from what's assumed below, the script prints the raw keys of
the first invoice to make it easy to adjust.

Usage:
    MYOB_CLIENT_ID=xxx MYOB_CLIENT_SECRET=yyy python3 examples/list_invoices.py
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "app"))
from myob_client import api_get

COLUMNS = [
    # (header, function to pull a display value out of one invoice record)
    ("Number", lambda inv: inv.get("Number", "")),
    ("Date", lambda inv: (inv.get("Date") or "")[:10]),
    ("Customer", lambda inv: (inv.get("Customer") or {}).get("Name", "")),
    ("Total", lambda inv: f"{inv['TotalAmount']:,.2f}" if "TotalAmount" in inv else ""),
    ("Status", lambda inv: inv.get("Status", "")),
]


def main():
    data = api_get("/Sale/Invoice", params={"$orderby": "Date desc", "$top": 20})
    invoices = data.get("Items", data if isinstance(data, list) else [])
    
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
