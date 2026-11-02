"""Auto-discovery of jobs from a careers page URL.

Order of attempts:
1. Known job boards with public JSON APIs (Greenhouse, Lever, Ashby,
   Workable, SmartRecruiters). Also detected if the company page just
   embeds / links to one of them.
2. schema.org JobPosting data (JSON-LD) in the page.
3. Fallback: links on the page that look like job postings.
"""
import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 30


@dataclass
class Job:
    job_id: str
    title: str
    url: str
    location: str = ""


# ---------------------------------------------------------------- helpers
def _get(url, **kw):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def _clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


# ------------------------------------------------------------ known boards
def _greenhouse(token):
    data = _get(
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    ).json()
    return [
        Job(
            job_id=str(j["id"]),
            title=_clean(j.get("title")),
            url=j.get("absolute_url", ""),
            location=_clean((j.get("location") or {}).get("name")),
        )
        for j in data.get("jobs", [])
    ]


def _lever(company):
    data = _get(f"https://api.lever.co/v0/postings/{company}?mode=json").json()
    return [
        Job(
            job_id=j["id"],
            title=_clean(j.get("text")),
            url=j.get("hostedUrl", ""),
            location=_clean((j.get("categories") or {}).get("location")),
        )
        for j in data
    ]


def _ashby(org):
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{org}").json()
    return [
        Job(
            job_id=j["id"],
            title=_clean(j.get("title")),
            url=j.get("jobUrl", ""),
            location=_clean(j.get("location")),
        )
        for j in data.get("jobs", [])
    ]


def _workable(account):
    jobs, token = [], None
    for _ in range(20):  # safety cap on pages
        body = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
        if token:
            body["token"] = token
        r = requests.post(
            f"https://apply.workable.com/api/v3/accounts/{account}/jobs",
            json=body, headers=HEADERS, timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        for j in data.get("results", []):
            loc = j.get("location") or {}
            jobs.append(
                Job(
                    job_id=j["shortcode"],
                    title=_clean(j.get("title")),
                    url=f"https://apply.workable.com/{account}/j/{j['shortcode']}/",
                    location=_clean(", ".join(
                        x for x in (loc.get("city"), loc.get("country")) if x
                    )),
                )
            )
        token = data.get("nextPage")
        if not token:
            break
    return jobs


def _smartrecruiters(company):
    jobs, offset = [], 0
    for _ in range(20):
        data = _get(
            f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
            f"?limit=100&offset={offset}"
        ).json()
        content = data.get("content", [])
        for j in content:
            loc = j.get("location") or {}
            jobs.append(
                Job(
                    job_id=j["id"],
                    title=_clean(j.get("name")),
                    url=f"https://jobs.smartrecruiters.com/{company}/{j['id']}",
                    location=_clean(", ".join(
                        x for x in (loc.get("city"), loc.get("country")) if x
                    )),
                )
            )
        offset += len(content)
        if not content or offset >= data.get("totalFound", 0):
            break
    return jobs


def _workday(host, tenant, site):
    """Workday career sites (*.myworkdayjobs.com) have a public JSON endpoint."""
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    jobs, offset, total = [], 0, None
    for _ in range(50):  # 50 pages x 20 = 1000 jobs max
        r = requests.post(
            api,
            json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""},
            headers=HEADERS, timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        postings = data.get("jobPostings", [])
        if total is None:  # Workday only reports the total on the first page
            total = data.get("total", 0)
        for j in postings:
            path = j.get("externalPath", "")
            if not path:
                continue
            jobs.append(
                Job(
                    job_id=path,
                    title=_clean(j.get("title")),
                    url=f"https://{host}/en-US/{site}{path}",
                    location=_clean(j.get("locationsText")),
                )
            )
        offset += len(postings)
        if not postings or offset >= total:
            break
    return jobs


AMAZON_COUNTRY = "IND"  # Amazon search is limited to India; change or set to "" for worldwide


def _amazon(keywords):
    """amazon.jobs has its own search API. One query per keyword."""
    seen, jobs = set(), []
    for q in (keywords or [""]):
        offset = 0
        for _ in range(10):  # 10 pages x 100 per keyword
            params = {
                "base_query": q, "result_limit": 100,
                "offset": offset, "sort": "recent",
            }
            if AMAZON_COUNTRY:
                params["country[]"] = AMAZON_COUNTRY
            data = _get("https://www.amazon.jobs/en/search.json", params=params).json()
            hits = data.get("jobs", [])
            for j in hits:
                jid = str(j.get("id_icims") or j.get("job_path"))
                if jid in seen:
                    continue
                seen.add(jid)
                jobs.append(
                    Job(
                        job_id=jid,
                        title=_clean(j.get("title")),
                        url="https://www.amazon.jobs" + j.get("job_path", ""),
                        location=_clean(j.get("normalized_location") or j.get("location")),
                    )
                )
            offset += len(hits)
            if not hits or offset >= data.get("hits", 0):
                break
    return jobs


# (regex over a URL or page HTML, handler). Handlers get the regex groups.
BOARD_PATTERNS = [
    (re.compile(r"(([\w-]+)\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Za-z]{2}/)?([\w-]+)"), _workday),
    (re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"), _greenhouse),
    (re.compile(r"jobs\.lever\.co/([\w-]+)"), _lever),
    (re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)"), _ashby),
    (re.compile(r"apply\.workable\.com/([\w-]+)"), _workable),
    (re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)"), _smartrecruiters),
]
IGNORED_TOKENS = {"embed", "v1", "api", "static", "assets"}


def _try_known_boards(text):
    for pattern, handler in BOARD_PATTERNS:
        for m in pattern.finditer(text):
            token = m.group(1)
            if token.lower() in IGNORED_TOKENS:
                continue
            try:
                jobs = handler(*m.groups())
            except Exception as e:  # wrong token / board not public
                log.debug("%s(%s) failed: %s", handler.__name__, token, e)
                continue
            if jobs:
                log.info("Detected %s board '%s' (%d jobs)",
                         handler.__name__.strip("_"), token, len(jobs))
                return jobs
    return None


# ------------------------------------------------------------ generic page
def _fetch_html(url, render_js):
    if render_js:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "render_js needs playwright: pip install playwright && "
                "playwright install chromium"
            )
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=HEADERS["User-Agent"])
            page.goto(url, wait_until="networkidle", timeout=60000)
            html = page.content()
            browser.close()
        return html
    return _get(url).text


def _iter_jsonld(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from _iter_jsonld(x)
    elif isinstance(obj, dict):
        t = obj.get("@type")
        if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
            yield obj
        for key in ("@graph", "itemListElement", "item"):
            if key in obj:
                yield from _iter_jsonld(obj[key])


def _jobs_from_jsonld(soup, base_url):
    jobs = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for p in _iter_jsonld(data):
            title = _clean(p.get("title"))
            if not title:
                continue
            url = urljoin(base_url, p.get("url") or "") if p.get("url") else base_url
            loc = ""
            jl = p.get("jobLocation")
            if isinstance(jl, list) and jl:
                jl = jl[0]
            if isinstance(jl, dict):
                addr = jl.get("address") or {}
                if isinstance(addr, dict):
                    loc = _clean(", ".join(
                        str(addr.get(k)) for k in ("addressLocality", "addressCountry")
                        if addr.get(k)
                    ))
            ident = p.get("identifier")
            if isinstance(ident, dict):
                ident = ident.get("value")
            jobs.append(Job(job_id=str(ident or url + "#" + title), title=title, url=url, location=loc))
    return jobs


JOB_HREF = re.compile(
    r"/(jobs?|careers?|positions?|openings?|vacanc(?:y|ies)|requisitions?|postings?|opportunit(?:y|ies)|apply)(/|\?|-|_|$)",
    re.I,
)
SKIP_TEXT = re.compile(
    r"^(apply( now)?|view( all)?( jobs| openings| positions)?|learn more|read more|"
    r"search( jobs)?|careers?|jobs?|open (roles|positions)|see (all )?(jobs|roles)|"
    r"back|home|next|previous|sign in|log ?in|share|save|filter)$",
    re.I,
)


def _normalize(url):
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))


def _jobs_from_links(soup, base_url):
    base_path = urlparse(base_url).path.rstrip("/")
    seen, jobs = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = _normalize(urljoin(base_url, href))
        if url in seen or url == _normalize(base_url):
            continue
        path = urlparse(url).path
        if path.rstrip("/") == base_path:
            continue
        if not JOB_HREF.search(path):
            continue
        # prefer a heading inside the link, else the link text
        heading = a.find(["h1", "h2", "h3", "h4", "h5"])
        title = _clean(heading.get_text(" ") if heading else a.get_text(" "))
        if not (4 <= len(title) <= 140) or SKIP_TEXT.match(title):
            continue
        seen.add(url)
        jobs.append(Job(job_id=url, title=title, url=url))
    return jobs


# ------------------------------------------------------------- entry point
def _extract(html, careers_url):
    """Look for jobs inside an already-fetched page."""
    # 1b. Page embeds / links to a known board
    jobs = _try_known_boards(html)
    if jobs:
        return jobs

    soup = BeautifulSoup(html, "lxml")

    # 2. Structured data
    jobs = _jobs_from_jsonld(soup, careers_url)
    if jobs:
        log.info("Found %d jobs via JSON-LD", len(jobs))
        return jobs

    # 3. Link heuristics
    jobs = _jobs_from_links(soup, careers_url)
    log.info("Found %d jobs via link heuristics", len(jobs))
    return jobs


def _playwright_available():
    import importlib.util
    return importlib.util.find_spec("playwright") is not None


def discover_jobs(careers_url, render_js="auto", keywords=None):
    """Return a list of Job objects found at careers_url.

    render_js: "auto" (plain page first, browser if nothing found),
               True (always browser) or False (never browser).

    Raises on network errors so the caller can skip the run instead of
    treating a failed fetch as 'no jobs'.
    """
    host = urlparse(careers_url).netloc.lower()
    if host.endswith("amazon.jobs"):
        return _amazon(keywords)

    # 1. URL itself points at a known board
    jobs = _try_known_boards(careers_url)
    if jobs:
        return jobs

    error = None
    try:
        html = _fetch_html(careers_url, render_js is True)
        jobs = _extract(html, careers_url)
    except Exception as e:
        jobs, error = [], e

    # Plain fetch found nothing or was blocked: try a real browser
    if not jobs and render_js == "auto" and _playwright_available():
        log.info("Nothing found with a plain request, retrying with a browser")
        try:
            html = _fetch_html(careers_url, True)
            jobs = _extract(html, careers_url)
            error = None
        except Exception as e:
            error = error or e

    if not jobs and error:
        raise error
    return jobs
