from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from html import unescape
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple


DEFAULT_REGISTRY_PATH = "newsticker_registry.json"

DEFAULT_SEARCH_QUERIES: List[str] = [
    "world news rss",
    "maritime shipping port disruption news",
    "geopolitics energy infrastructure news",
    "shortwave teletext breaking news",
]

DEFAULT_RSS_FEEDS: List[str] = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
]

ROUTE_MODES: Tuple[str, ...] = ("auto", "rf_and_web", "web_only", "rf_only")


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _sigmoid(x: float) -> float:
    if x >= 0.0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _logit(p: float) -> float:
    c = _clamp(p, 1e-6, 1.0 - 1e-6)
    return math.log(c / (1.0 - c))


def _tokenize(text: str) -> Counter:
    words = re.findall(r"[a-z0-9]{2,}", text.lower())
    return Counter(words)


def _cosine_counter(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a.keys()) & set(b.keys())
    if not common:
        return 0.0
    dot = sum(a[k] * b[k] for k in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _seq_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower()[:240], b.lower()[:240]).ratio()


def _strip_html(raw: str) -> str:
    no_script = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    no_tags = re.sub(r"<[^>]+>", " ", no_script)
    cleaned = " ".join(unescape(no_tags).split())
    return cleaned.strip()


def _extract_domain(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def _parse_iso_ts(raw_ts: Optional[str], fallback: Optional[datetime] = None) -> datetime:
    base = fallback or datetime.now(timezone.utc)
    if not raw_ts:
        return base
    try:
        dt = datetime.fromisoformat(raw_ts)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return base


def normalize_route_mode(mode: Optional[str]) -> str:
    if not mode:
        return "auto"
    m = mode.strip().lower()
    mapping = {
        "auto": "auto",
        "automatic": "auto",
        "rf_and_web": "rf_and_web",
        "rf+web": "rf_and_web",
        "hybrid": "rf_and_web",
        "web_only": "web_only",
        "web": "web_only",
        "rss_only": "web_only",
        "rf_only": "rf_only",
        "rf": "rf_only",
        "radio_only": "rf_only",
    }
    return mapping.get(m, "auto")


def default_http_fetch(url: str, timeout_sec: float = 6.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "AmbientForecasterTicker/1.0 (+https://example.local)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=max(1.0, float(timeout_sec))) as resp:
        raw = resp.read()
        encoding = resp.headers.get_content_charset() or "utf-8"
        try:
            return raw.decode(encoding, errors="replace")
        except LookupError:
            return raw.decode("utf-8", errors="replace")


@dataclass
class ParsedClaim:
    excerpt: str
    certainty: str = "reported"
    certainty_multiplier: float = 0.85
    directional_hint: float = 0.0
    tokens: Counter = field(default_factory=Counter)
    topics: List[str] = field(default_factory=list)


def default_claim_parser(text: str) -> ParsedClaim:
    clean = " ".join(text.split()).strip()
    excerpt = clean if len(clean) <= 180 else clean[:177].rstrip() + "..."
    low = clean.lower()

    if any(w in low for w in ("confirmed", "official", "verified", "declared", "signed", "closed")):
        certainty = "confirmed"
        cert_mult = 1.00
    elif any(w in low for w in ("rumor", "unconfirmed", "speculation", "alleged", "unverified", "may ")):
        certainty = "speculative"
        cert_mult = 0.55
    else:
        certainty = "reported"
        cert_mult = 0.85

    pos_tokens = (
        "escalat", "surge", "outage", "strike", "attack", "closure", "blockade",
        "sanction", "emergency", "alert", "shortage", "spike", "warning", "suspend",
        "halt", "evacuat", "mobiliz", "conflict", "breach", "disrupt",
    )
    neg_tokens = (
        "ceasefire", "reopen", "restored", "resolved", "agreement", "de-escalat",
        "deescalat", "lift", "normal", "calm", "truce", "withdrew", "stabiliz",
    )
    pos_hits = sum(1 for t in pos_tokens if t in low)
    neg_hits = sum(1 for t in neg_tokens if t in low)
    if pos_hits == 0 and neg_hits == 0:
        directional_hint = 0.18
    else:
        directional_hint = _clamp((pos_hits - neg_hits) * 0.45, -1.0, 1.0)

    tokens = _tokenize(clean)
    stopwords = {
        "the", "and", "for", "with", "from", "that", "this", "into", "over",
        "after", "amid", "says", "said", "news", "report", "reports", "live",
    }
    topics = [w for w, _ in tokens.most_common(8) if w not in stopwords and len(w) >= 3][:4]

    return ParsedClaim(
        excerpt=excerpt or "(empty claim)",
        certainty=certainty,
        certainty_multiplier=cert_mult,
        directional_hint=directional_hint,
        tokens=tokens,
        topics=topics,
    )


def default_semantic_headline_synthesizer(
    cluster: "TopicCluster",
    res: "EvaluationResult",
    parsed: ParsedClaim,
) -> str:
    """Synthesizes a concise, grounded headline from cluster evidence and forecast state."""
    recent_texts = [ev.excerpt for ev in cluster.evidence[-3:] if ev.excerpt]
    base_headline = parsed.excerpt
    if len(recent_texts) >= 2:
        primary = recent_texts[-1]
        secondary = recent_texts[-2]
        if _seq_ratio(primary, secondary) < 0.65 and len(primary) + len(secondary) <= 155:
            base_headline = f"{primary} ({secondary})"
        else:
            base_headline = primary

    clean = " ".join(base_headline.split()).strip()
    if len(clean) > 180:
        clean = clean[:177].rstrip() + "..."
    return clean


@dataclass
class InterestProfile:
    prompt: str = "Consequential world, infrastructure, maritime, and geopolitical developments"
    focus_terms: List[str] = field(default_factory=list)
    in_interest_multiplier: float = 1.30
    out_of_interest_multiplier: float = 0.75

    def _effective_terms(self) -> Set[str]:
        if self.focus_terms:
            return {t.lower().strip() for t in self.focus_terms if t.strip()}
        words = re.findall(r"[a-z0-9]{4,}", self.prompt.lower())
        generic = {"world", "news", "developments", "consequential", "with", "from", "that", "this"}
        return {w for w in words if w not in generic}

    def score_text(self, text: str) -> float:
        terms = self._effective_terms()
        if not terms:
            return 0.5
        low = text.lower()
        hits = sum(1 for t in terms if t in low)
        return _clamp(0.30 + 0.25 * hits, 0.0, 1.0)

    def multiplier_for(self, score: float) -> float:
        if score >= 0.50:
            span = max(0.01, self.in_interest_multiplier - 1.0)
            return 1.0 + span * _clamp((score - 0.50) * 2.0, 0.0, 1.0)
        span = max(0.01, 1.0 - self.out_of_interest_multiplier)
        return 1.0 - span * _clamp((0.50 - score) * 2.0, 0.0, 1.0)


@dataclass
class FermiDriver:
    name: str
    assumption: str = ""
    probability: float = 0.50
    weight: float = 0.25

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "assumption": self.assumption,
            "probability": self.probability,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FermiDriver":
        return cls(
            name=str(data.get("name", "Driver")),
            assumption=str(data.get("assumption", "")),
            probability=_clamp(float(data.get("probability", 0.50)), 0.01, 0.99),
            weight=max(0.0, float(data.get("weight", 0.25))),
        )


@dataclass
class ForecastSnapshot:
    timestamp: datetime
    prior_prob: float
    posterior_prob: float
    directional_hint: float
    evidential_weight: float
    item_id: str
    reason: str = "evidence_update"


@dataclass
class ForecastQuestion:
    question_id: str
    topic_label: str
    question_text: str
    resolution_deadline: datetime
    impact_score: float = 0.65
    base_rate: Optional[float] = 0.25
    base_rate_weight: float = 0.35
    reference_class_note: str = "Empirical reference class prior"
    resolution_rule: str = "Resolves YES if event or escalation occurs before deadline; otherwise NO."
    domain_tag: str = "general"
    regime_tag: str = "baseline"
    horizon_hours: float = 168.0
    drivers: List[FermiDriver] = field(default_factory=list)
    current_probability: float = 0.45
    history: List[ForecastSnapshot] = field(default_factory=list)
    resolved_outcome: Optional[bool] = None

    def compute_fermi_prior(self) -> float:
        if self.drivers:
            total_w = sum(max(0.0, d.weight) for d in self.drivers)
            if total_w > 0.0:
                weighted_logit = sum(
                    max(0.0, d.weight) * _logit(_clamp(d.probability, 0.01, 0.99))
                    for d in self.drivers
                ) / total_w
                driver_p = _sigmoid(weighted_logit)
            else:
                driver_p = sum(_clamp(d.probability, 0.01, 0.99) for d in self.drivers) / len(self.drivers)
        else:
            driver_p = 0.45

        if self.base_rate is not None:
            br = _clamp(self.base_rate, 0.01, 0.99)
            w_br = _clamp(self.base_rate_weight, 0.0, 0.90)
            blended_logit = w_br * _logit(br) + (1.0 - w_br) * _logit(_clamp(driver_p, 0.01, 0.99))
            return _clamp(_sigmoid(blended_logit), 0.01, 0.99)

        return _clamp(driver_p, 0.01, 0.99)

    def brier_score(self) -> Optional[float]:
        if self.resolved_outcome is None:
            return None
        y = 1.0 if self.resolved_outcome else 0.0
        p = _clamp(self.current_probability, 0.0, 1.0)
        return (p - y) ** 2


@dataclass
class BroadcastItem:
    item_id: str
    channel: str
    frequency_band: str
    source_weight: float
    timestamp: datetime
    text: str
    transcription_confidence: float = 0.90
    metadata: dict = field(default_factory=dict)


@dataclass
class StoredEvidence:
    item_id: str
    channel: str
    band: str
    source_weight: float
    transcription_confidence: float
    timestamp: datetime
    text: str
    excerpt: str
    certainty: str
    directional_hint: float
    evidential_weight: float
    is_echo: bool
    origin_id: str
    tokens: Counter


@dataclass
class TopicCluster:
    cluster_id: str
    label: str
    domain_tag: str = "general"
    regime_tag: str = "baseline"
    keywords: Set[str] = field(default_factory=set)
    evidence: List[StoredEvidence] = field(default_factory=list)
    forecast: Optional[ForecastQuestion] = None
    last_alert_at: Optional[datetime] = None
    last_power_level: float = 0.0
    last_unexpectedness: float = 0.0


@dataclass
class EvaluationResult:
    timestamp: datetime
    cluster_id: str
    cluster_label: str
    item_id: str
    channel: str
    is_echo: bool
    interest_score: float
    interest_multiplier: float
    salience: float
    unexpectedness: float
    accumulation: float
    confidence: float
    power_level: float
    prior_prob: Optional[float]
    posterior_prob: Optional[float]
    provenance_tag: str
    trigger_reasons: List[str]
    alerted: bool
    ticker_line: Optional[str]
    supporting_excerpts: List[str]
    domain_tag: str = "general"
    regime_tag: str = "baseline"
    resolution_rule: str = ""
    synthesized_headline: Optional[str] = None
    is_warmup: bool = False


@dataclass
class DataSourceConfig:
    mode: str = "auto"
    enable_radio: bool = False
    enable_teletext: bool = False
    enable_rss: bool = True
    allow_web_fallback: bool = True
    local_rf_available: bool = False
    websdr_url: str = ""
    radio_stream_url: str = ""
    teletext_web_url: str = ""
    rss_feed_urls: List[str] = field(default_factory=lambda: list(DEFAULT_RSS_FEEDS))
    frequency_endpoints: List[str] = field(default_factory=list)
    request_timeout_sec: float = 6.0
    local_rf_reader: Optional[Callable[[], Iterable[BroadcastItem]]] = None
    frequency_scanner: Optional[Callable[[List[str]], Iterable[BroadcastItem]]] = None
    http_fetcher: Optional[Callable[[str, float], str]] = None

    def to_dict(self) -> dict:
        return {
            "mode": normalize_route_mode(self.mode),
            "enable_radio": self.enable_radio,
            "enable_teletext": self.enable_teletext,
            "enable_rss": self.enable_rss,
            "allow_web_fallback": self.allow_web_fallback,
            "local_rf_available": self.local_rf_available,
            "websdr_url": self.websdr_url,
            "radio_stream_url": self.radio_stream_url,
            "teletext_web_url": self.teletext_web_url,
            "rss_feed_urls": list(self.rss_feed_urls),
            "frequency_endpoints": list(self.frequency_endpoints),
            "request_timeout_sec": self.request_timeout_sec,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DataSourceConfig":
        rss_raw = data.get("rss_feed_urls")
        if rss_raw is None:
            rss_list = list(DEFAULT_RSS_FEEDS)
        else:
            rss_list = [str(u).strip() for u in rss_raw if str(u).strip()]
        freq_list = [str(f).strip() for f in data.get("frequency_endpoints", []) if str(f).strip()]
        return cls(
            mode=normalize_route_mode(data.get("mode", "auto")),
            enable_radio=bool(data.get("enable_radio", False)),
            enable_teletext=bool(data.get("enable_teletext", False)),
            enable_rss=bool(data.get("enable_rss", True)),
            allow_web_fallback=bool(data.get("allow_web_fallback", True)),
            local_rf_available=bool(data.get("local_rf_available", False)),
            websdr_url=str(data.get("websdr_url", "")),
            radio_stream_url=str(data.get("radio_stream_url", "")),
            teletext_web_url=str(data.get("teletext_web_url", "")),
            rss_feed_urls=rss_list,
            frequency_endpoints=freq_list,
            request_timeout_sec=max(1.0, float(data.get("request_timeout_sec", 6.0))),
        )


@dataclass
class DiscoveredSourceCandidate:
    url: str
    title: str
    source_type: str = "rss"
    domain: str = ""
    sample_headline: str = ""
    status: str = "pending"

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "source_type": self.source_type,
            "domain": self.domain or _extract_domain(self.url),
            "sample_headline": self.sample_headline,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DiscoveredSourceCandidate":
        url = str(data.get("url", "")).strip()
        return cls(
            url=url,
            title=str(data.get("title", "")).strip() or (_extract_domain(url) or url),
            source_type=str(data.get("source_type", "rss")).strip() or "rss",
            domain=str(data.get("domain", "")).strip() or _extract_domain(url),
            sample_headline=str(data.get("sample_headline", "")).strip(),
            status=str(data.get("status", "pending")).strip() or "pending",
        )


@dataclass
class SourceRegistryState:
    config: DataSourceConfig = field(default_factory=DataSourceConfig)
    wizard_completed: bool = True
    auto_crawl_enabled: bool = True
    auto_approve_discovered: bool = True
    crawl_interval_sec: float = 900.0
    search_queries: List[str] = field(default_factory=lambda: list(DEFAULT_SEARCH_QUERIES))
    index_urls: List[str] = field(default_factory=list)
    candidates: List[DiscoveredSourceCandidate] = field(default_factory=list)
    rejected_urls: Set[str] = field(default_factory=set)
    rejected_domains: Set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "config": self.config.to_dict(),
            "wizard_completed": self.wizard_completed,
            "auto_crawl_enabled": self.auto_crawl_enabled,
            "auto_approve_discovered": self.auto_approve_discovered,
            "crawl_interval_sec": self.crawl_interval_sec,
            "search_queries": list(self.search_queries),
            "index_urls": list(self.index_urls),
            "candidates": [c.to_dict() for c in self.candidates],
            "rejected_urls": sorted(self.rejected_urls),
            "rejected_domains": sorted(self.rejected_domains),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SourceRegistryState":
        cfg_raw = data.get("config") or {}
        queries_raw = data.get("search_queries")
        queries = (
            [str(q).strip() for q in queries_raw if str(q).strip()]
            if queries_raw is not None
            else list(DEFAULT_SEARCH_QUERIES)
        )
        index_urls = [str(u).strip() for u in data.get("index_urls", []) if str(u).strip()]
        cands = [
            DiscoveredSourceCandidate.from_dict(item)
            for item in data.get("candidates", [])
            if isinstance(item, dict) and str(item.get("url", "")).strip()
        ]
        rej_urls = {str(u).strip() for u in data.get("rejected_urls", []) if str(u).strip()}
        rej_doms = {str(d).strip().lower() for d in data.get("rejected_domains", []) if str(d).strip()}
        return cls(
            config=DataSourceConfig.from_dict(cfg_raw),
            wizard_completed=bool(data.get("wizard_completed", True)),
            auto_crawl_enabled=bool(data.get("auto_crawl_enabled", True)),
            auto_approve_discovered=bool(data.get("auto_approve_discovered", True)),
            crawl_interval_sec=max(30.0, float(data.get("crawl_interval_sec", 900.0))),
            search_queries=queries,
            index_urls=index_urls,
            candidates=cands,
            rejected_urls=rej_urls,
            rejected_domains=rej_doms,
        )


def load_source_registry(path: str = DEFAULT_REGISTRY_PATH) -> SourceRegistryState:
    if not path or not os.path.exists(path):
        return SourceRegistryState()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return SourceRegistryState.from_dict(data)
    except Exception:
        pass
    return SourceRegistryState()


def _clear_readonly_if_needed(target_path: str) -> None:
    if not target_path or not os.path.exists(target_path):
        return
    try:
        mode = os.stat(target_path).st_mode
        if not (mode & stat.S_IWUSR):
            os.chmod(target_path, mode | stat.S_IWRITE)
    except OSError:
        pass


def save_source_registry(state: SourceRegistryState, path: str = DEFAULT_REGISTRY_PATH) -> None:
    if not path:
        return
    abs_path = os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    payload = json.dumps(state.to_dict(), indent=2)
    tmp_path = f"{abs_path}.tmp.{os.getpid()}.{threading.get_ident()}"

    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass

    last_err: Optional[OSError] = None
    for attempt in range(6):
        _clear_readonly_if_needed(abs_path)
        try:
            os.replace(tmp_path, abs_path)
            return
        except PermissionError as exc:
            last_err = exc
            time.sleep(0.03 * (attempt + 1))
        except OSError as exc:
            last_err = exc
            break

    try:
        _clear_readonly_if_needed(abs_path)
        with open(abs_path, "w", encoding="utf-8") as dst:
            dst.write(payload)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
    except OSError as fallback_err:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise fallback_err from last_err


def apply_candidate_approval(
    registry: SourceRegistryState,
    candidate: DiscoveredSourceCandidate,
    approved: bool,
) -> None:
    if approved:
        candidate.status = "approved"
        registry.rejected_urls.discard(candidate.url)
        dom = (candidate.domain or _extract_domain(candidate.url)).lower()
        if dom:
            registry.rejected_domains.discard(dom)
    else:
        candidate.status = "rejected"
        if candidate.url:
            registry.rejected_urls.add(candidate.url)


def _parse_feed_items(xml_or_html: str, source_url: str, now: datetime) -> List[Tuple[str, str]]:
    """Returns list of (title, headline_text) from RSS/Atom XML or HTML fallback."""
    out: List[Tuple[str, str]] = []
    if not xml_or_html:
        return out
    domain = _extract_domain(source_url) or "feed"

    try:
        root = ET.fromstring(xml_or_html)
        for item in root.findall(".//item"):
            title_el = item.find("title")
            desc_el = item.find("description")
            title = _strip_html(title_el.text or "") if title_el is not None else ""
            desc = _strip_html(desc_el.text or "") if desc_el is not None else ""
            combined = title if not desc or title.lower() in desc.lower() else f"{title} — {desc}"
            if combined:
                out.append((title or domain, combined[:320]))
        if out:
            return out

        for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
            title_el = entry.find("{http://www.w3.org/2005/Atom}title")
            sum_el = entry.find("{http://www.w3.org/2005/Atom}summary")
            title = _strip_html(title_el.text or "") if title_el is not None else ""
            summ = _strip_html(sum_el.text or "") if sum_el is not None else ""
            combined = title if not summ or title.lower() in summ.lower() else f"{title} — {summ}"
            if combined:
                out.append((title or domain, combined[:320]))
        if out:
            return out
    except ET.ParseError:
        pass

    for match in re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", xml_or_html, flags=re.S | re.I):
        headline = _strip_html(match)
        if len(headline) >= 12:
            out.append((domain, headline[:280]))
    return out[:15]


def scrape_web_for_news_sources(
    search_queries: List[str],
    index_urls: List[str],
    known_urls: Set[str],
    rejected_urls: Set[str],
    rejected_domains: Set[str],
    fetcher: Callable[[str, float], str] = default_http_fetch,
    timeout_sec: float = 6.0,
) -> List[DiscoveredSourceCandidate]:
    discovered: List[DiscoveredSourceCandidate] = []
    seen_here: Set[str] = set()
    active_fetcher = fetcher or default_http_fetch

    candidate_urls: List[Tuple[str, str]] = []
    for u in index_urls:
        clean_u = u.strip()
        if clean_u:
            candidate_urls.append((clean_u, "index"))

    for feed_u in DEFAULT_RSS_FEEDS:
        candidate_urls.append((feed_u, "rss"))

    for q in search_queries:
        clean_q = q.strip()
        if not clean_q:
            continue
        ddg_url = f"https://duckduckgo.com/html/?q={urllib.parse.quote_plus(clean_q)}"
        try:
            html_body = active_fetcher(ddg_url, timeout_sec)
            for href in re.findall(r'href=["\'](https?://[^"\']+)["\']', html_body, flags=re.I):
                unesc = unescape(href)
                if "duckduckgo.com" in unesc:
                    uddg = urllib.parse.parse_qs(urllib.parse.urlparse(unesc).query).get("uddg")
                    if uddg:
                        unesc = urllib.parse.unquote(uddg[0])
                if unesc.startswith(("http://", "https://")):
                    stype = "rss" if any(k in unesc.lower() for k in ("rss", "xml", "atom", "feed")) else "web"
                    candidate_urls.append((unesc, stype))
        except Exception:
            continue

    for url, stype in candidate_urls:
        if url in known_urls or url in rejected_urls or url in seen_here:
            continue
        dom = _extract_domain(url)
        if dom and dom in rejected_domains:
            continue
        seen_here.add(url)

        sample = ""
        title = dom or url
        if stype in ("rss", "index"):
            try:
                body = active_fetcher(url, timeout_sec)
                items = _parse_feed_items(body, url, datetime.now(timezone.utc))
                if items:
                    title = items[0][0] or title
                    sample = items[0][1]
                    stype = "rss"
                elif stype == "index":
                    for link in re.findall(r'href=["\'](https?://[^"\']*(?:rss|xml|atom|feed)[^"\']*)["\']', body, flags=re.I):
                        clean_link = unescape(link)
                        if clean_link not in known_urls and clean_link not in seen_here:
                            seen_here.add(clean_link)
                            discovered.append(
                                DiscoveredSourceCandidate(
                                    url=clean_link,
                                    title=_extract_domain(clean_link) or clean_link,
                                    source_type="rss",
                                    domain=_extract_domain(clean_link),
                                    sample_headline="",
                                    status="pending",
                                )
                            )
                    continue
            except Exception:
                continue

        discovered.append(
            DiscoveredSourceCandidate(
                url=url,
                title=title,
                source_type=stype,
                domain=dom,
                sample_headline=sample,
                status="pending",
            )
        )
        if len(discovered) >= 24:
            break

    return discovered


class SourceConnector:
    def __init__(self, config: DataSourceConfig) -> None:
        self.config = config
        self.last_active_route: str = "standby"
        self._seen_item_hashes: Set[str] = set()

    def set_config(self, config: DataSourceConfig) -> None:
        self.config = config
        self.config.mode = normalize_route_mode(self.config.mode)

    def _dedup_key(self, channel: str, text: str) -> str:
        digest = hashlib.sha256(f"{channel}|{text}".encode("utf-8", errors="ignore")).hexdigest()[:16]
        return digest

    def poll(self, now: Optional[datetime] = None) -> Tuple[List[BroadcastItem], str]:
        ts = now or datetime.now(timezone.utc)
        cfg = self.config
        mode = normalize_route_mode(cfg.mode)
        fetcher = cfg.http_fetcher or default_http_fetch
        items: List[BroadcastItem] = []
        active_routes: List[str] = []

        allow_rf = mode in ("auto", "rf_and_web", "rf_only") and (
            cfg.enable_radio or cfg.local_rf_available or bool(cfg.frequency_endpoints)
        )
        allow_web = mode in ("auto", "rf_and_web", "web_only") and (
            cfg.enable_rss or cfg.allow_web_fallback
        )

        if allow_rf:
            rf_items: List[BroadcastItem] = []
            if cfg.local_rf_reader is not None:
                try:
                    for raw in cfg.local_rf_reader():
                        rf_items.append(raw)
                except Exception:
                    pass
            if cfg.frequency_scanner is not None and cfg.frequency_endpoints:
                try:
                    for raw in cfg.frequency_scanner(list(cfg.frequency_endpoints)):
                        rf_items.append(raw)
                except Exception:
                    pass
            for ep in cfg.frequency_endpoints:
                if ep.startswith(("http://", "https://")):
                    try:
                        body = fetcher(ep, cfg.request_timeout_sec)
                        for _, headline in _parse_feed_items(body, ep, ts)[:5]:
                            rf_items.append(
                                BroadcastItem(
                                    item_id=f"rf_{hashlib.md5(headline.encode()).hexdigest()[:8]}",
                                    channel=f"RF-{_extract_domain(ep) or 'Endpoint'}",
                                    frequency_band="web-sdr",
                                    source_weight=0.78,
                                    timestamp=ts,
                                    text=headline,
                                    transcription_confidence=0.88,
                                )
                            )
                    except Exception:
                        continue
            if rf_items:
                items.extend(rf_items)
                active_routes.append("rf_streams")

        if allow_web:
            web_items: List[BroadcastItem] = []
            feed_urls = list(cfg.rss_feed_urls)
            if cfg.teletext_web_url:
                feed_urls.append(cfg.teletext_web_url)
            if cfg.radio_stream_url and cfg.radio_stream_url.startswith(("http://", "https://")):
                feed_urls.append(cfg.radio_stream_url)

            for url in feed_urls:
                try:
                    body = fetcher(url, cfg.request_timeout_sec)
                    parsed_entries = _parse_feed_items(body, url, ts)
                    dom = _extract_domain(url) or "web"
                    for _, headline in parsed_entries[:6]:
                        web_items.append(
                            BroadcastItem(
                                item_id=f"web_{hashlib.md5(f'{url}:{headline}'.encode()).hexdigest()[:8]}",
                                channel=dom,
                                frequency_band="rss",
                                source_weight=0.75,
                                timestamp=ts,
                                text=headline,
                                transcription_confidence=0.95,
                            )
                        )
                except Exception:
                    continue

            if web_items:
                items.extend(web_items)
                active_routes.append("web_streams")

        fresh_items: List[BroadcastItem] = []
        for it in items:
            key = self._dedup_key(it.channel, it.text)
            if key not in self._seen_item_hashes:
                self._seen_item_hashes.add(key)
                if len(self._seen_item_hashes) > 4000:
                    self._seen_item_hashes = set(list(self._seen_item_hashes)[-2000:])
                fresh_items.append(it)

        if active_routes:
            self.last_active_route = "+".join(active_routes)
        elif allow_web:
            self.last_active_route = "web_standby"
        elif allow_rf:
            self.last_active_route = "rf_standby"
        else:
            self.last_active_route = "standby"

        return fresh_items, self.last_active_route


class EvaluationLogger:
    def __init__(self, db_path: str = "newsticker_shadow.sqlite") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        if db_path:
            parent = os.path.dirname(os.path.abspath(db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._init_schema()

    def _init_schema(self) -> None:
        if self._conn is None:
            return
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT,
                    item_id TEXT,
                    cluster_id TEXT,
                    cluster_label TEXT,
                    channel TEXT,
                    is_echo INTEGER,
                    salience REAL,
                    unexpectedness REAL,
                    accumulation REAL,
                    confidence REAL,
                    power_level REAL,
                    prior_prob REAL,
                    posterior_prob REAL,
                    provenance_tag TEXT,
                    alerted INTEGER,
                    shadow_mode INTEGER,
                    ticker_line TEXT,
                    raw_text TEXT
                )
                """
            )
            cols = {
                row[1]
                for row in self._conn.execute("PRAGMA table_info(evaluations)").fetchall()
                if len(row) > 1
            }
            if "raw_text" not in cols:
                try:
                    self._conn.execute("ALTER TABLE evaluations ADD COLUMN raw_text TEXT")
                except sqlite3.Error:
                    pass
            self._conn.commit()

    def log_evaluation(
        self,
        res: EvaluationResult,
        shadow_mode: bool = False,
        raw_text: Optional[str] = None,
    ) -> None:
        if self._conn is None:
            return
        clean_raw = raw_text or (res.supporting_excerpts[0] if res.supporting_excerpts else "")
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO evaluations (
                        ts_utc, item_id, cluster_id, cluster_label, channel, is_echo,
                        salience, unexpectedness, accumulation, confidence, power_level,
                        prior_prob, posterior_prob, provenance_tag, alerted, shadow_mode, ticker_line, raw_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        res.timestamp.isoformat(),
                        res.item_id,
                        res.cluster_id,
                        res.cluster_label,
                        res.channel,
                        1 if res.is_echo else 0,
                        res.salience,
                        res.unexpectedness,
                        res.accumulation,
                        res.confidence,
                        res.power_level,
                        res.prior_prob,
                        res.posterior_prob,
                        res.provenance_tag,
                        1 if res.alerted else 0,
                        1 if shadow_mode else 0,
                        res.ticker_line,
                        clean_raw,
                    ),
                )
                self._conn.commit()
            except sqlite3.Error:
                pass

    def load_recent_evaluations(self, limit: int = 120) -> List[dict]:
        if self._conn is None or limit <= 0:
            return []
        with self._lock:
            try:
                cur = self._conn.execute(
                    """
                    SELECT ts_utc, item_id, cluster_id, cluster_label, channel, is_echo,
                           posterior_prob, ticker_line, raw_text
                    FROM evaluations
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (int(limit),),
                )
                rows = cur.fetchall()
            except sqlite3.Error:
                return []

        out: List[dict] = []
        for ts_utc, item_id, cluster_id, cluster_label, channel, is_echo, posterior_prob, ticker_line, raw_text in reversed(rows):
            text_val = (raw_text or "").strip()
            if not text_val and ticker_line:
                stripped = re.sub(r"^\[[^\]]+\]\s*", "", str(ticker_line)).strip()
                text_val = stripped or str(ticker_line)
            if not text_val:
                continue
            out.append(
                {
                    "ts_utc": ts_utc,
                    "item_id": item_id,
                    "cluster_id": cluster_id,
                    "cluster_label": cluster_label,
                    "channel": channel or "archive",
                    "is_echo": bool(is_echo),
                    "posterior_prob": posterior_prob,
                    "text": text_val,
                }
            )
        return out

    def close(self) -> None:
        if self._conn is not None:
            with self._lock:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None


class AmbientForecasterTicker:
    def __init__(
        self,
        total_monitored_channels: int = 4,
        power_alert_threshold: float = 0.20,
        surge_salience_threshold: float = 0.45,
        cooldown_sec: float = 60.0,
        step_gain: float = 0.80,
        interest_profile: Optional[InterestProfile] = None,
        source_config: Optional[DataSourceConfig] = None,
        registry_state: Optional[SourceRegistryState] = None,
        registry_path: str = DEFAULT_REGISTRY_PATH,
        shadow_mode: bool = False,
        db_path: str = "newsticker_shadow.sqlite",
        claim_parser: Callable[[str], ParsedClaim] = default_claim_parser,
        headline_synthesizer: Optional[Callable[[TopicCluster, EvaluationResult, ParsedClaim], Optional[str]]] = (
            default_semantic_headline_synthesizer
        ),
    ) -> None:
        self._state_lock = threading.RLock()
        self._warmup_event = threading.Event()
        self.total_monitored_channels = max(1, int(total_monitored_channels))
        self.power_alert_threshold = float(power_alert_threshold)
        self.surge_salience_threshold = float(surge_salience_threshold)
        self.cooldown_sec = float(cooldown_sec)
        self.step_gain = float(step_gain)
        self.interest_profile = interest_profile or InterestProfile()
        self.shadow_mode = bool(shadow_mode)
        self.claim_parser = claim_parser or default_claim_parser
        self.headline_synthesizer = headline_synthesizer

        self.registry_path = registry_path
        if registry_state is not None:
            self.registry_state = registry_state
            if source_config is not None:
                self.registry_state.config = source_config
        elif source_config is not None:
            self.registry_state = SourceRegistryState(config=source_config)
        else:
            self.registry_state = load_source_registry(registry_path)

        self.source_config = self.registry_state.config
        self.source_connector = SourceConnector(self.source_config)
        self.logger = EvaluationLogger(db_path=db_path)

        self.clusters: Dict[str, TopicCluster] = {}
        self.recent_alerts: List[str] = []

    @property
    def is_warmup_complete(self) -> bool:
        return self._warmup_event.is_set()

    def mark_warmup_complete(self) -> None:
        self._warmup_event.set()

    def wait_for_warmup(self, timeout_sec: Optional[float] = None) -> bool:
        return self._warmup_event.wait(timeout=timeout_sec)

    def set_source_config(self, cfg: DataSourceConfig, mark_wizard_done: bool = True) -> None:
        with self._state_lock:
            cfg.mode = normalize_route_mode(cfg.mode)
            self.source_config = cfg
            self.registry_state.config = cfg
            if mark_wizard_done:
                self.registry_state.wizard_completed = True
            self.source_connector.set_config(cfg)
            save_source_registry(self.registry_state, self.registry_path)

    def warmup_from_history(
        self,
        historical_items: Optional[Iterable[BroadcastItem]] = None,
        load_db_history: bool = True,
        db_limit: int = 120,
        reference_now: Optional[datetime] = None,
        mark_complete: bool = True,
    ) -> int:
        now_dt = reference_now or datetime.now(timezone.utc)
        warm_items: List[BroadcastItem] = []

        try:
            if load_db_history:
                archived = self.logger.load_recent_evaluations(limit=db_limit)
                for idx, rec in enumerate(archived):
                    ts_past = _parse_iso_ts(
                        rec.get("ts_utc"),
                        fallback=now_dt - timedelta(hours=max(1, len(archived) - idx)),
                    )
                    if ts_past >= now_dt:
                        ts_past = now_dt - timedelta(minutes=max(5, (len(archived) - idx) * 10))
                    warm_items.append(
                        BroadcastItem(
                            item_id=str(rec.get("item_id") or f"hist_db_{idx}"),
                            channel=str(rec.get("channel") or "archive"),
                            frequency_band="rss",
                            source_weight=0.75,
                            timestamp=ts_past,
                            text=str(rec.get("text") or ""),
                            transcription_confidence=0.95,
                            metadata={"from_db_archive": True},
                        )
                    )

            if historical_items is not None:
                for idx, h_item in enumerate(historical_items):
                    ts_past = h_item.timestamp
                    if ts_past.tzinfo is None:
                        ts_past = ts_past.replace(tzinfo=timezone.utc)
                    if ts_past >= now_dt:
                        ts_past = now_dt - timedelta(hours=max(1, idx + 1))
                    warm_items.append(
                        BroadcastItem(
                            item_id=h_item.item_id or f"hist_{idx}",
                            channel=h_item.channel,
                            frequency_band=h_item.frequency_band,
                            source_weight=h_item.source_weight,
                            timestamp=ts_past,
                            text=h_item.text,
                            transcription_confidence=h_item.transcription_confidence,
                            metadata=dict(h_item.metadata),
                        )
                    )

            warm_items.sort(key=lambda x: x.timestamp)
            count = 0
            for item in warm_items:
                if not item.text.strip():
                    continue
                self.ingest(item, warmup_mode=True)
                count += 1
            return count
        finally:
            if mark_complete:
                self.mark_warmup_complete()

    def poll_configured_sources(self, now: Optional[datetime] = None) -> Tuple[List[EvaluationResult], str]:
        with self._state_lock:
            raw_items, route = self.source_connector.poll(now=now)
        results: List[EvaluationResult] = []
        for item in raw_items:
            results.append(self.ingest(item))
        return results, route

    def background_discover_sources(self) -> List[DiscoveredSourceCandidate]:
        with self._state_lock:
            reg = self.registry_state
            if not reg.auto_crawl_enabled:
                return []
            queries = list(reg.search_queries) or list(DEFAULT_SEARCH_QUERIES)
            idx_urls = list(reg.index_urls)
            known = (
                set(reg.config.rss_feed_urls)
                | set(reg.config.frequency_endpoints)
                | {c.url for c in reg.candidates}
            )
            rej_urls = set(reg.rejected_urls)
            rej_doms = set(reg.rejected_domains)
            fetcher = reg.config.http_fetcher or default_http_fetch
            timeout = reg.config.request_timeout_sec
            auto_approve = reg.auto_approve_discovered

        found = scrape_web_for_news_sources(
            search_queries=queries,
            index_urls=idx_urls,
            known_urls=known,
            rejected_urls=rej_urls,
            rejected_domains=rej_doms,
            fetcher=fetcher,
            timeout_sec=timeout,
        )
        if not found:
            return []

        with self._state_lock:
            if auto_approve:
                for cand in found:
                    apply_candidate_approval(self.registry_state, cand, approved=True)
            self.registry_state.candidates.extend(found)
            save_source_registry(self.registry_state, self.registry_path)
        return found

    def register_cluster_with_forecast(
        self,
        cluster: TopicCluster,
        reason: str = "manual_register",
        reset_probability: bool = False,
    ) -> None:
        with self._state_lock:
            self.clusters[cluster.cluster_id] = cluster
            fc = cluster.forecast
            if fc is not None:
                if reset_probability:
                    prior_p = _clamp(fc.compute_fermi_prior(), 0.01, 0.99)
                    fc.current_probability = prior_p
                    fc.history.append(
                        ForecastSnapshot(
                            timestamp=datetime.now(timezone.utc),
                            prior_prob=prior_p,
                            posterior_prob=prior_p,
                            directional_hint=0.0,
                            evidential_weight=0.0,
                            item_id="prior_init",
                            reason=reason,
                        )
                    )

    def resolve_question(self, cluster_id: str, outcome: bool) -> Optional[float]:
        with self._state_lock:
            cl = self.clusters.get(cluster_id)
            if cl is None or cl.forecast is None:
                return None
            cl.forecast.resolved_outcome = bool(outcome)
            return cl.forecast.brier_score()

    def mean_brier_score(self) -> Optional[float]:
        with self._state_lock:
            scores = [
                cl.forecast.brier_score()
                for cl in self.clusters.values()
                if cl.forecast is not None and cl.forecast.brier_score() is not None
            ]
        valid = [s for s in scores if s is not None]
        if not valid:
            return None
        return sum(valid) / len(valid)

    def _infer_domain_and_regime(self, text: str) -> Tuple[str, str]:
        low = text.lower()
        if any(k in low for k in ("port", "maritime", "shipping", "vessel", "strait", "canal", "navtex")):
            domain = "maritime"
        elif any(k in low for k in ("grid", "power", "pipeline", "energy", "gas", "oil", "telecom", "outage")):
            domain = "infrastructure"
        elif any(k in low for k in ("sanction", "diplomacy", "border", "treaty", "minister", "parliament", "election")):
            domain = "geopolitics"
        else:
            domain = "general"

        if any(k in low for k in ("emergency", "crisis", "escalat", "surge", "breakout", "blockade")):
            regime = "elevated"
        else:
            regime = "baseline"
        return domain, regime

    def _match_or_create_cluster(self, item: BroadcastItem, parsed: ParsedClaim) -> TopicCluster:
        best_cluster: Optional[TopicCluster] = None
        best_score = 0.0

        for cl in self.clusters.values():
            cl_tokens = Counter({k: 1 for k in cl.keywords})
            for ev in cl.evidence[-5:]:
                cl_tokens.update(ev.tokens)
            sim = _cosine_counter(parsed.tokens, cl_tokens)
            if sim > best_score:
                best_score = sim
                best_cluster = cl

        if best_cluster is not None and best_score >= 0.22:
            best_cluster.keywords.update(parsed.topics[:3])
            return best_cluster

        domain_tag, regime_tag = self._infer_domain_and_regime(item.text)
        label_words = parsed.topics[:3] if parsed.topics else [w for w, _ in parsed.tokens.most_common(3)]
        label = " / ".join(label_words).title() if label_words else f"Topic {len(self.clusters) + 1}"
        cid = f"cl_{hashlib.md5(label.lower().encode()).hexdigest()[:8]}"
        if cid in self.clusters:
            cid = f"{cid}_{len(self.clusters) + 1}"

        horizon_hours = float(item.metadata.get("horizon_hours", 168.0))
        resolution_rule = str(
            item.metadata.get(
                "resolution_rule",
                f"Resolves YES if '{label}' materializes or escalates within {int(horizon_hours // 24)}d.",
            )
        )

        cl = TopicCluster(
            cluster_id=cid,
            label=label,
            domain_tag=domain_tag,
            regime_tag=regime_tag,
            keywords=set(parsed.topics),
        )

        if item.metadata.get("create_forecast", True):
            fc = ForecastQuestion(
                question_id=f"q_{cid}",
                topic_label=label,
                question_text=f"Will '{label}' escalate or persist over {max(1, int(horizon_hours // 24))} days?",
                resolution_deadline=item.timestamp + timedelta(hours=horizon_hours),
                impact_score=_clamp(float(item.metadata.get("impact_score", 0.65)), 0.10, 1.00),
                base_rate=item.metadata.get("base_rate", 0.25),
                base_rate_weight=float(item.metadata.get("base_rate_weight", 0.35)),
                reference_class_note=str(item.metadata.get("reference_class_note", "Empirical reference class prior")),
                resolution_rule=resolution_rule,
                domain_tag=domain_tag,
                regime_tag=regime_tag,
                horizon_hours=horizon_hours,
                drivers=[
                    FermiDriver("Primary catalyst", "Key escalation condition holds", 0.50, 0.50),
                    FermiDriver("Countervailing restraint", "Mitigation or mean-reversion holds", 0.45, 0.50),
                ],
                current_probability=0.45,
            )
            cl.forecast = fc
            self.register_cluster_with_forecast(cl, reason="cluster_spawn", reset_probability=True)
        else:
            self.clusters[cid] = cl
        return cl

    def _detect_echo(self, cluster: TopicCluster, item: BroadcastItem, parsed: ParsedClaim) -> Tuple[bool, str]:
        for ev in reversed(cluster.evidence):
            seq = _seq_ratio(item.text, ev.text)
            cos = _cosine_counter(parsed.tokens, ev.tokens)
            if seq >= 0.78 or cos >= 0.84:
                return True, ev.origin_id
        return False, item.item_id

    def _compute_salience(self, cluster: TopicCluster, now: datetime) -> float:
        c_6h = {ev.channel for ev in cluster.evidence if 0.0 <= (now - ev.timestamp).total_seconds() <= 6 * 3600}
        c_1h = {ev.channel for ev in cluster.evidence if 0.0 <= (now - ev.timestamp).total_seconds() <= 3600}
        nc = float(self.total_monitored_channels)
        return _clamp(0.55 * (len(c_6h) / nc) + 0.65 * (len(c_1h) / nc), 0.0, 1.0)

    def _compute_accumulation(self, cluster: TopicCluster, now: datetime, prob: Optional[float]) -> float:
        if prob is None:
            openness = 1.0
        elif 0.03 < prob < 0.97:
            openness = 1.0
        else:
            openness = 0.25

        weighted_sum = 0.0
        for ev in cluster.evidence:
            age_sec = max(0.0, (now - ev.timestamp).total_seconds())
            decay = math.exp(-age_sec / (36.0 * 3600.0))
            weighted_sum += ev.evidential_weight * decay

        raw_acc = openness * (1.0 - math.exp(-weighted_sum / 1.1))
        return _clamp(raw_acc, 0.0, 1.0)

    def _compute_confidence(self, cluster: TopicCluster, salience: float) -> Tuple[float, str]:
        origins = [ev for ev in cluster.evidence if not ev.is_echo and ev.evidential_weight >= 0.15]
        n_orig = len(origins)
        if n_orig > 0:
            mean_q = sum(
                _clamp(ev.source_weight, 0.05, 0.95) * _clamp(ev.transcription_confidence, 0.0, 1.0)
                for ev in origins
            ) / n_orig
        else:
            mean_q = 0.25

        conf = _clamp((1.0 - math.exp(-n_orig / 1.4)) * (0.50 + 0.50 * mean_q), 0.0, 1.0)

        pos_conflict = any(ev.directional_hint > 0.2 for ev in origins)
        neg_conflict = any(ev.directional_hint < -0.2 for ev in origins)
        if pos_conflict and neg_conflict and n_orig >= 2:
            prov = "DISPUTED"
        elif n_orig >= 2 and conf >= 0.45:
            prov = "CORROBORATED"
        elif n_orig == 1 and mean_q >= 0.60:
            prov = "OBSERVED"
        elif salience >= self.surge_salience_threshold:
            prov = "CIRCULATING / SURGING"
        else:
            prov = "UNVERIFIED / CIRCULATING"
        return conf, prov

    def _build_headline(
        self,
        cluster: TopicCluster,
        res: EvaluationResult,
        parsed: ParsedClaim,
        prov_tag: str,
        power_level: float,
        posterior_prob: Optional[float],
    ) -> Tuple[str, str]:
        synthesized: Optional[str] = None
        if self.headline_synthesizer is not None:
            try:
                candidate_hl = self.headline_synthesizer(cluster, res, parsed)
                if candidate_hl and candidate_hl.strip():
                    synthesized = " ".join(candidate_hl.split()).strip()
            except Exception:
                synthesized = None

        body_text = synthesized or parsed.excerpt
        prob_part = f" P={posterior_prob:.0%}" if posterior_prob is not None else ""
        ticker_line = f"[{prov_tag} · Ψ={power_level:.2f}{prob_part}] {body_text}"
        return ticker_line, body_text

    def ingest(self, item: BroadcastItem, warmup_mode: bool = False) -> EvaluationResult:
        parsed = self.claim_parser(item.text)
        with self._state_lock:
            cluster = self._match_or_create_cluster(item, parsed)
            is_echo, origin_id = self._detect_echo(cluster, item, parsed)
            indep_mult = 0.25 if is_echo else 1.00

            src_w = _clamp(item.source_weight, 0.05, 0.95)
            tau = _clamp(item.transcription_confidence, 0.0, 1.0)
            ev_weight = src_w * tau * parsed.certainty_multiplier * indep_mult

            prior_prob: Optional[float] = None
            posterior_prob: Optional[float] = None
            unexpectedness = 0.0

            fc = cluster.forecast
            if fc is not None and fc.resolved_outcome is None:
                prior_prob = _clamp(fc.current_probability, 0.01, 0.99)
                z_post = _logit(prior_prob) + self.step_gain * parsed.directional_hint * ev_weight
                posterior_prob = _clamp(_sigmoid(z_post), 0.01, 0.99)
                fc.current_probability = posterior_prob
                unexpectedness = _clamp(abs(posterior_prob - prior_prob) / 0.15, 0.0, 1.0)
                fc.history.append(
                    ForecastSnapshot(
                        timestamp=item.timestamp,
                        prior_prob=prior_prob,
                        posterior_prob=posterior_prob,
                        directional_hint=parsed.directional_hint,
                        evidential_weight=ev_weight,
                        item_id=item.item_id,
                        reason="warmup_baseline" if warmup_mode else "evidence_update",
                    )
                )
            elif fc is not None:
                prior_prob = fc.current_probability
                posterior_prob = fc.current_probability

            stored = StoredEvidence(
                item_id=item.item_id,
                channel=item.channel,
                band=item.frequency_band,
                source_weight=src_w,
                transcription_confidence=tau,
                timestamp=item.timestamp,
                text=item.text,
                excerpt=parsed.excerpt,
                certainty=parsed.certainty,
                directional_hint=parsed.directional_hint,
                evidential_weight=ev_weight,
                is_echo=is_echo,
                origin_id=origin_id,
                tokens=parsed.tokens,
            )
            cluster.evidence.append(stored)

            salience = self._compute_salience(cluster, item.timestamp)
            accumulation = self._compute_accumulation(cluster, item.timestamp, posterior_prob)
            confidence, prov_tag = self._compute_confidence(cluster, salience)

            interest_score = self.interest_profile.score_text(item.text)
            interest_mult = self.interest_profile.multiplier_for(interest_score)
            base_impact = fc.impact_score if fc is not None else 0.50
            eff_impact = base_impact * interest_mult

            power_level = eff_impact * (
                0.50 * salience
                + 0.55 * unexpectedness * confidence
                + 0.45 * accumulation * confidence
            )

            triggers: List[str] = []
            if salience >= self.surge_salience_threshold:
                triggers.append("rapid_broadcast_surge")
            if unexpectedness * confidence >= 0.14:
                triggers.append("unexpected_development")
            if accumulation * confidence >= 0.20:
                triggers.append("sustained_accumulation")

            in_cooldown = False
            if cluster.last_alert_at is not None:
                elapsed = (item.timestamp - cluster.last_alert_at).total_seconds()
                if elapsed < self.cooldown_sec:
                    in_cooldown = True

            override_cooldown = (
                "rapid_broadcast_surge" in triggers or "unexpected_development" in triggers
            )
            meets_threshold = (
                power_level >= self.power_alert_threshold
                or "rapid_broadcast_surge" in triggers
                or "unexpected_development" in triggers
            )

            excerpts = [ev.excerpt for ev in reversed(cluster.evidence[-5:])]

            res = EvaluationResult(
                timestamp=item.timestamp,
                cluster_id=cluster.cluster_id,
                cluster_label=cluster.label,
                item_id=item.item_id,
                channel=item.channel,
                is_echo=is_echo,
                interest_score=interest_score,
                interest_multiplier=interest_mult,
                salience=salience,
                unexpectedness=unexpectedness,
                accumulation=accumulation,
                confidence=confidence,
                power_level=power_level,
                prior_prob=prior_prob,
                posterior_prob=posterior_prob,
                provenance_tag=prov_tag,
                trigger_reasons=triggers,
                alerted=False,
                ticker_line=None,
                supporting_excerpts=excerpts,
                domain_tag=cluster.domain_tag,
                regime_tag=cluster.regime_tag,
                resolution_rule=fc.resolution_rule if fc is not None else "",
                synthesized_headline=None,
                is_warmup=warmup_mode,
            )

            if (not warmup_mode) and meets_threshold and (not in_cooldown or override_cooldown):
                if not self.shadow_mode:
                    cluster.last_alert_at = item.timestamp
                    ticker_line, synthesized_headline = self._build_headline(
                        cluster=cluster,
                        res=res,
                        parsed=parsed,
                        prov_tag=prov_tag,
                        power_level=power_level,
                        posterior_prob=posterior_prob,
                    )
                    res.alerted = True
                    res.ticker_line = ticker_line
                    res.synthesized_headline = synthesized_headline
                    self.recent_alerts.append(ticker_line)

            cluster.last_power_level = power_level
            cluster.last_unexpectedness = unexpectedness

            if not warmup_mode:
                self.logger.log_evaluation(res, shadow_mode=self.shadow_mode, raw_text=item.text)
            return res