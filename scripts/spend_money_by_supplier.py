#!/usr/bin/env python3
"""Interactively pick a Supplier, then print a CSV of Spend Money
transactions for that supplier: date, memo, description, amount.

MYOB's SpendMoneyTxn has a transaction-level "Memo" plus a Lines array,
where each line carries its own "Memo", which we call "Description" for
convenience. One CSV row is printed per line, so a transaction with
several lines produces several rows sharing the same date/memo.

Ref:
https://developer.myob.com/api/myob-business-api/v2/banking/spend_money/

Always prints CSV to stdout; pass a filename as the one optional argument to
also write it to that file.

Usage:
    MYOB_CLIENT_ID=xxx MYOB_CLIENT_SECRET=yyy scripts/spend_money_by_supplier.py [output.csv]
"""

import csv
import os
import sys

from myob_client import api_get_all, load_tokens


def validate_output_path(path: str) -> None:
    """Fail fast on an unusable path; ask before overwriting an existing file."""
    if os.path.isdir(path):
        sys.exit(f"{path} is a directory, not a file.")
    
    parent = os.path.dirname(os.path.abspath(path)) or "."
    if not os.path.isdir(parent):
        sys.exit(f"Cannot write to {path}: directory {parent} does not exist.")
    if not os.access(parent, os.W_OK):
        sys.exit(f"Cannot write to {path}: no write permission for {parent}.")
    
    if os.path.exists(path):
        answer = input(f"{path} already exists. Overwrite? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            sys.exit("Aborted.")

def contact_name(s: dict) -> str:
    # Contact records have no single "Name" field. Companies use "CompanyName",
    # individuals use "FirstName"/"LastName", and have "IsIndividual" set.
    # Ref: https://developer.myob.com/api/myob-business-api/v2/contact/supplier/
    if s.get("IsIndividual"):
        name = f"{s.get('FirstName') or ''} {s.get('LastName') or ''}".strip()
    else:
        name = s.get("CompanyName") or ""
    return name or "(no name)"

def pick_supplier() -> dict:
    suppliers = api_get_all("/Contact/Supplier")
    suppliers = [s for s in suppliers if not s.get("IsIndividual")]
    if not suppliers:
        sys.exit("No (non-individual) suppliers found.")
    
    suppliers.sort(key=lambda s: contact_name(s).lower())
    
    print("Suppliers:")
    for i, s in enumerate(suppliers, start=1):
        print(f"{i:3}. {contact_name(s)}")
    
    while True:
        choice = input(f"\nPick a supplier [1-{len(suppliers)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(suppliers):
            return suppliers[int(choice) - 1]
        print("Not a valid choice, try again.")


def main():
    output_path = sys.argv[1] if len(sys.argv) > 1 else None
    if output_path:
        validate_output_path(output_path)
    
    supplier = pick_supplier()
    supplier_uid = supplier["UID"]
    
    # Server-side filter on the single Contact covering all lines.
    params = {
        "$filter": f"Contact/UID eq guid'{supplier_uid}'",
        "$orderby": "Date desc"
    }
    matching = api_get_all("/Banking/SpendMoneyTxn", params=params)
    
    if not matching:
        print(f"No Spend Money transactions found for {contact_name(supplier)}.")
        return
    
    output_file = open(output_path, "w", newline="") if output_path else None
    writers = [csv.writer(sys.stdout)]
    if output_file:
        writers.append(csv.writer(output_file))
    print()
    try:
        header = ["date", "amount", "memo", "description"]
        for writer in writers:
            writer.writerow(header)
        for txn in matching:
            date = (txn.get("Date") or "")[:10]
            memo = txn.get("Memo", "")
            lines = txn.get("Lines") or [{"Memo": "", "Amount": txn.get("AmountPaid", "")}]
            for line in lines:
                row = [date, line.get("Amount", ""), memo, line.get("Memo", "")]
                for writer in writers:
                    writer.writerow(row)
    finally:
        if output_file:
            output_file.close()
    
    print(f"\n{len(matching)} transaction(s) for {contact_name(supplier)} written to stdout"
          + (f" and {output_path}." if output_path else "."))

    # Spend Money transactions can be viewed at
    #   https://app.myob.com/#/au/{business_id}/spendMoney/<number>
    # Unfortunately, <number> doesn't seem to be part of the API!


if __name__ == "__main__":
    main()
