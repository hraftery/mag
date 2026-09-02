#!/usr/bin/env python3
"""Print a table of the 20 most recent MYOB invoices, via mag's own proxy.

Behaves like a real mag client would, relying on mag-issued bearer token.
See no_proxy/list_invoices.py for the version that talks to MYOB directly.

The token needs "Sale/Invoice:GET" scope.

Usage:
    MAG_DOMAIN=mag.example.com MAG_TOKEN=xxxx python3 examples/list_invoices.py
"""

from _proxy_client import proxy_get

COLUMNS = [
    # (header, function to pull a display value out of one invoice record)
    ("Number", lambda inv: inv.get("Number", "")),
    ("Date", lambda inv: (inv.get("Date") or "")[:10]),
    ("Customer", lambda inv: (inv.get("Customer") or {}).get("Name", "")),
    ("Total", lambda inv: f"{inv['TotalAmount']:,.2f}" if "TotalAmount" in inv else ""),
    ("Status", lambda inv: inv.get("Status", "")),
]


def main():
    data = proxy_get("/Sale/Invoice", params={"$orderby": "Date desc", "$top": 20})
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
