#!/usr/bin/env python3
"""Copy prices from the "Website prices - Pan de Mama" Google Sheet into the website.

Reads the sheet as CSV (columns: Section, Item, Option, Price C$) and updates, in
index.html and es/index.html:
  - the MENU list in the order script (what the order form charges),
  - the order list written into the page for Google (#ord-menu),
  - the menu highlights (#menu),
  - the extras / add-ons list (EX) for rows with Section "Extras".
Only prices change. Items are matched by their English name and option, so rename
or add items on the website itself, not in the sheet.
Exits 0 with no changes when every price already matches.
"""
import csv, io, os, re, sys, urllib.request

SHEET_ID = os.environ.get("PRICES_SHEET_ID", "")
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet=Prices"
FILES = ["index.html", "es/index.html"]
# Menu highlights that are not in the takeout order list (English -> Spanish name).
EXTRA_ES = {"Stuffed French Toast": "Tostada francesa rellena"}


def load_prices(text):
    prices = {}
    for row in csv.DictReader(io.StringIO(text)):
        item = (row.get("Item") or "").strip()
        opt = (row.get("Option") or "").strip()
        raw = re.sub(r"[^\d]", "", row.get("Price C$") or "")
        if not item or not raw:
            continue
        n = int(raw)
        if not 10 <= n <= 5000:
            sys.exit(f"Price for {item} / {opt or '-'} looks wrong: {n}")
        prices[(item, opt)] = n
    if len(prices) < 20:
        sys.exit(f"Only {len(prices)} prices read from the sheet; stopping so the site isn't broken.")
    return prices


def js_unescape(s):
    return s.replace("\\'", "'")


def update_file(path, prices, report):
    h = open(path, encoding="utf-8").read()
    start = h.index("var MENU=[")
    end = h.index("\nvar BOX", start)
    menu = h[start:end]
    items = []  # (g, i, en, es, [prices of variants or single])
    g, i = -1, 0
    out_lines = []
    for line in menu.split("\n"):
        if re.match(r"^\{en:'[^']*',es:'[^']*'(,box:1)?,items:\[", line):
            g += 1; i = 0
            out_lines.append(line); continue
        m = re.match(r"^\{en:'((?:[^'\\]|\\.)*)',es:'((?:[^'\\]|\\.)*)'", line)
        if not m or g < 0:
            out_lines.append(line); continue
        en, es = js_unescape(m.group(1)), js_unescape(m.group(2))
        vals = []
        if "v:[" in line:
            def wsub(mm):
                opt = js_unescape(mm.group(1)); old = int(mm.group(3))
                new = prices.get((en, opt), old)
                if new != old: report.append(f"{path}: {en} / {opt}: C${old} -> C${new}")
                vals.append(new)
                return f"W('{mm.group(1)}','{mm.group(2)}',{new})"
            line = re.sub(r"W\('((?:[^'\\]|\\.)*)','((?:[^'\\]|\\.)*)',(\d+)\)", wsub, line)
        else:
            def psub(mm):
                old = int(mm.group(1)); new = prices.get((en, ""), old)
                if new != old: report.append(f"{path}: {en}: C${old} -> C${new}")
                vals.append(new)
                return f",p:{new}"
            line = re.sub(r",p:(\d+)", psub, line, count=1)
        items.append((g, i, en, es, vals, "v:[" in line))
        i += 1
        out_lines.append(line)
    h = h[:start] + "\n".join(out_lines) + h[end:]

    # Extras / add-ons (sheet rows: Section "Extras", Item = group name, Option = extra).
    if "var EX={" in h:
        names = dict(re.findall(r"(\w+):'([^']+)'", h[h.index("var EXN={"):h.index("};", h.index("var EXN={"))]))
        a = h.index("var EX={"); b = h.index("\n};", a)
        out = []
        for line in h[a:b].split("\n"):
            m = re.match(r"^(\w+):\[", line)
            if m and m.group(1) in names:
                grp = names[m.group(1)]
                def xsub(mm, grp=grp):
                    opt = js_unescape(mm.group(1)); old = int(mm.group(3))
                    new = prices.get((grp, opt), old)
                    if new != old: report.append(f"{path}: {grp} / {opt}: C${old} -> C${new}")
                    return f"X('{mm.group(1)}','{mm.group(2)}',{new})"
                line = re.sub(r"X\('((?:[^'\\]|\\.)*)','((?:[^'\\]|\\.)*)',(\d+)\)", xsub, line)
            out.append(line)
        h = h[:a] + "\n".join(out) + h[b:]

    # Order list written into the page.
    for g, i, en, es, vals, multi in items:
        if not vals: continue
        tag = f'<div class="ord-item" data-g="{g}" data-i="{i}">'
        a = h.find(tag)
        if a < 0: continue
        b = h.find('<div class="ord-item"', a + len(tag))
        b = len(h) if b < 0 else b
        c = h.find("</div></div></div>", a)  # end of the last item in a group
        if 0 <= c < b: b = c
        block = h[a:b]
        block = re.sub(r'(<span class="pr">(?:from |desde )?)C\$[\d,]+(</span>)', lambda mm: f"{mm.group(1)}C${vals[0]:,}{mm.group(2)}", block, count=1)
        if multi:
            block = re.sub(r'(<option value="(\d+)">[^<]*? · )C\$[\d,]+(</option>)',
                           lambda mm: f"{mm.group(1)}C${vals[int(mm.group(2))]:,}{mm.group(3)}" if int(mm.group(2)) < len(vals) else mm.group(0), block)
        h = h[:a] + block + h[b:]

    # Menu highlights.
    names = {}
    for g, i, en, es, vals, multi in items:
        if vals: names[en] = names[es] = vals[0]
    for en, es in EXTRA_ES.items():
        if (en, "") in prices: names[en] = names[es] = prices[(en, "")]
    s0 = h.find('<section id="menu"'); s1 = h.find("</section>", s0)
    sec = h[s0:s1]
    def hsub(mm):
        name = mm.group(2).replace("&amp;", "&")
        if name not in names: return mm.group(0)
        return f"{mm.group(1)}C${names[name]:,}{mm.group(4)}"
    sec = re.sub(r'(<h4[^>]*>([^<]*)</h4>(<small>[^<]*</small>)?<span class="dots"></span><b>)C\$[\d,]+(</b>)', hsub, sec)
    h = h[:s0] + sec + h[s1:]
    return h, items


def main():
    if not SHEET_ID: sys.exit("PRICES_SHEET_ID is not set")
    text = urllib.request.urlopen(CSV_URL, timeout=30).read().decode("utf-8")
    prices = load_prices(text)
    report, changed = [], False
    for path in FILES:
        old = open(path, encoding="utf-8").read()
        new, items = update_file(path, prices, report)
        if new != old:
            open(path, "w", encoding="utf-8").write(new); changed = True
    print("\n".join(report) if report else "All website prices already match the sheet.")
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a") as f: f.write(f"changed={'true' if changed else 'false'}\n")


if __name__ == "__main__":
    main()
