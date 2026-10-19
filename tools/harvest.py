#!/usr/bin/env python3
"""Randomized sweeps of the Wayback Machine's capture index, run on GitHub's servers.

Browsers can't query the index from a web page, so this job samples it here and writes
small JSON chunks beside the page (data/wb-<kind>-NN.json), which Flash Summoner reads a
chunk at a time. Images are then shown straight from web.archive.org.

Broad queries over the index are slow, so every query here aims at a narrow random spot:
a dated upload folder plus a random first letter ("www.justjared.com/wp-content/uploads/
2009/07/k"), or a random two-letter start of a file name that describes the picture
("media.gettyimages.com/photos/je"). Narrow prefix queries return in seconds. Each kept
image is sorted by what its file name says: s stock, p paparazzi, e red carpet, x early web.
"""
import json, os, random, re, string, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

CDX = 'https://web.archive.org/cdx/search/cdx'
UA = {'User-Agent': 'Flash Summoner harvest (github.com/mh6crk2hhm-web/archive-summona)'}
OUT = os.path.join(os.path.dirname(__file__), '..', 'data')
MINUTES = float(os.environ.get('HARVEST_MINUTES', '90'))
DEADLINE = time.time() + MINUTES * 60
WORKERS = 2  # the archive starts refusing connections when pushed harder
CHUNK, CAP = 800, 80000
log_lines, errors, hits, tried = [], {}, {}, {}

def log(*a):
    s = ' '.join(str(x) for x in a)
    print(s, flush=True); log_lines.append(s)

def cdx(params, tries=2):
    url = CDX + '?' + urllib.parse.urlencode(params, doseq=True)
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception as e:
            msg = (type(e).__name__ + ' ' + str(e))[:90]
            errors[msg] = errors.get(msg, 0) + 1
            time.sleep(8 * (i + 1))
    return None

AT_LEAST_40KB = r'^([4-9][0-9]{4}|[0-9]{6,})$'
AT_LEAST_25KB = r'^([2-9][0-9]{4}|[0-9]{6,})$'
AT_LEAST_12KB = r'^(1[2-9][0-9]{3}|[2-9][0-9]{4}|[0-9]{6,})$'
JUNK = re.compile(r'(logo|banner|avatar|icon|button|sprite|header|footer|spacer|pixel|badge|_t\.|-\d{2,3}x\d{2,3}\.|/ads?/|/static/|/assets/|thumb|s=170x170|[?&]w=[1-3]\d\d\b)', re.I)

# What a file name says about the shot. Sightings are paparazzi; events are red carpet.
# Fashion outranks everything else: a runway look, a street-style guest or a front row is a fashion picture.
FASHION = re.compile(r'(runway|catwalk|walks-the-runway|walk-the-runway|guest-wears|guest-wearing|guest-is-wearing|street-style|streetstyle|'
                     r'lookbook|look-book|fashion-show|fashionshow|fashion-week|front-row|modeshow|mode-show|show-during)', re.I)
STRONG_EVENT = re.compile(r'(premiere|red-carpet|awards|gala|photocall|arrivals)', re.I)
CANDID = re.compile(r'(sighting|spotted|out-and-about|out_and_about|seen-|leaves|leaving|leave-|arrives-at-lax|at-lax|airport|shopping|grocery|'
                    r'gas-station|coffee|lunch|dinner-at|filming|on-set|on-location|walks|walking|jogging|gym|beach|vacation|holiday|errand|'
                    r'paparazzi|candid|steps-out|stepping-out|heads-to|out-in-|in-her-car|in-his-car|parking)', re.I)
EVENT = re.compile(r'(arrive|party|festival|fashion-week|runway|show|portrait|concert|benefit|screening|launch|ceremony|celebration|'
                   r'press-conference|junket|dinner|ball|opening|honors|tribute)', re.I)
YEAR = re.compile(r'(19[5-9]\d|20[0-2]\d)')

def slug(orig):
    # Zimbio writes captions with plus signs: Jennifer+Aniston+Out+About+LA
    return urllib.parse.unquote(orig.split('?')[0].rstrip('/').rsplit('/', 1)[-1]).replace('+', '-').lower()

# Getty's own captions are formulaic: a sighting says so. "beach" or "seen from" alone is a landscape.
GETTY_CANDID = re.compile(r'(sighting|is-seen|are-seen|seen-on-|seen-in-|seen-out|spotted|out-and-about|leaves-|leaving-|departs-|'
                          r'arrives-at-lax|at-lax|paparazzi|steps-out|stepping-out|out-shopping|grabs-|shopping-in)', re.I)
SPORT = re.compile(r'(match|league|football|soccer|nba|nfl|mlb|nhl|race|racing|cup-|championship|olympic|tennis|golf|grand-prix|'
                   r'in-action|during-the|tries-to|scores|player|team|stadium|quarterback|pitcher|goalkeeper|playoff|tournament|'
                   r'wrestl|boxing|ufc|marathon|rally|porsche|ferrari|formula|fifa|pitch|livestream)', re.I)

def editorial(ts, orig):
    if JUNK.search(orig): return None
    sl = slug(orig)
    if SPORT.search(sl): return None
    if FASHION.search(sl): return 'f'
    if STRONG_EVENT.search(sl): return 'e'
    if GETTY_CANDID.search(sl): return 'p'
    if EVENT.search(sl) or re.search(r'(attend|host|pose|perform)', sl, re.I): return 'e'
    return None if YEAR.search(sl) else 's'

# Article furniture: header graphics, promos, screenshots and collages rather than photographs of someone.
FURNITURE = re.compile(r'(-ftr|-lead|_lead|livestream|-watch|cast-photos|promo|poster|trailer|screenshot|screen-shot|tweet|instagram|'
                       r'collage|split|graphic|gif|meme|quiz|vs-|-vs|split|side-by-side)', re.I)

def gossip(ts, orig):
    # a gossip blog's upload folder is mostly paparazzi; events only when the file name says so
    if JUNK.search(orig): return None
    sl = slug(orig)
    if FURNITURE.search(sl) or SPORT.search(sl): return None
    if FASHION.search(sl): return 'f'
    if STRONG_EVENT.search(sl) or (EVENT.search(sl) and not CANDID.search(sl)): return 'e'
    return 'p'

def stock(ts, orig):
    return None if JUNK.search(orig) else 's'

DA_YEAR = re.compile(r'/(?:f|i)/((?:19|20)\d\d)/')
DA_JUNK = re.compile(r'(profile_?pic|avatar|icon|(^|/)id_|_id_|_id\.|stamp|banner|journal|signature|button|_base|f2u|free_to_use|'
                     r'preview|thumb|emote|pixel_?dolls?)', re.I)
def deviantart(ts, orig):
    if DA_JUNK.search(slug(orig)): return None
    m = DA_YEAR.search(orig)
    return 'x' if m and 2000 <= int(m.group(1)) <= 2013 and int(ts[:4]) <= 2016 else None

LOWER = string.ascii_lowercase
# Two-letter starts weighted toward how names and stock captions begin.
STARTS = ['ja', 'je', 'ju', 'jo', 'ka', 'ke', 'ki', 'ma', 'me', 'mi', 'li', 'la', 'le', 'ra', 're', 'ri', 'ro', 'sa', 'se', 'sh', 'st', 'ta', 'ti',
          'to', 'ch', 'ca', 'co', 'cl', 'da', 'de', 'di', 'dr', 'el', 'em', 'er', 'ga', 'ge', 'gw', 'ha', 'he', 'hi', 'ho', 'is', 'ad', 'al', 'am',
          'an', 'ar', 'as', 'au', 'be', 'bi', 'bl', 'br', 'na', 'ni', 'no', 'ol', 'or', 'pa', 'pe', 'ph', 'pr', 'ri', 'ry', 'sc', 'so', 'su', 'vi',
          'va', 'wi', 'za', 'ze', 'ky', 'kr', 'fr', 'fl', 'gi', 'gr', 'tr', 'ty']
WORDS = list(LOWER) + ['ab', 'ba', 'bu', 'ci', 'fa', 'fi', 'fo', 'hu', 'mo', 'mu', 'po', 'pi', 'sp', 'wo', 'yo']

def ym(y0, y1):
    return random.randint(y0, y1), random.randint(1, 12)

WP_BLOGS = [  # host, first year, last year: WordPress keeps uploads in /YYYY/MM/
    ('justjared.buzznet.com', 2006, 2009), ('www.justjared.com', 2009, 2016), ('cdn.justjared.com', 2012, 2016),
    ('justjaredjr.buzznet.com', 2007, 2009), ('www.justjaredjr.com', 2009, 2016),
    ('perezhilton.com', 2010, 2016), ('www.perezhilton.com', 2010, 2016), ('hollywoodlife.com', 2010, 2016),
    ('radaronline.com', 2010, 2016), ('pagesix.com', 2016, 2018), ('www.x17online.com', 2008, 2014),
    ('www.celebitchy.com', 2008, 2016), ('www.socialitelife.com', 2008, 2014), ('www.egotastic.com', 2009, 2016),
    ('www.thesuperficial.com', 2009, 2016), ('www.dlisted.com', 2010, 2016), ('www.celebuzz.com', 2009, 2014),
    ('www.hawtcelebs.com', 2013, 2016), ('www.gotceleb.com', 2010, 2016), ('www.popsugar.com', 2007, 2012),
    ('www.hollywoodtuna.com', 2008, 2013), ('www.celebslam.com', 2008, 2012), ('www.idontlikeyouinthatway.com', 2008, 2013),
]

CANDID_BLOGS = [('www.hawtcelebs.com', 2013, 2022), ('www.hawtcelebs.com', 2013, 2022), ('celebmafia.com', 2014, 2022),
                 ('celebmafia.com', 2014, 2022), ('hollywoodlife.com', 2010, 2018), ('www.celebzz.com', 2016, 2022)]

def candid_blog_prefix():
    host, a, b = random.choice(CANDID_BLOGS)
    y, m = ym(a, b)
    return 'tabloids', host, '%s/wp-content/uploads/%d/%02d/%s' % (host, y, m, random.choice(LOWER))

# Zimbio carried the paparazzi agencies' feeds, one folder per agency, captions in the file name.
ZIMBIO_AGENCIES = ['bg', 'fp', 'pc', 'mp', 'x1', 'gi', 'wi', 'fm', 'sp', 'ak', 'ps', 'ss']
def zimbio_prefix():
    host = 'www%d.pictures.zimbio.com' % random.randint(1, 4)
    return 'zimbio', host, '%s/%s/%s' % (host, random.choice(ZIMBIO_AGENCIES), random.choice(STARTS + list(string.ascii_uppercase)))

SIGHTING_CITIES = ['new-york-city', 'los-angeles', 'london', 'paris', 'milan', 'cannes', 'berlin', 'venice', 'toronto', 'sydney',
                   'miami', 'beverly-hills', 'west-hollywood', 'malibu', 'saint-tropez', 'rome', 'madrid', 'tokyo', 'hong-kong', 'st-barths']
MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december']
def getty_sightings_prefix():
    tail = '%s-%s' % (random.choice(SIGHTING_CITIES), random.choice(MONTHS)) if random.random() < 0.7 else random.choice(SIGHTING_CITIES)
    return 'getty', 'media.gettyimages.com (sightings)', 'media.gettyimages.com/photos/celebrity-sightings-in-' + tail

def wp_prefix():
    host, a, b = random.choice(WP_BLOGS)
    y, m = ym(a, b)
    return 'tabloids', host, '%s/wp-content/uploads/%d/%02d/%s' % (host, y, m, random.choice(LOWER))

DA_FS = [(n, 2006, 2007) for n in range(10, 20)] + [(n, 2007, 2008) for n in range(20, 30)] + \
        [(n, 2008, 2009) for n in range(30, 41)] + [(n, 2009, 2010) for n in range(41, 52)] + [(70, 2010, 2013), (71, 2011, 2013)] * 6

def da_prefix():
    fs, a, b = random.choice(DA_FS)
    host = 'fc%02d.deviantart.net' % random.randint(1, 9)
    return 'deviantart', host, '%s/fs%d/f/%d/%d' % (host, fs, random.randint(a, b), random.randint(0, 3))

def getty_prefix():
    return 'getty', 'media.gettyimages.com', 'media.gettyimages.com/photos/' + random.choice(STARTS)

def ss_prefix():
    kind = random.choice(['stock-photo-'] * 4 + ['stock-vector-'])
    return 'shutterstock', 'image.shutterstock.com', 'image.shutterstock.com/z/' + kind + random.choice(WORDS)

# Style.com, Conde Nast's runway site 2000-2015: one folder per show, named season + designer (F2009RTW-CHANEL).
def style_prefix():
    y = random.randint(2000, 2015)
    season = random.choice('FS') + str(y) + random.choice(['RTW', 'RTW', 'RTW', 'CTR', 'MEN'])
    letter = random.choice(string.ascii_uppercase)
    if random.random() < 0.5:
        return 'style.com', 'media.style.com', 'media.style.com/image/fashionshows/%s-%s' % (season, letter)
    return 'style.com', 'www.style.com', 'www.style.com/slideshows/%d/fashionshows/%s-%s' % (y, season, letter)

def runway(ts, orig):
    if re.search(r'(thumb|\d+t\.jpg|_t\.|/t/|sprite|logo)', orig, re.I): return None
    return 'f'

GETTY_FASHION = ['a-model-walks-the-runway-', 'model-walks-the-runway-', 'models-walk-the-runway-', 'a-model-walks-the-runway-at-the-',
                 'a-model-walks-the-runway-during-the-', 'models-walk-the-runway-finale-', 'a-guest-wears-', 'a-guest-is-wearing-',
                 'guest-wears-', 'a-guest-wearing-', 'street-style-', 'a-model-poses-backstage-']
def getty_fashion_prefix():
    start = random.choice(GETTY_FASHION)
    if start.endswith('-') and random.random() < 0.6: start += random.choice(LOWER)
    return 'getty', 'media.gettyimages.com (fashion)', 'media.gettyimages.com/photos/' + start

PLANS = [  # generator, how often, size filter, sorter (weighted toward candids: the thinnest pool)
    (zimbio_prefix, 8, AT_LEAST_25KB, gossip),
    (candid_blog_prefix, 5, AT_LEAST_25KB, gossip),
    (wp_prefix, 2, AT_LEAST_40KB, gossip),
    (getty_prefix, 1, AT_LEAST_25KB, editorial),
    (da_prefix, 2, AT_LEAST_25KB, deviantart),
    (ss_prefix, 1, AT_LEAST_12KB, stock),
    (style_prefix, 3, AT_LEAST_25KB, runway),
    (getty_fashion_prefix, 3, AT_LEAST_25KB, editorial),
]
WEIGHTED = [p for p in PLANS for _ in range(p[1])]

def one_query():
    gen, _, size, sorter = random.choice(WEIGHTED)
    group, host, prefix = gen()
    text = cdx({'url': prefix, 'matchType': 'prefix', 'fl': 'timestamp,original', 'collapse': 'urlkey', 'limit': '250',
                'filter': ['statuscode:200', 'mimetype:image/(jpeg|png|gif)', 'length:' + size]})
    out = []
    for line in (text or '').splitlines():
        parts = line.split(' ', 1)
        if len(parts) != 2 or len(parts[0]) != 14: continue
        k = sorter(parts[0], parts[1])
        if k: out.append((k, parts[0], parts[1]))
    return host, out

def sorter_for(orig):
    h = orig.split('/')[2].lower() if '//' in orig else ''
    if 'gettyimages' in h: return editorial
    if 'shutterstock' in h: return stock
    if 'deviantart' in h: return deviantart
    if 'style.com' in h: return runway
    return gossip

def key(orig):
    u = orig.split('?')[0].lower()
    return re.sub(r'^https?://(www\.)?', '', u).replace(':80/', '/')

def load(kind):
    meta = os.path.join(OUT, 'wb-%s-meta.json' % kind)
    out = []
    if os.path.exists(meta):
        for i in range(json.load(open(meta)).get('chunks', 0)):
            p = os.path.join(OUT, 'wb-%s-%02d.json' % (kind, i))
            if os.path.exists(p): out += json.load(open(p))
    return out

def save(kind, entries):
    meta = os.path.join(OUT, 'wb-%s-meta.json' % kind)
    old_n = json.load(open(meta)).get('chunks', 0) if os.path.exists(meta) else 0
    chunks = [entries[i:i + CHUNK] for i in range(0, len(entries), CHUNK)]
    for i, c in enumerate(chunks):
        json.dump(c, open(os.path.join(OUT, 'wb-%s-%02d.json' % (kind, i)), 'w'), separators=(',', ':'))
    for i in range(len(chunks), old_n):
        try: os.remove(os.path.join(OUT, 'wb-%s-%02d.json' % (kind, i)))
        except OSError: pass
    json.dump({'chunks': len(chunks), 'total': len(entries), 'built': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, open(meta, 'w'))

# Where a site's images really live is an open question until the index answers it.
PROBES = ['www.style.com/slideshows/', 'www.style.com/slideshows/2009/', 'media.style.com/', 'www.style.com/images/', 'img.style.com/',
          'www.style.com/fashionshows/', 'assets.style.com/', 'www.style.com/assets/', 'www.firstview.com/', 'www.nowfashion.com/']

def probe():
    lines = []
    for pre in PROBES:
        text = cdx({'url': pre, 'matchType': 'prefix', 'fl': 'original', 'collapse': 'urlkey', 'limit': '400',
                    'filter': ['statuscode:200', 'mimetype:image/(jpeg|png|gif)', 'length:' + AT_LEAST_25KB]}) or ''
        urls = text.split()
        picks = [urls[i] for i in sorted(random.sample(range(len(urls)), min(3, len(urls))))] if urls else []
        lines.append('%s -> %d%s' % (pre, len(urls), (': ' + ' ; '.join(u[:150] for u in picks)) if picks else ''))
        time.sleep(3)
    for i in range(0, len(lines), 3):
        print('::notice title=probe::' + ' || '.join(lines[i:i + 3]))

def main():
    os.makedirs(OUT, exist_ok=True)
    if os.environ.get('HARVEST_PROBE'): probe()
    kinds = ['s', 'p', 'e', 'x', 'f']
    new = {k: [] for k in kinds}
    queries = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        running = {pool.submit(one_query) for _ in range(WORKERS)}
        while running:
            for f in as_completed(running):
                running.discard(f)
                queries += 1
                try:
                    host, found = f.result()
                    tried[host] = tried.get(host, 0) + 1
                    if found: hits[host] = hits.get(host, 0) + len(found)
                    for k, ts, orig in found: new[k].append([ts, orig])
                except Exception as e:
                    errors[type(e).__name__] = errors.get(type(e).__name__, 0) + 1
                if time.time() < DEADLINE:
                    time.sleep(3)
                    running.add(pool.submit(one_query))
                if queries % 50 == 0:
                    log('%d queries; kept so far: %s' % (queries, ', '.join('%s %d' % (k, len(new[k])) for k in kinds)))
                break
    # Everything found so far is sorted again under the current rules, so a fix reaches old finds too.
    pool = [e for k in kinds for e in new[k]] + [e for k in kinds for e in load(k)]
    buckets, seen = {k: [] for k in kinds}, set()
    for ts, orig in pool:
        kk = key(orig)
        if kk in seen: continue
        seen.add(kk)
        k = sorter_for(orig)(ts, orig)
        if k: buckets[k].append([ts, orig])
    for k in kinds:
        merged = buckets[k]
        merged = merged[:CAP]
        random.shuffle(merged)
        if merged: save(k, merged)
        if not merged: save(k, [])
        log('kind %s: %d new this run, %d in total' % (k, len(new[k]), len(merged)))
    for host in sorted(tried, key=lambda h: -hits.get(h, 0)):
        log('  %-36s %4d queries, kept %6d' % (host, tried[host], hits.get(host, 0)))
    with open(os.path.join(OUT, 'harvest-log.txt'), 'w') as f:
        f.write('\n'.join(log_lines) + '\n')
    # Annotations are the one part of a run that can be read back without the Actions log.
    print('::notice title=kept::%d queries; ' % queries + ', '.join('%s %d' % (k, len(new[k])) for k in kinds))
    top = sorted(hits.items(), key=lambda kv: -kv[1])
    for i in range(0, min(len(top), 30), 10):
        print('::notice title=hosts::' + ' | '.join('%s %d' % kv for kv in top[i:i + 10]))
    dry = [h for h in tried if not hits.get(h)]
    if dry: print('::notice title=dry::' + ' | '.join('%s %d' % (h, tried[h]) for h in dry[:25]))
    for msg, n in sorted(errors.items(), key=lambda kv: -kv[1])[:5]:
        print('::warning title=errors::%dx %s' % (n, msg))

if __name__ == '__main__':
    main()
