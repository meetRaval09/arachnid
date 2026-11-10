#!/usr/bin/env python3
"""
ARACHNID - BRUTAL HTML TEST
===========================
    python qa_html_brutal.py

A small shop website (3 pages, 2 scripts, 1 style sheet) with 38 bugs hidden so deep that nothing shows an error:
a comment that never closes, a radio group split by one capital letter, a click calling a function hidden in a
module, a checkbox tested with .value, 499 + "1" = "4991", ... and its CLEAN TWIN: the same site with every bug
fixed plus correct code that LOOKS like the bugs.

Passes only if a full scan finds EVERY hidden bug on its exact line, and finds NOTHING AT ALL on the clean twin.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "reports" / "ledger.jsonl"

SHOP = {
 "index.html": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n  <title>Brutal Shop</title>\n  <link rel=\"stylesheet\" href=\"css/style.css\">\n  <link rel=\"stylesheet\" href=\"css/print.css\" media=\"pritn\">\n  <script>\n    document.querySelector(\".cart-count\").textContent = \"0\";\n  </script>\n</head>\n<body>\n  <header>\n    <nav>\n      <a href=\"index.html\">Home</a>\n      <a href=\"about.html\">About us</a>\n      <a href=\"#reviews\">Reviews</a>\n      <a href=\"www.instagram.com/brutalshop\">Instagram</a>\n      <span class=\"cart-counter\">0</span>\n    </nav>\n  </header>\n  <main>\n    <img src=\"C:\\Users\\Meet\\Desktop\\shop\\img\\hero.jpg\" alt=\"Our shop\">\n    <p class=\"lead\">Free shipping on <span>orders over\n      <table class=\"rates\"><tr><td>India</td><td>499</td></tr></table>\n    </span></p>\n    <!-- seasonal promo, remove after Diwali -- >\n    <section class=\"products\">\n      <article class=\"item\">\n        <img src=\"img/mug.png\" alt=\"Mug\">\n        <h3>Pixel mug</h3>\n        <button onclick=\"addToCart(3)\">Add to cart</button>\n      </article>\n    </section>\n    <!-- end of promo -->\n    <h2 id=\"review\">What people say</h2>\n    <p>Prices include GST &amp;copy; Brutal Shop 2026</p>\n  </main>\n  <script type=\"module\" src=\"js/app.js\"></script>\n  <script src=\"js/cart.js\" />\n  <script>startCart();</script>\n</body>\n</html>\n",
 "checkout.html": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n  <title>Checkout</title>\n  <link rel=\"stylesheet\" href=\"css/style.css\">\n</head>\n<body>\n  <form id=\"checkout\" action=\"/order\" method=\"POTS\">\n    <label for=\"qty\">Quantity</label>\n    <input type=\"number\" id=\"qty\" name=\"qty\" min=\"1\" max=\"0\" value=\"1\">\n    <input type=\"hidden\" name=\"price\" value=\"499\">\n    <p>Shipping:</p>\n    <input type=\"radio\" id=\"std\" name=\"shipping\" value=\"standard\"> <label for=\"std\">Standard</label>\n    <input type=\"radio\" id=\"exp\" name=\"Shipping\" value=\"express\"> <label for=\"exp\">Express</label>\n    <select name=\"country\">\n      <option value=\"IN\" selected>India</option>\n      <option value=\"CA\" selected>Canada</option>\n    </select>\n    <label for=\"zip\">PIN code</label>\n    <input type=\"text\" id=\"zipcode\" name=\"zip\">\n    <input type=\"text\" name=\"coupon\"> <button type=\"buton\" onclick=\"applyCoupon()\">Apply</button>\n    <input type=\"checkbox\" id=\"terms\" name=\"terms\"> <label for=\"terms\">I accept the terms</label>\n    <p>Total: <span id=\"total\">499</span></p>\n    <input type=\"submit\" name=\"submit\" value=\"Place order\" disabled=\"false\">\n  </form>\n  <button form=\"checkot\" type=\"submit\">Pay now</button>\n  <script>\n    const form = document.getElementById(\"checkout\");\n    const qty = document.getElementById(\"qty\");\n    function applyCoupon() {\n      const cart = { items: 3, code: \"DIWALI\" };\n      localStorage.setItem(\"cart\", cart);\n    }\n    function updateTotal() {\n      const shipping = document.querySelector(\"input[name=shipping]:checked\");\n      const total = qty.value * 499 + document.getElementById(\"qty\").value;\n      document.getElementById(\"total\").value = total;\n    }\n    qty.addEventListener(\"onchange\", updateTotal);\n    document.getElementById(\"terms\").addEventListener(\"change\", () => {\n      if (document.getElementById(\"terms\").value) {\n        form.submit();\n      }\n    });\n  </script>\n</body>\n</html>\n",
 "account.html": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n  <title>Account</title>\n</head>\n<body>\n  <form action=\"/profile\" method=\"post\">\n    <label for=\"name\">Name</label>\n    <input type=\"text\" id=\"name\" name=\"name\">\n    <label for=\"bio\">About you</label>\n    <textarea id=\"bio\" name=\"bio\" />\n    <label for=\"news\">Newsletter</label>\n    <input type=\"checkbox\" id=\"news\" name=\"news\" checked=\"false\">\n    <button type=\"submit\">Save</button>\n  </form>\n  <form action=\"/avatar\" method=\"post\">\n    <input type=\"file\" name=\"avatar\">\n    <button type=\"submit\">Upload</button>\n  </form>\n  <iframe src=\"https://www.openstreetmap.org/export/embed.html\" title=\"Map\" />\n  <p>Need help? <a href=\"mailto:help@brutalshop.in\">Write to us</a></p>\n</body>\n</html>\n",
 "img/mug.png": "",
 "img/hero.jpg": "",
 "css/print.css": "@media print { nav { display: none; } }\n",
 "css/style.css": "body { font-family: system-ui, sans-serif; margin: 0; }\n.cart-counter { position: relative; top: -4px; }\n.badge { z-index: 10; background: red; }\n.card { margin: 10px 20px 10px 20; }\n.btn { transition: background 0.3; }\n@media (max-width: 600px) {\n  .nav { display: none; }\n.footer { padding: 16px; }\n",
 "js/app.js": "const cart = [];\n\nfunction addToCart(id) {\n  cart.push(id);\n  if (cart.length = 0) {\n    console.log(\"empty\");\n  }\n}\n\nfetch(\"/api/cart\").then((r) => r.json).then((data) => {\n  cart.push(...data.items);\n});\n\nconst qty = document.querySelector(\"#qty\");\nif (qty && parseInt(qty.value) === NaN) {\n  qty.value = 1;\n}\n",
 "js/cart.js": "function startCart() {\n  const list = document.getElementById(\"cart-list\");\n  return list;\n}\n"
}

CLEAN = {
 "about.html": "<!doctype html>\n<html lang=\"en\">\n<head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>About</title></head>\n<body><h1 id=\"team\">Team</h1><p><a href=\"index.html#reviews\">Reviews</a></p></body>\n</html>\n",
 "index.html": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n  <title>Brutal Shop</title>\n  <link rel=\"stylesheet\" href=\"css/style.css\">\n  <link rel=\"stylesheet\" href=\"css/print.css\" media=\"print\">\n  <link rel=\"stylesheet\" href=\"css/style.css\" media=\"screen and (min-width: 600px), only print\">\n  <script defer src=\"js/cart.js\"></script>\n</head>\n<body>\n  <header>\n    <nav>\n      <a href=\"index.html\">Home</a>\n      <a href=\"about.html\">About us</a>\n      <a href=\"#reviews\">Reviews</a>\n      <a href=\"#top\">Top</a>\n      <a href=\"#faq\">FAQ</a>\n      <a href=\"about.html#team\">Team</a>\n      <a href=\"https://www.instagram.com/brutalshop\">Instagram</a>\n      <a href=\"mailto:hi@brutalshop.in?subject=Hi&amp;body=Hello\">Mail</a>\n      <span class=\"cart-counter\">0</span>\n    </nav>\n  </header>\n  <main>\n    <img src=\"img/hero.jpg\" alt=\"Our shop\">\n    <div class=\"lead\">Free shipping on <span>orders over</span>\n      <table class=\"rates\"><tr><td>India</td><td>499</td></tr></table>\n    </div>\n    <!-- seasonal promo: a -> b, remove after Diwali -->\n    <section class=\"products\">\n      <article class=\"item\">\n        <img src=\"img/mug.png\" alt=\"Mug\">\n        <h3>Pixel mug</h3>\n        <button type=\"button\" onclick=\"addToCart(3)\">Add to cart</button>\n        <button type=\"button\" onclick=\"showInfo()\">Info</button>\n      </article>\n    </section>\n    <h2 id=\"reviews\">What people say</h2>\n    <p>Fish &amp; chips &copy; Brutal Shop 2026. To write &copy; in HTML type <code>&amp;copy;</code>.</p>\n    <div id=\"faq-box\"></div>\n  </main>\n  <script type=\"module\" src=\"js/app.js\"></script>\n  <script src=\"js/cart.js\"></script>\n  <script>startCart(); function showInfo() { return 1; } document.getElementById(\"faq-box\").id = \"faq\";</script>\n</body>\n</html>\n",
 "checkout.html": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n  <title>Checkout</title>\n  <link rel=\"stylesheet\" href=\"css/style.css\">\n</head>\n<body>\n  <form id=\"checkout\" action=\"/order\" method=\"post\">\n    <label for=\"qty\">Quantity</label>\n    <input type=\"number\" id=\"qty\" name=\"qty\" min=\"1\" max=\"10\" value=\"1\">\n    <input type=\"date\" name=\"when\" min=\"2026-01-01\" max=\"2026-12-31\">\n    <input type=\"hidden\" name=\"product_id\" value=\"mug-3\">\n    <input type=\"hidden\" name=\"currency\" value=\"INR\">\n    <p>Shipping:</p>\n    <input type=\"radio\" id=\"std\" name=\"shipping\" value=\"standard\" checked> <label for=\"std\">Standard</label>\n    <input type=\"radio\" id=\"exp\" name=\"shipping\" value=\"express\"> <label for=\"exp\">Express</label>\n    <select name=\"country\">\n      <option value=\"IN\" selected>India</option>\n      <option value=\"CA\">Canada</option>\n    </select>\n    <select name=\"tags\" multiple>\n      <option value=\"a\" selected>A</option>\n      <option value=\"b\" selected>B</option>\n    </select>\n    <label for=\"zip\">PIN code</label>\n    <input type=\"text\" id=\"zip\" name=\"zip\">\n    <input type=\"text\" name=\"coupon\" id=\"coupon\"> <button type=\"button\" onclick=\"applyCoupon()\">Apply</button>\n    <input type=\"checkbox\" id=\"terms\" name=\"terms\"> <label for=\"terms\">I accept the terms</label>\n    <p>Total: <span id=\"total\">499</span></p>\n    <output id=\"sum\" name=\"sum\">0</output>\n    <input type=\"submit\" name=\"place\" value=\"Place order\" disabled>\n  </form>\n  <button form=\"checkout\" type=\"submit\">Pay now</button>\n  <script>\n    const form = document.getElementById(\"checkout\");\n    const qty = document.getElementById(\"qty\");\n    const coupon = document.getElementById(\"coupon\");\n    function applyCoupon() {\n      const cart = { items: 3, code: coupon.value };\n      localStorage.setItem(\"cart\", JSON.stringify(cart));\n      localStorage.setItem(\"code\", coupon.value);\n      const label = \"Code: \" + coupon.value + 1;\n    }\n    function updateTotal() {\n      const total = qty.valueAsNumber * 499 + Number(document.getElementById(\"qty\").value);\n      const other = 2 + +qty.value;\n      document.getElementById(\"total\").textContent = total;\n      document.getElementById(\"sum\").value = total;\n      document.getElementById(\"total\").textContent = \"Rs \" + qty.value + \" items\";\n    }\n    qty.addEventListener(\"change\", updateTotal);\n    document.getElementById(\"terms\").addEventListener(\"change\", () => {\n      if (document.getElementById(\"terms\").checked && qty.value) {\n        form.requestSubmit();\n      }\n    });\n  </script>\n</body>\n</html>\n",
 "account.html": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n  <title>Account</title>\n</head>\n<body>\n  <form action=\"/profile\" method=\"post\">\n    <label for=\"name\">Name</label>\n    <input type=\"text\" id=\"name\" name=\"name\">\n    <label for=\"bio\">About you</label>\n    <textarea id=\"bio\" name=\"bio\"></textarea>\n    <label for=\"news\">Newsletter</label>\n    <input type=\"checkbox\" id=\"news\" name=\"news\" checked>\n    <button type=\"submit\">Save</button>\n  </form>\n  <form action=\"/avatar\" method=\"post\" enctype=\"multipart/form-data\">\n    <input type=\"file\" name=\"avatar\">\n    <button type=\"submit\">Upload</button>\n  </form>\n  <iframe src=\"https://www.openstreetmap.org/export/embed.html\" title=\"Map\"></iframe>\n  <p>Need help? <a href=\"mailto:help@brutalshop.in\">Write to us</a></p>\n</body>\n</html>\n",
 "img/mug.png": "",
 "img/hero.jpg": "",
 "css/print.css": "@media print { nav { display: none; } }\n",
 "css/style.css": "body { font-family: system-ui, sans-serif; margin: 0; }\n.cart-counter { position: relative; top: -4px; z-index: 2; }\n.badge { position: absolute; }\n.badge { z-index: 10; background: red; }\n.card { margin: 10px 20px 10px 20px; }\n.btn { transition: background 0.3s ease-in-out, color 200ms cubic-bezier(0.1, 0.7, 1, 0.1); }\n.spin { animation: spin 1s linear infinite; animation-iteration-count: 3; }\n@media screen and (max-width: 600px) {\n  .nav { display: none; }\n}\n@media (prefers-reduced-motion: reduce) { .btn { transition: none; } }\n.footer { padding: 16px; }\n",
 "js/app.js": "const cart = [];\n\nfunction addToCart(id) {\n  cart.push(id);\n  if (cart.length === 0) {\n    console.log(\"empty\");\n  }\n}\nwindow.addToCart = addToCart;\n\nfetch(\"/api/cart\").then((r) => r.json()).then((data) => {\n  cart.push(...data.items);\n}).catch(() => {});\n\nconst qty = document.querySelector(\"#qty\");\nif (qty && Number.isNaN(parseInt(qty.value, 10))) {\n  qty.value = 1;\n}\nconst settings = { dark: true };\nsessionStorage.setItem(\"settings\", JSON.stringify(settings));\n",
 "js/cart.js": "function startCart() {\n  const list = document.querySelector(\".cart-counter\");\n  return list;\n}\n"
}

# (file, text on the line of the bug, what the bug is)
TRUTH = [
 [
  "index.html",
  "media=\"pritn\"",
  "stylesheet media type misspelled - never applies"
 ],
 [
  "index.html",
  ".cart-count\")",
  "head script runs before body + wrong class -> crash on load"
 ],
 [
  "index.html",
  "href=\"about.html\"",
  "link to a page that does not exist"
 ],
 [
  "index.html",
  "href=\"#reviews\"",
  "anchor to an id that does not exist (#review)"
 ],
 [
  "index.html",
  "www.instagram.com",
  "link without https:// - goes to /www.instagram.com"
 ],
 [
  "index.html",
  "C:\\Users",
  "picture path on the developer's own PC"
 ],
 [
  "index.html",
  "<table class=\"rates\">",
  "table inside <p> (via span) - paragraph ended early"
 ],
 [
  "index.html",
  "-- >",
  "comment never closes (-- >) - hides the product section"
 ],
 [
  "index.html",
  "addToCart(3)",
  "onclick calls a function inside a module script (not global)"
 ],
 [
  "index.html",
  "&amp;copy;",
  "double-escaped entity shows '&copy;' literally"
 ],
 [
  "index.html",
  "js/cart.js\" />",
  "<script /> swallows the next script - startCart() never runs"
 ],
 [
  "checkout.html",
  "method=\"POTS\"",
  "misspelled form method - silently GET"
 ],
 [
  "checkout.html",
  "max=\"0\"",
  "number field with min 1, max 0 - no value is valid"
 ],
 [
  "checkout.html",
  "name=\"price\" value=\"499\"",
  "price sent from the browser (visitor can change it)"
 ],
 [
  "checkout.html",
  "name=\"Shipping\"",
  "radio group split by capital letter - both can be chosen"
 ],
 [
  "checkout.html",
  "value=\"CA\" selected",
  "two options selected - only the last counts"
 ],
 [
  "checkout.html",
  "<label for=\"zip\">",
  "label for zip, input id is zipcode"
 ],
 [
  "checkout.html",
  "type=\"buton\"",
  "misspelled button type - becomes SUBMIT"
 ],
 [
  "checkout.html",
  "disabled=\"false\"",
  "disabled=false still disables the order button"
 ],
 [
  "checkout.html",
  "name=\"submit\"",
  "field named submit hides form.submit() - TypeError"
 ],
 [
  "checkout.html",
  "form=\"checkot\"",
  "button points to a form id that does not exist"
 ],
 [
  "checkout.html",
  "localStorage.setItem(\"cart\", cart)",
  "object saved as '[object Object]'"
 ],
 [
  "checkout.html",
  "+ document.getElementById(\"qty\").value",
  "number + input value = text joined, not added"
 ],
 [
  "checkout.html",
  "(\"total\").value",
  ".value on a <span> - nothing changes on screen"
 ],
 [
  "checkout.html",
  "\"onchange\"",
  "addEventListener('onchange') never fires"
 ],
 [
  "checkout.html",
  "(\"terms\").value",
  "checkbox .value is always 'on' - always true"
 ],
 [
  "account.html",
  "<textarea id=\"bio\" name=\"bio\" />",
  "<textarea /> swallows the rest of the form as text"
 ],
 [
  "account.html",
  "checked=\"false\"",
  "checked=false is still checked"
 ],
 [
  "account.html",
  "action=\"/avatar\"",
  "file upload without multipart - only the name is sent"
 ],
 [
  "account.html",
  "<iframe",
  "<iframe /> swallows the rest of the page"
 ],
 [
  "js/app.js",
  "cart.length = 0",
  "= instead of === (empties the cart)"
 ],
 [
  "js/app.js",
  "r.json)",
  "r.json without () - data is the function, not the data"
 ],
 [
  "js/app.js",
  "=== NaN",
  "comparison with NaN is always false"
 ],
 [
  "js/cart.js",
  "\"cart-list\"",
  "getElementById for an id no page has"
 ],
 [
  "css/style.css",
  "z-index: 10",
  "z-index without position - no effect"
 ],
 [
  "css/style.css",
  "10px 20;",
  "margin 20 without unit - whole rule ignored"
 ],
 [
  "css/style.css",
  "0.3;",
  "transition 0.3 without s - ignored"
 ],
 [
  "css/style.css",
  "@media (max-width",
  "@media block never closed - swallows the rest"
 ]
]


def write(root, files):
    for rel, text in files.items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def scan(root):
    subprocess.run([sys.executable, str(HERE / "arachnid.py"), "scan", str(root)], capture_output=True, text=True, errors="replace", timeout=900)
    ents = []
    for ln in (LEDGER.read_text("utf-8", "replace").splitlines() if LEDGER.exists() else []):
        try:
            ents.append(json.loads(ln))
        except ValueError:
            pass
    return ents


def main():
    tmp = Path(tempfile.mkdtemp(prefix="arachnid-brutal-"))
    keep = tmp / "reports-backup"
    if (HERE / "reports").exists():
        shutil.copytree(HERE / "reports", keep)
    try:
        write(tmp / "shop", SHOP)
        write(tmp / "clean", CLEAN)
        ents = scan(tmp / "shop")
        caught = 0
        print("HIDDEN BUGS (each must be found on its exact line)")
        for f, needle, what in TRUTH:
            line = next(i for i, l in enumerate(SHOP[f].splitlines(), 1) if needle in l)
            hits = [e for e in ents if e["file"].replace(chr(92), "/").endswith(f) and int(e.get("line") or 0) == line]
            caught += bool(hits)
            print("  %-6s %-14s %3d  %s" % ("FOUND" if hits else "MISSED", f, line, what))
        noise = [e for e in scan(tmp / "clean")]
        print("\nCLEAN TWIN (must stay completely quiet)")
        for e in noise:
            print("  FALSE ALARM  [%s] %s:%s %s" % (e["tool"], e["file"], e.get("line"), e["what"][:100]))
        print("  %d finding(s)" % len(noise))
        print("\nBRUTAL SCORE: %d of %d hidden bugs found, %d false alarm(s) on the clean twin" % (caught, len(TRUTH), len(noise)))
        return 0 if caught == len(TRUTH) and not noise else 1
    finally:
        if keep.exists():
            shutil.rmtree(HERE / "reports", ignore_errors=True)
            shutil.copytree(keep, HERE / "reports")
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
