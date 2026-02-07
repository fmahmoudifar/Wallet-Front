from flask import Blueprint, render_template, session, request, redirect, url_for, jsonify
import requests
import uuid
from decimal import Decimal
from decimal import ROUND_HALF_UP
from collections import defaultdict
from datetime import datetime

from config import API_URL, aws_auth

loans_bp = Blueprint('loans', __name__)


def _to_decimal(val) -> Decimal:
    try:
        if val is None:
            return Decimal(0)
        s = str(val).strip()
        if s == '':
            return Decimal(0)
        return Decimal(s)
    except Exception:
        return Decimal(0)


def _format_amount(val: Decimal, max_decimals: int = 2) -> str:
    """Format Decimal as a human-friendly string.

    Loans are fiat-oriented in the UI (EUR/USD), so default is 2 decimals.
    """
    try:
        d = val if isinstance(val, Decimal) else Decimal(str(val))
    except Exception:
        return '0'

    try:
        q = Decimal('1').scaleb(-int(max_decimals))
        d = d.quantize(q, rounding=ROUND_HALF_UP)
    except Exception:
        pass

    s = format(d, 'f')
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    if s in ('-0', '-0.0'):
        s = '0'
    return s or '0'


def _safe_date_key(date_str: str) -> float:
    """Return a sortable key (timestamp) for ISO-ish strings; invalid -> 0."""
    try:
        s = (date_str or '').strip()
        if not s:
            return 0.0
        # Handle a common "Z" suffix.
        if s.endswith('Z'):
            s = s[:-1]
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def _day_from_iso(date_str: str) -> str:
    """Extract YYYY-MM-DD from an ISO-ish datetime string."""
    s = (date_str or '').strip()
    if len(s) >= 10:
        return s[:10]
    return s


def _derive_position(counterparty: str, currency: str, tdate: str) -> str:
    """Derive a position string from counterparty+currency+date (day precision)."""
    party = (counterparty or '').strip() or '—'
    ccy = (currency or '').strip().upper() or 'EUR'
    day = _day_from_iso(tdate)
    if not day:
        day = 'unknown-date'
    return f"{party} | {ccy} | {day}"


@loans_bp.route('/loans', methods=['GET'])
def loans_page():
    user = session.get('user')
    if not user:
        return render_template('home.html')

    user_id = user.get('username')

    # --- Fetch loans ---
    try:
        resp = requests.get(f"{API_URL}/loans", params={"userId": user_id}, auth=aws_auth, timeout=12)
        loans = resp.json().get('loans', []) if resp.status_code == 200 else []
    except Exception as e:
        print(f"Error fetching loans: {e}")
        loans = []

    # --- Counterparties list (for autocomplete) ---
    counterparties_map = {}
    try:
        for row in (loans or []):
            raw = row.get('counterparty')
            s = (str(raw) if raw is not None else '').strip()
            if not s or s.lower() == 'none':
                continue
            key = s.lower()
            # Keep the first-seen capitalization to be stable.
            if key not in counterparties_map:
                counterparties_map[key] = s
    except Exception:
        counterparties_map = {}

    counterparties = list(counterparties_map.values())
    try:
        counterparties.sort(key=lambda x: (x or '').lower())
    except Exception:
        pass

    # --- Fetch wallets (for dropdowns + id->name mapping) ---
    try:
        w_resp = requests.get(f"{API_URL}/wallets", params={"userId": user_id}, auth=aws_auth, timeout=12)
        wallets = w_resp.json().get('wallets', []) if w_resp.status_code == 200 else []
    except Exception as e:
        print(f"Error fetching wallets: {e}")
        wallets = []

    # --- Overview totals (currency-safe) ---
    # Totals must not mix currencies. We compute per-currency totals.
    base_currency = (session.get('currency') or 'EUR').strip().upper() or 'EUR'
    receive_totals = defaultdict(lambda: Decimal(0))
    owe_totals = defaultdict(lambda: Decimal(0))
    try:
        for row in (loans or []):
            t = str(row.get('type') or '').strip().lower()
            if t not in ('borrow', 'lend', 'loan'):
                t = 'borrow'
            action = str(row.get('action') or '').strip().lower() or 'new'
            amt = _to_decimal(row.get('amount'))
            ccy = (str(row.get('currency') or '').strip().upper() or base_currency)

            if t == 'lend':
                if action == 'repay':
                    receive_totals[ccy] -= amt
                else:
                    receive_totals[ccy] += amt
            elif t in ('borrow', 'loan'):
                if action == 'repay':
                    owe_totals[ccy] -= amt
                else:
                    owe_totals[ccy] += amt
    except Exception:
        receive_totals = defaultdict(lambda: Decimal(0))
        owe_totals = defaultdict(lambda: Decimal(0))

    # Clamp negatives (overpaid) to 0 per currency.
    for ccy in list(receive_totals.keys()):
        if receive_totals[ccy] < 0:
            receive_totals[ccy] = Decimal(0)
    for ccy in list(owe_totals.keys()):
        if owe_totals[ccy] < 0:
            owe_totals[ccy] = Decimal(0)

    # Legacy single totals for older templates: prefer base currency bucket.
    receive_total = receive_totals.get(base_currency, Decimal(0))
    owe_total = owe_totals.get(base_currency, Decimal(0))

    # --- Positions overview (explicit Position field) ---
    # We group by (type, position). Position is a string identifying a "contract".
    # For backwards compatibility, if position is missing on an existing transaction,
    # we derive it from counterparty+currency+date for NEW rows. Repays without a
    # position are allocated FIFO across derived positions within (type, counterparty, currency).
    positions = {}
    positions_catalog_map = {}  # (type, position) -> {type, position, counterparty, currency, opened_date}
    new_entries_by_group = defaultdict(list)  # (type, party_lower, currency) -> [entry]
    repay_rows_no_pos_by_group = defaultdict(list)  # (type, party_lower, currency) -> [repay_row]
    try:
        for row in (loans or []):
            t = str(row.get('type') or '').strip().lower()
            if t not in ('borrow', 'lend', 'loan'):
                t = 'borrow'

            action = str(row.get('action') or '').strip().lower() or 'new'
            if action not in ('new', 'repay'):
                action = 'new'

            party = str(row.get('counterparty') or '').strip() or '—'
            currency = (str(row.get('currency') or '').strip().upper() or base_currency)
            amt = _to_decimal(row.get('amount'))
            fee = _to_decimal(row.get('fee'))
            tdate = str(row.get('tdate') or '').strip()
            due = str(row.get('ddate') or '').strip()
            position_raw = str(row.get('position') or '').strip()

            ts = _safe_date_key(tdate)
            due_ts = _safe_date_key(due)

            if action == 'new':
                position = position_raw or _derive_position(party, currency, tdate)
            else:
                position = position_raw

            if position:
                key = (t, position)
                entry = positions.get(key)
                if not entry:
                    entry = {
                        'type': t,
                        'position': position,
                        'counterparty': party,
                        'currency': currency,
                        'principal': Decimal(0),
                        'repaid': Decimal(0),
                        'fee_total': Decimal(0),
                        'tx_count': 0,
                        'opened_date': '',
                        'opened_ts': 0.0,
                        'last_activity': '',
                        'last_activity_ts': 0.0,
                        'next_due': '',
                        'next_due_ts': 0.0,
                    }
                    positions[key] = entry

                entry['tx_count'] += 1
                entry['fee_total'] += fee

                if action == 'new':
                    entry['principal'] += amt
                    open_ts = ts
                    if open_ts and ((entry.get('opened_ts') or 0.0) == 0.0 or open_ts < entry['opened_ts']):
                        entry['opened_ts'] = open_ts
                        entry['opened_date'] = tdate
                else:
                    entry['repaid'] += amt

                if ts and ts >= (entry.get('last_activity_ts') or 0.0):
                    entry['last_activity_ts'] = ts
                    entry['last_activity'] = tdate

                if due_ts and ((entry.get('next_due_ts') or 0.0) == 0.0 or due_ts < entry['next_due_ts']):
                    entry['next_due_ts'] = due_ts
                    entry['next_due'] = due

                # Build positions catalog (suggestions) from NEW transactions.
                # Include derived positions too so users can target existing legacy positions.
                if action == 'new' and position:
                    cat_key = (t, position)
                    if cat_key not in positions_catalog_map:
                        positions_catalog_map[cat_key] = {
                            'type': t,
                            'position': position,
                            'counterparty': party,
                            'currency': currency,
                            'opened_date': _day_from_iso(tdate),
                        }

                # Track NEW entries per (type, counterparty, currency) for FIFO fallback.
                if action == 'new':
                    new_entries_by_group[(t, party.lower(), currency)].append(entry)
            else:
                # Repay without position (legacy): allocate FIFO later.
                repay_rows_no_pos_by_group[(t, party.lower(), currency)].append({
                    'amount': amt,
                    'fee': fee,
                    'tdate': tdate,
                    'ts': ts or 0.0,
                })

        # FIFO allocation for legacy REPAY rows without position.
        for group_key, repay_rows in repay_rows_no_pos_by_group.items():
            entries = new_entries_by_group.get(group_key, [])
            if not entries:
                continue

            entries.sort(key=lambda e: (e.get('opened_ts') or 0.0, e.get('opened_date') or ''))
            repay_rows_sorted = sorted(repay_rows, key=lambda r: (r.get('ts') or 0.0, r.get('tdate') or ''))

            for repay in repay_rows_sorted:
                remaining = repay.get('amount') or Decimal(0)
                if remaining <= 0:
                    continue

                fee_added = False
                repay_ts = repay.get('ts') or 0.0
                repay_date = repay.get('tdate') or ''

                for entry in entries:
                    if remaining <= 0:
                        break
                    outstanding = (entry.get('principal') or Decimal(0)) - (entry.get('repaid') or Decimal(0))
                    if outstanding <= 0:
                        continue

                    apply_amt = remaining if remaining <= outstanding else outstanding
                    entry['repaid'] = (entry.get('repaid') or Decimal(0)) + apply_amt
                    entry['tx_count'] = int(entry.get('tx_count') or 0) + 1
                    remaining -= apply_amt

                    if not fee_added:
                        entry['fee_total'] = (entry.get('fee_total') or Decimal(0)) + (repay.get('fee') or Decimal(0))
                        fee_added = True

                    if repay_ts and repay_ts >= (entry.get('last_activity_ts') or 0.0):
                        entry['last_activity_ts'] = repay_ts
                        entry['last_activity'] = repay_date

    except Exception as e:
        print(f"Error building loan positions: {e}")
        positions = {}
        positions_catalog_map = {}

    loan_positions = []
    try:
        for entry in positions.values():
            principal = entry['principal']
            repaid = entry['repaid']
            outstanding = principal - repaid
            if outstanding < 0:
                # Avoid showing negative outstanding (e.g., data issues or overpayment).
                outstanding = Decimal(0)

            repaid_pct = Decimal(0)
            if principal > 0:
                repaid_pct = (repaid / principal) * Decimal(100)
                if repaid_pct < 0:
                    repaid_pct = Decimal(0)
                if repaid_pct > 100:
                    repaid_pct = Decimal(100)

            status = 'Open' if outstanding > 0 else 'Closed'
            if entry['type'] == 'lend':
                badge_class = 'pct-positive'
                type_label = 'Lend'
            elif entry['type'] == 'loan':
                badge_class = 'pct-negative'
                type_label = 'Loan'
            else:
                badge_class = 'pct-negative'
                type_label = 'Borrow'

            # Display-friendly dates: show just YYYY-MM-DD when possible.
            opened_disp = (entry.get('opened_date') or '')
            if opened_disp and len(opened_disp) >= 10:
                opened_disp = opened_disp[:10]
            last_disp = (entry.get('last_activity') or '')
            if last_disp and len(last_disp) >= 10:
                last_disp = last_disp[:10]
            due_disp = (entry.get('next_due') or '')
            if due_disp and len(due_disp) >= 10:
                due_disp = due_disp[:10]

            loan_positions.append({
                'type': entry['type'],
                'type_label': type_label,
                'badge_class': badge_class,
                'position': entry.get('position') or '',
                'counterparty': entry['counterparty'],
                'currency': entry['currency'],
                'principal': float(principal),
                'repaid': float(repaid),
                'outstanding': float(outstanding),
                'principal_display': _format_amount(principal),
                'repaid_display': _format_amount(repaid),
                'outstanding_display': _format_amount(outstanding),
                'repaid_pct': float(repaid_pct),
                'repaid_pct_display': _format_amount(repaid_pct, 0),
                'fee_total_display': _format_amount(entry.get('fee_total') or Decimal(0)),
                'tx_count': int(entry.get('tx_count') or 0),
                'status': status,
                'opened_date': opened_disp,
                'last_activity': last_disp,
                'next_due': due_disp,
            })
    except Exception:
        loan_positions = []

    # Sort: open first, then lend/borrow, then counterparty, then currency.
    loan_positions.sort(key=lambda p: (
        0 if p.get('status') == 'Open' else 1,
        0 if p.get('type') == 'lend' else (1 if p.get('type') == 'borrow' else 2),
        (p.get('counterparty') or '').lower(),
        (p.get('currency') or ''),
        _safe_date_key((p.get('opened_date') or '') + 'T00:00'),
    ))

    positions_catalog = list(positions_catalog_map.values())
    try:
        positions_catalog.sort(key=lambda x: (
            str(x.get('type') or ''),
            str(x.get('counterparty') or '').lower(),
            str(x.get('currency') or ''),
            str(x.get('opened_date') or ''),
        ))
    except Exception:
        pass

    return render_template(
        "loans.html",
        loans=loans,
        wallets=wallets,
        receive_total=float(receive_total),
        owe_total=float(owe_total),
        base_currency=base_currency,
        receive_totals={k: float(v) for k, v in (receive_totals or {}).items() if v is not None},
        owe_totals={k: float(v) for k, v in (owe_totals or {}).items() if v is not None},
        loan_positions=loan_positions,
        counterparties=counterparties,
        positions_catalog=positions_catalog,
    )


@loans_bp.route('/loans', methods=['POST'])
def create_loan_transaction():
    user = session.get('user')
    if not user:
        return redirect(url_for('home.home_page'))

    user_id = user.get('username')
    loan_id = str(uuid.uuid4())

    from_wallet = (request.form.get('fromWallet', '') or '').strip()
    to_wallet = (request.form.get('toWallet', '') or '').strip()
    amount = (request.form.get('amount', '') or '').strip() or '0'
    fee = (request.form.get('fee', '') or '').strip() or '0'
    currency = (request.form.get('currency', '') or '').strip() or ((session.get('currency') or 'EUR').strip().upper() or 'EUR')

    tx_type = (request.form.get('type', '') or '').strip().lower()
    if tx_type not in ('borrow', 'lend', 'loan'):
        tx_type = 'borrow'
    action = (request.form.get('action', '') or '').strip().lower() or 'new'

    data = {
        "loanId": loan_id,
        "userId": user_id,
        "type": tx_type,
        "action": action,
        "position": (request.form.get('position', '') or '').strip(),
        "counterparty": request.form.get('counterparty', ''),
        "amount": amount,
        "currency": currency,
        "fromWallet": from_wallet,
        "toWallet": to_wallet,
        "fee": fee,
        "tdate": request.form.get('tdate', ''),
        "ddate": request.form.get('ddate', ''),
        "note": request.form.get('note', ''),
    }

    # Keep keys present even when empty so the backend can persist them.
    # Send dates as "" when not selected; only convert empty position to null.
    if data.get("position") == "":
        data["position"] = None

    print(f"🔄 [Loans] Creating loan payload: {data}")

    try:
        resp = requests.post(f"{API_URL}/loan", json=data, auth=aws_auth, timeout=12)
        if resp.status_code in (200, 201):
            return redirect(url_for('loans.loans_page'))

        # Bubble up backend message for debugging
        try:
            detail = resp.json()
        except Exception:
            detail = {"text": resp.text}
        print(f"❌ [Loans] Create failed: {resp.status_code} detail={detail} text={resp.text}")
        # Keep UX consistent with other pages: redirect back even on failure.
        return redirect(url_for('loans.loans_page'))
    except Exception as e:
        print(f"[Loans] Create exception: {e}")
        return redirect(url_for('loans.loans_page'))


@loans_bp.route('/updateLoan', methods=['POST'])
def update_loan_transaction():
    user = session.get('user')
    if not user:
        return redirect(url_for('home.home_page'))

    # Mirror the crypto/stock pattern: userId is submitted by the form.
    user_id = (request.form.get('userId', '') or '').strip() or user.get('username')

    from_wallet = (request.form.get('fromWallet', '') or '').strip()
    to_wallet = (request.form.get('toWallet', '') or '').strip()
    amount = (request.form.get('amount', '') or '').strip() or '0'
    fee = (request.form.get('fee', '') or '').strip() or '0'
    currency = (request.form.get('currency', '') or '').strip() or ((session.get('currency') or 'EUR').strip().upper() or 'EUR')

    tx_type = (request.form.get('type', '') or '').strip().lower()
    if tx_type not in ('borrow', 'lend', 'loan'):
        tx_type = 'borrow'
    action = (request.form.get('action', '') or '').strip().lower() or 'new'

    data = {
        "loanId": request.form.get('loanId', ''),
        "userId": user_id,
        "type": tx_type,
        "action": action,
        "position": (request.form.get('position', '') or '').strip(),
        "counterparty": request.form.get('counterparty', ''),
        "amount": amount,
        "currency": currency,
        "fromWallet": from_wallet,
        "toWallet": to_wallet,
        "fee": fee,
        "tdate": request.form.get('tdate', ''),
        "ddate": request.form.get('ddate', ''),
        "note": request.form.get('note', ''),
    }

    # Keep keys present even when empty so the backend can persist them.
    # Send dates as "" when not selected; only convert empty position to null.
    if data.get("position") == "":
        data["position"] = None

    print(f"🔄 [Loans] Updating loan payload: {data}")

    try:
        response = requests.patch(f"{API_URL}/loan", json=data, auth=aws_auth, timeout=12)
        try:
            j = response.json()
        except Exception:
            j = None

        print(f"✅ [Loans] Update Response: {response.status_code}, JSON: {j}")
        if response.status_code not in (200, 201):
            print(f"❌ [Loans] Update failed. Text: {response.text}")
        # Keep UX consistent with other pages: always redirect back.
        return redirect(url_for('loans.loans_page'))
    except Exception as e:
        print(f"❌ [Loans] Failed to update loan: {str(e)}")
        return redirect(url_for('loans.loans_page'))


@loans_bp.route('/deleteloan/<loan_id>', methods=['POST'])
def delete_loan_transaction(loan_id):
    user = session.get('user')
    if not user:
        return redirect(url_for('home.home_page'))

    user_id = user.get('username')
    data = {
        "loanId": loan_id,
        "userId": user_id,
    }

    try:
        response = requests.delete(f"{API_URL}/loan", json=data, auth=aws_auth, timeout=12)
        try:
            print(f"✅ [Loans] Delete Response: {response.status_code}, JSON: {response.json()}")
        except Exception:
            print(f"✅ [Loans] Delete Response: {response.status_code}, Text: {response.text}")
        return redirect(url_for('loans.loans_page'))
    except Exception as e:
        print(f"❌ [Loans] Failed to delete loan: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500
