#!/usr/bin/env python3
"""Randomized sweeps of the Wayback Machine's capture index, run on GitHub's servers.

Browsers can't query the index from a web page, so this job samples it here and writes
small JSON chunks beside the page, which Flash Summoner reads a
chunk at a time. Images are then shown straight from web.archive.org.

Each group lists URL prefixes. For each prefix the index is split into blocks; the job
picks blocks at random, keeps captured images that pass the filters, sorts each by what
its file name says (s stock, p paparazzi, e red carpet, x early web), mixes the results
with what earlier runs found, and rewrites data/wb-<kind>-NN.json.
"""
import json, os, random, re, sys, time, urllib.parse, urllib.request

CDX = 'https://web.archive.org/cdx/search/cdx'
UA = {'User-Agent': 'Flash Summoner harvest (github.com/mh6crk2hhm-web/archive-summona)'}
OUT = os.path.join(os.path.dirname(__file__), '..', 'data')
DEADLINE = time.time() + 100 * 60
CHUNK, CAP = 800, 60000
log_lines = []

def log(*a):
    s = ' '.join(str(x) for x in a)
    print(s, flush=True); log_lines.append(s)

def cdx(params, tries=4):
    url = CDX + '?' + urllib.parse.urlencode(params, doseq=True)
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception as e:
            log('  retry', i + 1, type(e).__name__, str(e)[:120])
            time.sleep(15 * (i + 1))
    return None

AT_LEAST_40KB = r'^([4-9][0-9]{4}|[0-9]{6,})$'
AT_LEAST_25KB = r'^([2-9][0-9]{4}|[0-9]{6,})$'
AT_LEAST_15KB = r'^(1[5-9][0-9]{3}|[2-9][0-9]{4}|[0-9]{6,})$'
IMG = 'mimetype:image/(jpeg|png|gif)'
JUNK = re.compile(r'(logo|banner|avatar|icon|button|sprite|header|footer|spacer|pixel|badge|_t\.|-150x150|/ads?/|/static/|/assets/)', re.I)

# What a file name says about the shot. Sightings are paparazzi; events are red carpet.
CANDID = re.compile(r'(sighting|spotted|out-and-about|out_and_about|seen-|leaves|leaving|leave-|arrives-at-lax|at-lax|airport|shopping|grocery|'
                    r'gas-station|coffee|lunch|dinner-at|filming|on-set|on-location|walks|walking|jogging|gym|beach|vacation|holiday|errand|'
                    r'paparazzi|candid|steps-out|stepping-out|heads-to|out-in-|in-her-car|in-his-car|parking)', re.I)
EVENT = re.compile(r'(premiere|arrivals|arrive|red-carpet|awards|gala|party|photocall|festival|fashion-week|runway|show|portrait|'
                   r'concert|benefit|screening|launch|ceremony|celebration|press-conference|junket|dinner|ball|opening|honors|tribute)', re.I)
STRONG_EVENT = re.compile(r'(premiere|red-carpet|awards|gala|photocall|arrivals)', re.I)
YEAR = re.compile(r'(19[5-9]\d|20[0-2]\d)')

def slug(orig):
    return urllib.parse.unquote(orig.split('?')[0].rstrip('/').rsplit('/', 1)[-1])

def editorial(ts, orig):
    if JUNK.search(orig): return None
    sl = slug(orig)
    if STRONG_EVENT.search(sl): return 'e'
    if CANDID.search(sl): return 'p'
    if EVENT.search(sl): return 'e'
    return 'e' if YEAR.search(sl) else 's'

def editorial_default_p(ts, orig):
    k = editorial(ts, orig)
    return 'p' if k == 's' else k

def stock(ts, orig):
    return None if JUNK.search(orig) else 's'

DA_STRONG_EVENT = re.compile(r'(premiere|red-carpet|awards|gala|photocall|arrivals)', re.I)
YEAR = re.compile(r'/(?:f|i)/((?:19|20)\d\d)/')
def deviantart(ts, orig):
    m = DA_YEAR.search(orig)
    return 'x' if m and 2000 <= int(m.group(1)) <= 2013 and int(ts[:4]) <= 2016 else None

DM_STRONG_EVENT = re.compile(r'(premiere|red-carpet|awards|gala|photocall|arrivals)', re.I)
YEAR = re.compile(r'/i/pix/(20\d\d)/')
def tabloid(ts, orig):
    if JUNK.search(orig): return None
    m = DM_YEAR.search(orig)
    if m and int(m.group(1)) > 2016: return None
    k = editorial(ts, orig)
    return 'e' if k == 'e' else 'p'

GROUPS = [
    {'name': 'DeviantArt image servers', 'size': AT_LEAST_25KB, 'blocks': 10, 'kind': deviantart,
     'prefixes': ['fc0%d.deviantart.net/' % n for n in range(1, 10)] + ['fc00.deviantart.net/']},
    {'name': 'Getty Images and WireImage previews', 'size': AT_LEAST_25KB, 'blocks': 8, 'kind': editorial,
     'prefixes': ['media.gettyimages.com/photos/', 'media.gettyimages.com/id/', 'cache.gettyimages.com/', 'cache1.asset-cache.net/',
                  'cache2.asset-cache.net/', 'cache3.asset-cache.net/', 'cache4.asset-cache.net/', 'www.wireimage.com/', 'www.filmmagic.com/',
                  'www.gettyimages.com/detail/']},
    {'name': 'Shutterstock and Rex editorial previews', 'size': AT_LEAST_25KB, 'blocks': 8, 'kind': editorial_default_p,
     'prefixes': ['editorial01.shutterstock.com/', 'editorial02.shutterstock.com/', 'editorial03.shutterstock.com/', 'www.rexfeatures.com/',
                  'www.rexusa.com/', 'images.rexfeatures.com/', 'www.shutterstock.com/editorial/']},
    {'name': 'Shutterstock stock previews', 'size': AT_LEAST_15KB, 'blocks': 8, 'kind': stock,
     'prefixes': ['image.shutterstock.com/display_pic_with_logo/', 'image.shutterstock.com/z/', 'image.shutterstock.com/image-photo/',
                  'thumb1.shutterstock.com/display_pic_with_logo/', 'thumb7.shutterstock.com/display_pic_with_logo/',
                  'thumb9.shutterstock.com/display_pic_with_logo/', 'thumb10.shutterstock.com/display_pic_with_logo/',
                  'image.shutterstock.com/image-vector/']},
    {'name': 'Tabloids and gossip blogs', 'size': AT_LEAST_40KB, 'blocks': 6, 'kind': tabloid,
     'prefixes': ['justjared.buzznet.com/wp-content/uploads/', 'www.justjared.com/wp-content/uploads/', 'cdn.justjared.com/wp-content/uploads/',
                  'cdn01.justjared.com/wp-content/uploads/', 'cdn02.justjared.com/wp-content/uploads/', 'cdn03.justjared.com/wp-content/uploads/',
                  'cdn04.justjared.com/wp-content/uploads/', 'justjaredjr.buzznet.com/wp-content/uploads/']
                 + ['i.dailymail.co.uk/i/pix/%d/' % y for y in range(2006, 2017)]
                 + ['pagesix.com/wp-content/uploads/', 'nypost.com/wp-content/uploads/', 'radaronline.com/wp-content/uploads/',
                    'www.usmagazine.com/wp-content/', 'img.wennermedia.com/', 'akns-images.eonline.com/', 'img2.timeinc.net/people/i/',
                    'www.thesun.co.uk/', 'i2-prod.mirror.co.uk/', 'www.okmagazine.com/', 'www.intouchweekly.com/',
                    'www.x17online.com/', 'x17online.com/', 'perezhilton.com/wp-content/uploads/', 'www.perezhilton.com/',
                    'll-media.tmz.com/', 'www.thesuperficial.com/', 'www.egotastic.com/', 'www.hollywoodtuna.com/', 'www.dlisted.com/',
                    'www.celebuzz.com/', 'www.pacificcoastnews.com/', 'www.splashnewsonline.com/', 'www.bauergriffin.com/',
                    'www.fame-pictures.com/', 'hollywoodlife.com/wp-content/uploads/', 'www.socialitelife.com/', 'www.celebitchy.com/',
                    'www.gotceleb.com/', 'www.hawtcelebs.com/']},
]
KINDS = ['s', 'p', 'e', 'x']

def sample_prefix(prefix, cfg):
    base = {'url': prefix, 'matchType': 'prefix', 'pageSize': '1'}
    raw = cdx(dict(base, showNumPages='true'))
    try: n = int((raw or '').strip())
    except ValueError: n = 0
    if n <= 0:
        log('  %-52s empty' % prefix); return []
    picks = random.sample(range(n), min(n, cfg['blocks']))
    found = []
    for k in picks:
        if time.time() > DEADLINE: break
        text = cdx(dict(base, page=str(k), fl='timestamp,original', filter=['statuscode:200', IMG, 'length:' + cfg['size']]))
        time.sleep(2)
        for line in (text or '').splitlines():
            parts = line.split(' ', 1)
            if len(parts) != 2: continue
            ts, orig = parts
            if len(ts) != 14: continue
            k = cfg['kind'](ts, orig)
            if k: found.append([k, ts, orig])
    log('  %-52s %6d blocks, sampled %2d, kept %5d' % (prefix, n, len(picks), len(found)))
    return found

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

def main():
    os.makedirs(OUT, exist_ok=True)
    jobs = [(g, pre) for g in GROUPS for pre in g['prefixes']]
    random.shuffle(jobs)  # if time runs out, what got sampled is still a fair spread
    new = {k: [] for k in KINDS}
    for g, pre in jobs:
        if time.time() > DEADLINE: log('out of time'); break
        for k, ts, orig in sample_prefix(pre, g):
            new[k].append([ts, orig])
    for k in KINDS:
        seen, merged = set(), []
        for e in new[k] + load(k):
            kk = key(e[1])
            if kk in seen: continue
            seen.add(kk); merged.append(e)
        merged = merged[:CAP]
        random.shuffle(merged)
        if merged: save(k, merged)
        log('kind %s: %d new this run, %d in total' % (k, len(new[k]), len(merged)))
    with open(os.path.join(OUT, 'harvest-log.txt'), 'w') as f:
        f.write('\n'.join(log_lines) + '\n')

if __name__ == '__main__':
    main()
