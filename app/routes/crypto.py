from flask import Blueprint, render_template, session, request, redirect, url_for, jsonify
import requests
import uuid
from config import API_URL, aws_auth
from decimal import Decimal
import math
from datetime import datetime

crypto_bp = Blueprint('crypto', __name__)

@crypto_bp.route('/crypto', methods=['GET'])
def crypto_page():
    user = session.get('user')
    if user:
        userId = user.get('username')

        # --- Fetch Cryptos ---
        try:
            response = requests.get(f"{API_URL}/cryptos", params={"userId": userId}, auth=aws_auth)
            cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching cryptos: {e}")
            cryptos = []

        # --- Fetch Wallets ---
        try:
            response = requests.get(f"{API_URL}/wallets", params={"userId": userId}, auth=aws_auth)
            wallets = response.json().get("wallets", []) if response.status_code == 200 else []
        except Exception as e:
            print(f"Error fetching wallets: {e}")
            wallets = []

        # --- Fetch top coins from CoinGecko (markets) to populate dropdown ---
        coins = []
        try:
            cg_url = "https://api.coingecko.com/api/v3/coins/markets"
            cg_params = {"vs_currency": "eur", "order": "market_cap_desc", "per_page": 500, "page": 1}
            cg_resp = requests.get(cg_url, params=cg_params, timeout=10)
            if cg_resp.status_code == 200:
                coins = cg_resp.json()
            else:
                print(f"CoinGecko returned status {cg_resp.status_code}")
        except Exception as e:
            print(f"Error fetching CoinGecko coins: {e}")

        # --- Compute totals per crypto (quantity sum and value sum) server-side ---
        totals_map = {}
        try:
            for c in cryptos:
                name = c.get('cryptoName') or 'Unknown'
                try:
                    qty = Decimal(str(c.get('quantity', 0)))
                except Exception:
                    qty = Decimal(0)
                try:
                    price = Decimal(str(c.get('price', 0)))
                except Exception:
                    price = Decimal(0)
                # fee is stored per transaction (assumed in the same fiat currency as price)
                try:
                    fee = Decimal(str(c.get('fee', 0)))
                except Exception:
                    fee = Decimal(0)

                # value is the fiat cost of the purchased crypto (qty * unit_price) + fee
                value = (qty * price) + fee

                entry = totals_map.get(name)
                if not entry:
                    # track total quantity, aggregate fiat cost (including fees), and total fees
                    entry = {
                        'cryptoName': name,
                        'total_qty': Decimal(0),
                        'total_value': Decimal(0),
                        'total_fee': Decimal(0),
                        'currency': ''
                    }
                    totals_map[name] = entry

                entry['total_qty'] += qty
                # add both the transaction fiat value and the fee (fee already included in `value` above,
                # but keep a separate running total_fee for clarity)
                entry['total_value'] += value
                entry['total_fee'] += fee
                if not entry['currency'] and c.get('currency'):
                    entry['currency'] = c.get('currency')
        except Exception as e:
            print(f"Error computing totals: {e}")

        # --- Compute live prices from CoinGecko coins list and attach live totals ---
        try:
            # helper: find coin from coins list by matching symbol/name/id (case-insensitive)
            def find_coin_for_name(name_to_find):
                """Try multiple heuristics to match stored cryptoName to a CoinGecko coin entry.
                Handles values like 'BTC', 'Bitcoin', 'BTC - Bitcoin', and case/whitespace differences.
                """
                if not name_to_find:
                    return None
                s = (name_to_find or '').strip()
                # common pattern: 'SYMBOL - Name'
                parts = [s]
                if ' - ' in s:
                    left, right = s.split(' - ', 1)
                    parts.insert(0, left.strip())
                    parts.append(right.strip())
                # also try removing spaces and parentheses
                parts.append(s.replace(' ', '').strip())
                parts = [p.lower() for p in parts if p]

                for coin in coins:
                    if not coin:
                        continue
                    c_sym = (coin.get('symbol') or '').lower()
                    c_name = (coin.get('name') or '').lower()
                    c_id = (coin.get('id') or '').lower()

                    for cand in parts:
                        if not cand:
                            continue
                        # exact matches
                        if cand == c_sym or cand == c_name or cand == c_id:
                            return coin
                        # substring matches (handle 'bitcoin' vs 'bitcoin cash' etc.)
                        if cand in c_sym or cand in c_name or cand in c_id:
                            return coin
                        if c_sym in cand or c_name in cand or c_id in cand:
                            return coin

                # no match found
                print(f"[DEBUG] No CoinGecko match for crypto name: '{name_to_find}'")
                return None

            for v in totals_map.values():
                    # try to find latest price for this crypto name
                    found = find_coin_for_name(v['cryptoName'])
                    if found and found.get('current_price') is not None:
                        try:
                            v['latest_price'] = Decimal(str(found.get('current_price', 0)))
                        except Exception:
                            v['latest_price'] = Decimal(0)
                    else:
                        # fallback: use 0 for latest price if not found
                        v['latest_price'] = Decimal(0)

                    # compute live total value based on latest price
                    v['total_value_live'] = v['total_qty'] * (v['latest_price'] or Decimal(0))

                    # compute weighted average buy price from stored totals (if total_qty > 0)
                    try:
                        if v['total_qty'] and v['total_qty'] != 0:
                            # note: total_value already includes fees, so avg_buy_price accounts for them
                            v['avg_buy_price'] = (v['total_value'] / v['total_qty'])
                        else:
                            v['avg_buy_price'] = Decimal(0)
                    except Exception:
                        v['avg_buy_price'] = Decimal(0)

                    # We no longer compute a combined average; downstream template will show:
                    # - 'latest_price' as the unit price now
                    # - 'total_value_live' as the total value now (qty * latest_price)
                    # Also compute absolute gain/loss amount based on stored total (includes fees)
                    try:
                        v['value_change_amount'] = v['total_value_live'] - v['total_value']
                    except Exception:
                        v['value_change_amount'] = Decimal(0)
        except Exception as e:
            print(f"Error computing live prices: {e}")

        # Convert Decimal values to floats for template rendering
        totals = []
        for v in totals_map.values():
            try:
                tq = float(v['total_qty'])
            except Exception:
                tq = 0.0
            try:
                tv = float(v['total_value'])
            except Exception:
                tv = 0.0
            try:
                lp = float(v.get('latest_price', 0) or 0)
            except Exception:
                lp = 0.0
            try:
                tv_live = float(v.get('total_value_live', 0) or 0)
            except Exception:
                tv_live = 0.0

            # compute percent change compared to average stored price (per-unit) and value
            price_pct = None
            value_pct = None
            pct_fill = 0.0
            price_multiplier = None
            price_pct_display = None
            value_pct_display = None
            try:
                if tq and tv:
                    avg_price = tv / tq
                    if avg_price and avg_price != 0:
                        price_pct = ((lp - avg_price) / avg_price) * 100.0
                        # multiplier (how many times current price vs avg stored price)
                        if avg_price > 0:
                            price_multiplier = (lp / avg_price) if lp is not None else None
                # percent change based on total stored value
                if tv and tv != 0:
                    value_pct = ((tv_live - tv) / tv) * 100.0
            except Exception:
                price_pct = None
                value_pct = None

            # create friendly display strings
            try:
                # price percent display: show percentage change based on price_pct
                if price_pct is None:
                    price_pct_display = 'N/A'
                else:
                    # for extremely large or tiny values, use scientific notation
                    if abs(price_pct) > 10000:
                        price_pct_display = f"{price_pct:.2e}%"
                    else:
                        price_pct_display = f"{price_pct:.2f}%"

                # value percent display
                if value_pct is None:
                    value_pct_display = 'N/A'
                else:
                    # cap large values for readability
                    if abs(value_pct) > 10000:
                        value_pct_display = f"{value_pct:.2e}%"
                    else:
                        value_pct_display = f"{value_pct:.2f}%"
            except Exception:
                price_pct_display = 'N/A'
                value_pct_display = 'N/A'

            # visual fill: use a log scale on multiplier to keep bars readable for huge changes
            try:
                # Use absolute percent change to derive a visual fill. Map percent -> 0..100
                # Small changes (0%) -> 0; 100% -> 50; >100% scale up toward 100. Use log scaling
                if price_pct is None:
                    pct_fill = 0.0
                else:
                    abs_pct = abs(price_pct)
                    # normalize: 0% -> 0, 100% -> 50, 10000%+ -> 100
                    # map via log10 to compress large ranges
                    if abs_pct <= 0:
                        pct_fill = 0.0
                    else:
                        lf = math.log10(abs_pct + 1)
                        # choose divisor so that log10(10000)/div ~ 1 -> div ~= 4
                        pct_fill = float(min(100.0, (lf / 4.0) * 100.0))
            except Exception:
                pct_fill = 0.0

            try:
                avg_buy = float(v.get('avg_buy_price', 0) or 0)
            except Exception:
                avg_buy = 0.0
            try:
                fee_total = float(v.get('total_fee', 0) or 0)
            except Exception:
                fee_total = 0.0
            try:
                change_amt = float(v.get('value_change_amount', 0) or 0)
            except Exception:
                change_amt = 0.0

            totals.append({
                'cryptoName': v['cryptoName'],
                'total_qty': tq,
                # total_value: kept for backwards-compatibility (raw stored aggregate)
                'total_value': tv,
                # total_value_buy: the amount the user actually paid (includes fees)
                'total_value_buy': tv,
                'latest_price': lp,
                'total_value_live': tv_live,
                'currency': v.get('currency',''),
                'price_pct': price_pct if price_pct is not None else 0.0,
                'value_pct': value_pct if value_pct is not None else 0.0,
                'price_multiplier': price_multiplier if price_multiplier is not None else 0.0,
                'price_pct_display': price_pct_display,
                'value_pct_display': value_pct_display,
                'pct_fill': pct_fill,
                'pct_fill_str': f"{pct_fill:.2f}",
                'avg_buy_price': avg_buy,
                'total_fee': fee_total,
                'value_change_amount': change_amt
            })

        # Send both to template (include coin list and totals)
        return render_template("crypto.html", cryptos=cryptos, wallets=wallets, coins=coins, totals=totals, userId=userId)
    else:
        return render_template("home.html")

# @crypto_bp.route('/crypto', methods=['GET'])
# def crypto_page():
#     user = session.get('user')
#     if user:
#         userId = user.get('username')
#         try:
#             response = requests.get(f"{API_URL}/cryptos", params={"userId": userId}, auth=aws_auth)
#             cryptos = response.json().get("cryptos", []) if response.status_code == 200 else []
#         except Exception as e:
#             print(f"Error fetching cryptos: {e}")
#             cryptos = []

#         try:
#             response = requests.get(f"{API_URL}/wallets", params={"userId": userId}, auth=aws_auth)
#             cryptos = response.json().get("wallets", []) if response.status_code == 200 else []
#         except Exception as e:
#             print(f"Error fetching wallets: {e}")
#             wallets = []

#         return render_template("crypto.html", cryptos=cryptos, wallets=wallets, userId=userId)
#     else:
#         return render_template("home.html")

@crypto_bp.route('/crypto', methods=['POST'])
def create_crypto():
    crypto_id = str(uuid.uuid4())
    user = session.get('user')
    user_id = user.get('username')
    data = {
        "cryptoId": crypto_id,
        "userId": user_id,
        "cryptoName": request.form["cryptoName"],
        "tdate": request.form["tdate"],        
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"],
        "quantity": request.form["quantity"],  
        "price": request.form["price"],  
        "currency": request.form["currency"],
        "fee": request.form["fee"],
        "note": request.form["note"]
    }

    print(data)
    try:
        response = requests.post(f"{API_URL}/crypto", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Create Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("crypto.crypto_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to create crypto: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

@crypto_bp.route('/updateCrypto', methods=['POST'])
def update_crypto():
    data = {
        "cryptoId": request.form["cryptoId"],
        "userId": request.form["userId"],
        "cryptoName": request.form["cryptoName"],
        "tdate": request.form["tdate"],        
        "fromWallet": request.form["fromWallet"],
        "toWallet": request.form["toWallet"],
        "quantity": request.form["quantity"],  
        "price": request.form["price"],  
        "currency": request.form["currency"], 
        "fee": request.form["fee"],  
        "note": request.form["note"]
    }
    print(f"🔄 [DEBUG] Updating crypto: {data}")
    
    try:
        response = requests.patch(f"{API_URL}/crypto", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Update Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("crypto.crypto_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to update crypto: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500

@crypto_bp.route('/deleteCrypto/<crypto_id>/<user_id>', methods=['POST'])
def delete_crypto(crypto_id, user_id):
    """Delete a crypto."""
    data = {
        "cryptoId": crypto_id,
        "userId": user_id
    }
    print(f"🗑️ [DEBUG] Deleting crypto: {data}")

    try:
        response = requests.delete(f"{API_URL}/crypto", json=data, auth=aws_auth)
        print(f"✅ [DEBUG] Delete Response: {response.status_code}, JSON: {response.json()}")

        return redirect(url_for("crypto.crypto_page"))
    except Exception as e:
        print(f"❌ [ERROR] Failed to delete crypto: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500
    
