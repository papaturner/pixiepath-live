#!/usr/bin/env python3
"""
Pixie Path drink-menu sweep — deterministic, no model in the loop.

Reads `sources.json` (catalog venue -> public menu page URLs), fetches each page,
parses drink names + prices with plain HTML parsing, matches them to the app's
built-in catalog (`drinks-catalog.json`), and rewrites the overrides file the
iPhone app downloads (`drinks-menu.json`). Only exact prices seen on a page are
written; anything unmatched stays an estimate in the app.

Standard library only. Run locally:  python3 sweep.py            (writes drinks-menu.json)
                                      python3 sweep.py --dry-run  (prints the diff summary only)
                                      python3 sweep.py --test file.html  (parse one saved page)
"""
import json, re, sys, html, time, datetime, difflib, urllib.request, urllib.error
from html.parser import HTMLParser

UA = 'pixiepath-menu-sweep/1.0 (+https://github.com/papaturner/pixiepath-live)'
TODAY = datetime.date.today().isoformat()
PRICE_RX = re.compile(r'\$\s?(\d{1,3}(?:\.\d{1,2})?)')
MISSING_DAYS_BEFORE_REMOVED = 21   # a verified drink absent from its source this long is marked removed
MAX_AGE_DAYS_KEEP = 120            # verified prices older than this (and not re-seen) fall back to estimates

def norm(s):
    s = html.unescape(s or '').lower()
    s = s.replace('&', 'and').replace('’', "'")
    s = re.sub(r'\(.*?\)', ' ', s)                 # drop parentheticals: "(souvenir mug)"
    s = re.sub(r"[^a-z0-9' ]+", ' ', s)
    s = re.sub(r'\b(the|a|an|of|de|du|la|le|with|and)\b', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html,*/*;q=0.8'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode('utf-8', 'ignore'), r.status
    except urllib.error.HTTPError as e:
        # The Mouse For Less answers 406 but still serves the page body.
        body = e.read().decode('utf-8', 'ignore') if hasattr(e, 'read') else ''
        return body, e.code
    except Exception:
        return '', 0

# ---------------------------------------------------------------- parsers
class TextBlocks(HTMLParser):
    """Collapse a page into a list of (tag, text) blocks in document order."""
    def __init__(self):
        super().__init__(); self.blocks = []; self._stack = []; self._buf = []; self._cls = []
    BLOCK = {'p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'tr', 'td', 'span', 'strong', 'b'}
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self._stack.append('skip'); self._cls.append('')
        elif tag in self.BLOCK:
            self._flush(); self._stack.append(tag); self._cls.append(dict(attrs).get('class') or '')
    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self._stack and self._stack[-1] == 'skip': self._stack.pop(); self._cls.pop()
        elif tag in self.BLOCK:
            self._flush()
            if self._stack and self._stack[-1] == tag: self._stack.pop(); self._cls.pop()
    def handle_data(self, data):
        if 'skip' in self._stack: return
        self._buf.append(data)
    def _flush(self):
        t = re.sub(r'\s+', ' ', ''.join(self._buf)).strip(); self._buf = []
        if t: self.blocks.append((self._stack[-1] if self._stack else '', t, self._cls[-1] if self._cls else ''))

def parse_menu(page_html):
    """Return (items, menu_date) where items = [(name, price_float)]."""
    tb = TextBlocks(); tb.feed(page_html)
    blocks = tb.blocks
    text = ' '.join(t for _, t, _c in blocks)
    items = []
    # Pattern A (The Mouse For Less): "<strong>Name</strong> – $14.50" → name block then a price block
    # Pattern B (Disneyblog): name block, description block, then a lone "$19.5" block
    pending = []   # name candidates seen since the last price
    LEAD_PRICE = re.compile(r'^[\s\u2013\u2014\-:]*\$\s?(\d{1,3}(?:\.\d{1,2})?)(?!\s*/)')
    SECTION = re.compile(r'\b(menu|beverages?|cocktails?|beer|wine|flights?|non.alcoholic|specialty|draft|bottled|canned|updated|date|snacks?|entrees?|desserts?|kids)\b', re.I)
    for tag, t, cls in blocks:
        prices = PRICE_RX.findall(t)
        is_name = tag in ('strong', 'b') or 'font-semibold' in cls or 'font-bold' in cls
        is_desc = (('text-gray-5' in cls or 'text-gray-4' in cls or 'description' in cls) and not is_name)
        is_badge = tag == 'span' and ('rounded' in cls or 'bg-' in cls or len(t) <= 8)   # "GF", "Vegan", "New" pills
        if len(prices) >= 2 and '/' in t: pending = []; continue           # "Glass $12 / Bottle $52" rows
        # A) block that STARTS with a price (Mouse For Less: "<strong>Name</strong> – $14.50<br>desc")
        lm = LEAD_PRICE.match(t)
        if lm:
            if pending: items.append((pending[-1], float(lm.group(1))))
            pending = []; continue
        # B) price-only block (Disneyblog: name p, desc p, then "$19.5")
        if len(prices) == 1 and not re.search(r'[a-z]{3,}', PRICE_RX.sub('', t).lower()):
            if pending: items.append((pending[-1], float(prices[0])))
            pending = []; continue
        # C) inline "Name – $12.00" in one block
        im = re.match(r'^(.{2,70}?)\s*[\u2013\u2014\-:]+\s*\$\s?(\d{1,3}(?:\.\d{1,2})?)(?:\D|$)', t) or re.match(r'^(.{2,70}?)\s+\$\s?(\d{1,3}(?:\.\d{1,2})?)\s*$', t)
        if im:
            name = im.group(1).strip(' \u2013\u2014-:')
            if pending and (',' in name or len(name.split()) > 5 or name[:1].islower()): name = pending[-1]
            items.append((name, float(im.group(2)))); pending = []; continue
        # D) name candidate: short, no price, title-like, not a section heading
        if is_desc or is_badge or tag in ('h1', 'h2', 'h3', 'h4', 'h5'): continue   # descriptions, badges and section headings are never names
        if 2 <= len(t) <= 60 and '$' not in t and t[:1].isalpha() and (t[:1].isupper() or is_name):
            if is_name or not SECTION.search(t):
                if not (',' in t and len(t.split()) > 4):
                    pending.append(t.strip(' \u2013\u2014-:')); pending = pending[-3:]
    # de-dupe keeping first occurrence
    seen = set(); out = []
    for n, p in items:
        k = (norm(n), p)
        if k in seen or not norm(n): continue
        seen.add(k); out.append((n, p))
    # menu date
    date = None
    m = re.search(r'Last updated:?\s*([A-Z][a-z]+)\s+(\d{4})', text)
    if m:
        try: date = datetime.datetime.strptime(m.group(1)[:3] + ' ' + m.group(2), '%b %Y').date().isoformat()
        except Exception: date = None
    # 'Menu Date: 2026' (year only) is not a real date — the sweep day stands in for it
    return out, date

# ---------------------------------------------------------------- matching
def best_match(catalog_name, found):
    """Match a catalog drink to a parsed (name, price). Conservative on purpose."""
    cn = norm(catalog_name)
    if not cn: return None
    best, score = None, 0.0
    for n, p in found:
        fn = norm(n)
        if not fn: continue
        if fn == cn: return (n, p, 1.0)
        if cn in fn or fn in cn:
            s = 0.9 if min(len(cn), len(fn)) >= 6 else 0.6
        else:
            s = difflib.SequenceMatcher(None, cn, fn).ratio()
        if s > score: best, score = (n, p), s
    return (best[0], best[1], score) if best and score >= 0.86 else None

def fmt_price(p): return '$%.2f' % p

# ---------------------------------------------------------------- main
def main(argv):
    if len(argv) > 1 and argv[1] == '--test':
        items, date = parse_menu(open(argv[2], encoding='utf-8', errors='ignore').read())
        print('date:', date); [print('  %-45s %s' % (n[:45], fmt_price(p))) for n, p in items]; return 0
    dry = '--dry-run' in argv
    catalog = json.load(open('drinks-catalog.json'))['parks']
    sources = json.load(open('sources.json'))
    try: feed = json.load(open('drinks-menu.json'))
    except Exception: feed = {}
    feed.setdefault('version', 1); feed.setdefault('items', []); feed.setdefault('venues', []); feed.setdefault('removed', []); feed.setdefault('sources', [])
    feed['about'] = ('Live overrides for drinks.html, rewritten daily by sweep.py (GitHub Actions, no model in the loop). '
                     'Exact prices are read from public menu pages; everything not seen stays an estimate in the app.')
    index = {}   # (park, venue, name) lower -> item
    for it in feed['items']: index[(it['park'], it['venue'].lower(), it['name'].lower())] = it
    state = feed.setdefault('sweepState', {})   # venue key -> {lastOk, lastSeen:{name: date}}
    stats = {'pages': 0, 'ok': 0, 'verified': 0, 'changed': 0, 'added': 0, 'removed': 0, 'venues': 0}
    used_sources = []
    for pk, park in catalog.items():
        for venue in park['venues']:
            vkey = pk + '|' + venue['name']
            urls = sources.get(venue['name']) or sources.get(vkey) or []
            if not urls: continue
            stats['venues'] += 1
            found, date, best_n, best_url = [], None, 0, None
            for url in urls:
                body, status = fetch(url); stats['pages'] += 1
                time.sleep(0.6)
                if not body: continue
                its, d = parse_menu(body)
                n = sum(1 for dd in venue['drinks'] if best_match(dd['name'], its))
                if n > best_n: found, date, best_n, best_url = its, d, n, url
                if best_n >= 3: break
            if best_url: stats['ok'] += 1; used_sources.append(best_url)
            vs = state.setdefault(vkey, {'lastSeen': {}})
            if not found: continue
            vs['lastOk'] = TODAY
            checked = TODAY            # the day we saw the price live; the page's own date rides along as menuDate
            seen_names = set()
            for d in venue['drinks']:
                m = best_match(d['name'], found)
                if not m: continue
                seen_names.add(d['name'].lower())
                vs['lastSeen'][d['name']] = TODAY
                key = (pk, venue['name'].lower(), d['name'].lower())
                it = index.get(key)
                price = fmt_price(m[1])
                if it is None:
                    it = {'park': pk, 'venue': venue['name'], 'name': d['name']}
                    feed['items'].append(it); index[key] = it
                prev = it.get('price')
                if it.get('status') == 'verified' and it.get('checked') and checked < it['checked']:
                    continue   # never overwrite with an older-dated menu
                if prev and prev != price and it.get('status') == 'verified':
                    it['note'] = 'Was ' + prev + ' until ' + checked
                elif prev == price and it.get('note', '').startswith('Was '):
                    pass
                    stats['changed'] += 1
                it['price'] = price; it['status'] = 'verified'; it['checked'] = checked
                if date: it['menuDate'] = date
                else: it.pop('menuDate', None)
                if m[0].strip().lower() != d['name'].strip().lower(): it['listedAs'] = m[0].strip()
                stats['verified'] += 1
            # removal: verified drinks not seen on the page for a while
            for d in venue['drinks']:
                key = (pk, venue['name'].lower(), d['name'].lower())
                it = index.get(key)
                if not it or it.get('status') != 'verified' or d['name'].lower() in seen_names: continue
                last = vs['lastSeen'].get(d['name'])
                if last and (datetime.date.fromisoformat(TODAY) - datetime.date.fromisoformat(last)).days >= MISSING_DAYS_BEFORE_REMOVED:
                    if not any(r['park'] == pk and r['venue'] == venue['name'] and r['name'] == d['name'] for r in feed['removed']):
                        feed['removed'].append({'park': pk, 'venue': venue['name'], 'name': d['name'], 'since': TODAY}); stats['removed'] += 1
    # expire very old verified prices back to estimates
    cutoff = (datetime.date.fromisoformat(TODAY) - datetime.timedelta(days=MAX_AGE_DAYS_KEEP)).isoformat()
    feed['items'] = [it for it in feed['items'] if not (it.get('status') == 'verified' and it.get('checked', '9999') < cutoff and not it.get('add'))]
    feed['checkedAt'] = TODAY
    srcs = [s for s in feed['sources'] if s not in used_sources] + used_sources
    feed['sources'] = srcs[-25:]
    feed['items'].sort(key=lambda i: (i['park'], i['venue'].lower(), i['name'].lower()))
    summary = 'Menu sweep %s: %d venues, %d pages ok, %d verified (%d changed), %d added, %d removed' % (TODAY, stats['venues'], stats['ok'], stats['verified'], stats['changed'], stats['added'], stats['removed'])
    print(summary)
    if dry: return 0
    json.dump(feed, open('drinks-menu.json', 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    open('.sweep-summary', 'w').write(summary)
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
