import csv
import io
import uuid
from datetime import datetime

import requests
from flask import Blueprint, Response, redirect, render_template, request, session, url_for

from app.services.user_scope import filter_records_by_user
from config import API_URL, aws_auth
from .home import _ensure_user_settings_row

data_io_bp = Blueprint("data_io", __name__)

# ---------------------------------------------------------------------------
# Definitions: internal field → user-friendly CSV header (order matters)
# ---------------------------------------------------------------------------

FIAT_COLUMNS = [
    ("tdate", "Date"),
    ("transType", "Transaction Type"),
    ("fromWallet", "From Wallet"),
    ("toWallet", "To Wallet"),
    ("amount", "Amount"),
    ("currency", "Currency"),
    ("fee", "Fee"),
    ("mainCat", "Category"),
    ("note", "Note"),
]

CRYPTO_COLUMNS = [
    ("tdate", "Date"),
    ("cryptoName", "Crypto"),
    ("operation", "Operation"),
    ("quantity", "Quantity"),
    ("price", "Price"),
    ("currency", "Currency"),
    ("fee", "Fee"),
    ("fromWallet", "From Wallet"),
    ("toWallet", "To Wallet"),
]

STOCK_COLUMNS = [
    ("tdate", "Date"),
    ("stockName", "Symbol"),
    ("operation", "Side"),
    ("quantity", "Quantity"),
    ("price", "Price"),
    ("currency", "Currency"),
    ("fee", "Fee"),
    ("fromWallet", "From Wallet"),
    ("toWallet", "To Wallet"),
    ("note", "Note"),
]

LOAN_COLUMNS = [
    ("tdate", "Date"),
    ("type", "Type"),
    ("action", "Action"),
    ("counterparty", "Counterparty"),
    ("position", "Position"),
    ("amount", "Amount"),
    ("currency", "Currency"),
    ("fromWallet", "From Wallet"),
    ("toWallet", "To Wallet"),
    ("fee", "Fee"),
    ("ddate", "Due Date"),
    ("note", "Note"),
]

ASSET_CONFIG = {
    "fiat": {
        "api_path": "/transactions",
        "api_key": "transactions",
        "id_field": "transId",
        "post_path": "/transaction",
        "columns": FIAT_COLUMNS,
        "label": "Fiat",
    },
    "crypto": {
        "api_path": "/cryptos",
        "api_key": "cryptos",
        "id_field": "cryptoId",
        "post_path": "/crypto",
        "columns": CRYPTO_COLUMNS,
        "label": "Crypto",
    },
    "stock": {
        "api_path": "/stocks",
        "api_key": "stocks",
        "id_field": "stockId",
        "post_path": "/stock",
        "columns": STOCK_COLUMNS,
        "label": "Stock",
    },
    "loans": {
        "api_path": "/loans",
        "api_key": "loans",
        "id_field": "loanId",
        "post_path": "/loan",
        "columns": LOAN_COLUMNS,
        "label": "Loans",
    },
}

# Sample rows for downloadable templates
SAMPLE_ROWS = {
    "fiat": [
        {"Date": "2025-01-15", "Transaction Type": "income", "From Wallet": "", "To Wallet": "My Bank", "Amount": "3500", "Currency": "EUR", "Fee": "0", "Category": "Salary", "Note": "January salary"},
        {"Date": "2025-01-20", "Transaction Type": "expense", "From Wallet": "My Bank", "To Wallet": "", "Amount": "120", "Currency": "EUR", "Fee": "0", "Category": "Utilities", "Note": "Electric bill"},
    ],
    "crypto": [
        {"Date": "2025-01-10", "Crypto": "Bitcoin", "Operation": "buy", "Quantity": "0.05", "Price": "42000", "Currency": "USD", "Fee": "1.5", "From Wallet": "Bank Account", "To Wallet": "Binance"},
        {"Date": "2025-02-01", "Crypto": "Ethereum", "Operation": "buy", "Quantity": "1.2", "Price": "2300", "Currency": "USD", "Fee": "0.8", "From Wallet": "Bank Account", "To Wallet": "Binance"},
    ],
    "stock": [
        {"Date": "2025-01-05", "Symbol": "AAPL", "Side": "buy", "Quantity": "10", "Price": "185.50", "Currency": "USD", "Fee": "1", "From Wallet": "Bank Account", "To Wallet": "IBKR", "Note": "Apple shares"},
        {"Date": "2025-02-10", "Symbol": "MSFT", "Side": "buy", "Quantity": "5", "Price": "420.00", "Currency": "USD", "Fee": "1", "From Wallet": "Bank Account", "To Wallet": "IBKR", "Note": "Microsoft shares"},
    ],
    "loans": [
        {"Date": "2025-01-01", "Type": "borrow", "Action": "new", "Counterparty": "ABC Bank", "Position": "ABC Bank | EUR | 2025-01-01", "Amount": "10000", "Currency": "EUR", "From Wallet": "", "To Wallet": "My Bank", "Fee": "50", "Due Date": "2026-01-01", "Note": "Personal loan"},
        {"Date": "2025-03-01", "Type": "borrow", "Action": "repay", "Counterparty": "ABC Bank", "Position": "ABC Bank | EUR | 2025-01-01", "Amount": "500", "Currency": "EUR", "From Wallet": "My Bank", "To Wallet": "", "Fee": "0", "Due Date": "", "Note": "Monthly payment"},
    ],
}


WALLET_FIELDS = {"fromWallet", "toWallet"}


def _derive_position(counterparty, currency, tdate):
    """Derive a position string matching the JS derivePosition() convention."""
    cp = (counterparty or "").strip()
    ccy = (currency or "").strip()
    dt = (tdate or "").strip()[:10]
    if cp and ccy and dt:
        return f"{cp} | {ccy} | {dt}"
    return ""


def _fetch_wallets(user_id):
    """Return list of wallet dicts for the user."""
    try:
        resp = requests.get(
            f"{API_URL}/wallets",
            params={"userId": user_id},
            auth=aws_auth,
            timeout=15,
        )
        wallets = resp.json().get("wallets", []) if resp.status_code == 200 else []
        return filter_records_by_user(wallets, user_id)
    except Exception:
        return []


def _wallet_id_to_name(wallets):
    """Build {walletId: walletName} mapping."""
    return {w["walletId"]: w.get("walletName", w["walletId"]) for w in wallets if w.get("walletId")}


def _wallet_name_to_id(wallets):
    """Build {walletName (lower): walletId} mapping."""
    return {w.get("walletName", "").strip().lower(): w["walletId"] for w in wallets if w.get("walletId") and w.get("walletName")}


def _parse_date(s):
    """Parse a date string (YYYY-MM-DD or ISO) into a date object, or None."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "")).date()
    except Exception:
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@data_io_bp.route("/data", methods=["GET"])
def data_page():
    user = session.get("user")
    if not user:
        return render_template("home.html")
    _ensure_user_settings_row(user.get("username"))
    return render_template("data_io.html")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@data_io_bp.route("/export/<asset_type>", methods=["GET"])
def export_csv(asset_type):
    user = session.get("user")
    if not user:
        return redirect(url_for("home.home_page"))

    cfg = ASSET_CONFIG.get(asset_type)
    if not cfg:
        return Response("Invalid asset type", status=400)

    user_id = user.get("username")

    # Date range filters (optional)
    date_from = _parse_date(request.args.get("from"))
    date_to = _parse_date(request.args.get("to"))

    try:
        resp = requests.get(
            f"{API_URL}{cfg['api_path']}",
            params={"userId": user_id},
            auth=aws_auth,
            timeout=15,
        )
        records = resp.json().get(cfg["api_key"], []) if resp.status_code == 200 else []
        records = filter_records_by_user(records, user_id)
    except Exception as e:
        print(f"Export error ({asset_type}): {e}")
        records = []

    # Filter by date range if provided
    if date_from or date_to:
        filtered = []
        for rec in records:
            rec_date = _parse_date(rec.get("tdate"))
            if rec_date is None:
                continue
            if date_from and rec_date < date_from:
                continue
            if date_to and rec_date > date_to:
                continue
            filtered.append(rec)
        records = filtered

    columns = cfg["columns"]
    headers = [label for _, label in columns]

    # Resolve wallet IDs to names if this asset type has wallet columns
    has_wallet_cols = any(f in WALLET_FIELDS for f, _ in columns)
    id_to_name = _wallet_id_to_name(_fetch_wallets(user_id)) if has_wallet_cols else {}

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers)
    writer.writeheader()
    for rec in records:
        row = {}
        for field, label in columns:
            val = rec.get(field, "")
            if field in WALLET_FIELDS and val:
                val = id_to_name.get(val, val)
            row[label] = val
        writer.writerow(row)

    csv_content = buf.getvalue()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{asset_type}_transactions_{timestamp}.csv"

    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Sample template download
# ---------------------------------------------------------------------------

@data_io_bp.route("/sample/<asset_type>", methods=["GET"])
def sample_csv(asset_type):
    cfg = ASSET_CONFIG.get(asset_type)
    if not cfg:
        return Response("Invalid asset type", status=400)

    columns = cfg["columns"]
    headers = [label for _, label in columns]
    sample_rows = SAMPLE_ROWS.get(asset_type, [])

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers)
    writer.writeheader()
    for row in sample_rows:
        writer.writerow({h: row.get(h, "") for h in headers})

    csv_content = buf.getvalue()
    filename = f"{asset_type}_sample_template.csv"

    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

@data_io_bp.route("/import/<asset_type>", methods=["POST"])
def import_csv(asset_type):
    user = session.get("user")
    if not user:
        return redirect(url_for("home.home_page"))

    cfg = ASSET_CONFIG.get(asset_type)
    if not cfg:
        return redirect(url_for("data_io.data_page"))

    user_id = user.get("username")
    columns = cfg["columns"]

    # Build reverse mapping: "User Label" → "internal_field"
    label_to_field = {label: field for field, label in columns}

    file = request.files.get("file")
    if not file or file.filename == "":
        return redirect(url_for("data_io.data_page"))

    try:
        stream = io.TextIOWrapper(file.stream, encoding="utf-8-sig")
        reader = csv.DictReader(stream)

        if reader.fieldnames is None:
            return redirect(url_for("data_io.data_page"))

        # Resolve wallet names to IDs if this asset type has wallet columns
        has_wallet_cols = any(f in WALLET_FIELDS for f, _ in columns)
        name_to_id = _wallet_name_to_id(_fetch_wallets(user_id)) if has_wallet_cols else {}

        imported = 0
        errors = 0
        skipped_rows = []

        for row_num, row in enumerate(reader, start=2):
            record = {cfg["id_field"]: str(uuid.uuid4()), "userId": user_id}
            skip = False
            for csv_header, value in row.items():
                header_clean = (csv_header or "").strip()
                internal_field = label_to_field.get(header_clean)
                if internal_field:
                    val = (value or "").strip()
                    if internal_field in WALLET_FIELDS and val:
                        resolved = name_to_id.get(val.lower())
                        if resolved is None:
                            skip = True
                            skipped_rows.append(f"Row {row_num}: wallet \"{val}\" not found")
                            break
                        val = resolved
                    record[internal_field] = val

            if skip:
                errors += 1
                continue

            # Auto-derive position for loans if missing
            if asset_type == "loans" and not record.get("position"):
                record["position"] = _derive_position(
                    record.get("counterparty"),
                    record.get("currency"),
                    record.get("tdate"),
                )

            # Send empty position as None so the backend stores it correctly
            if asset_type == "loans" and not record.get("position"):
                record["position"] = None

            try:
                resp = requests.post(
                    f"{API_URL}{cfg['post_path']}",
                    json=record,
                    auth=aws_auth,
                    timeout=15,
                )
                if resp.status_code in (200, 201):
                    imported += 1
                else:
                    errors += 1
                    print(f"Import error ({asset_type}): {resp.status_code} – {resp.text}")
            except Exception as e:
                errors += 1
                print(f"Import error ({asset_type}): {e}")

        session["import_result"] = {
            "asset": cfg["label"],
            "imported": imported,
            "errors": errors,
            "skipped": skipped_rows,
        }
    except Exception as e:
        print(f"Import parse error ({asset_type}): {e}")
        session["import_result"] = {
            "asset": cfg["label"],
            "imported": 0,
            "errors": -1,
        }

    return redirect(url_for("data_io.data_page"))
