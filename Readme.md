# Ambient News Ticker — Forecast & Salience Monitor

An ambient signal-monitoring and probabilistic forecasting application built with Python and PyQt5. It ingests headlines and claims from RSS/Atom feeds, web/index discovery, and optional RF/SDR/teletext routes, partitions observations into dynamic topic clusters, updates event probabilities over time, and surfaces high-signal alerts on a scrolling marquee ticker.

Rather than ranking observations by engagement or conflating repetition with evidential support, the system maintains a Fermi-decomposed probabilistic forecast per cluster and decouples **cross-channel salience** from **independent corroboration confidence**, emitting excerpt-grounded alerts only when multi-signal **Power Level** or surge criteria are satisfied.

---

## Key Capabilities

- **Multi-Stream Acquisition & Auto-Discovery:** Polls configured RSS/Atom feeds, web/teletext/radio URLs, and RF/SDR callbacks or endpoints across route modes (`auto`, `rf_and_web`, `web_only`, `rf_only`), with automatic network/RF probing, background web/RSS crawling (`scrape_web_for_news_sources`), and domain/URL approval or blocklists persisted in JSON.
- **Duplicate & Echo Suppression:** Detects redundant or echoed reports via lexical sequence similarity (`>= 0.72`) and token cosine similarity (`>= 0.78`), discounting echo evidential weight (`0.15x`) while preserving visibility in channel-spread metrics.
- **Fermi Decomposition & Log-Odds Bayesian Updating:** Synthesizes initial forecast probabilities across weighted conditional Fermi drivers and optional reference-class base rates in log-odds space (`ForecastQuestion.compute_fermi_prior`), applying sequential log-odds updates with `step_gain = 0.55` and archiving probability snapshots.
- **Dual-Signal Gating, Shadow Logging & Calibration:** Separates broadcast **Salience ($S$)** from **Corroboration Confidence ($C$)**, modulates impact via a continuous `InterestProfile` multiplier, logs evaluations to SQLite (`newsticker_shadow.sqlite`) with **Shadow Mode** support, and computes post-resolution **Brier scores**.
- **Interactive PyQt5 Monitor & Dynamic TTL Marquee:** Provides real-time inspection of topic clusters, provenance states, evaluated items, and grounded excerpts alongside a continuous marquee strip (`TickerMarqueeWidget`) with queue-pressure and signal-strength-modulated time-to-live (TTL) expiration.

---

## Repository Layout

```text
├── Newsticker.py            # Core claim/echo engine, Fermi/log-odds updater, source crawler, SQLite logger
├── NewsTickerGui_2.py       # PyQt5 monitor UI, dynamic TTL marquee widget, source & Fermi dialogs
├── newsticker_registry.json # Persisted source config, crawl settings, candidate pool, and approval/block state
└── newsticker_shadow.sqlite # SQLite audit log of evaluations, probabilities, and alert/shadow status

```

---

## Installation & Execution

### Prerequisites

* Python 3.9+
* `PyQt5` (required for `NewsTickerGui_2.py`; `Newsticker.py` uses the Python standard library)

```bash
pip install PyQt5

```

### Launching the Monitor

```bash
python NewsTickerGui_2.py

```

To syntax-check both modules without launching the GUI:

```bash
python -m py_compile Newsticker.py NewsTickerGui_2.py

```

At startup, `NewsTickerGui_2.py` probes network and RF/antenna availability, enables auto-crawl/auto-approve on the source registry, runs an initial web/RSS discovery pass when online and not in `rf_only` mode, and starts background polling (`15s` interval) alongside manual claim ingestion and `poll now` triggers.

---

## 1. Architecture & Modeling Principles

The system transforms an asynchronous stream of textual observations into auditable, excerpt-grounded ticker alerts through five stages:

1. **Provenance-Preserving Ingestion:** Each observation records item ID, channel identity, frequency/transmission band, source reliability weight, transcription confidence, timestamp, payload text, and optional metadata.
2. **Claim Parsing & Echo Attribution:** `default_claim_parser` extracts certainty, directional signal, normalized tokens, and top topics, while `_detect_echo` checks prior cluster evidence to distinguish novel origins from echoes.
3. **Dynamic Topic Clustering:** `_match_or_create_cluster` matches claims against existing clusters via token cosine similarity (`>= 0.24`) or creates a new cluster with inferred domain (`maritime`, `infrastructure`, `geopolitics`, `general`) and regime (`baseline`, `elevated`) tags and an attached `ForecastQuestion`.
4. **Fermi Prior & Log-Odds Sequential Updating:** Active forecast questions combine conditional driver probabilities and optional base rates in log-odds space and update sequentially (`step_gain = 0.55`) as weighted directional evidence arrives.
5. **Dual-Signal Evaluation & Gated Emission:** Salience, unexpectedness, time-decayed accumulation, and corroboration confidence are computed independently, modulated by `InterestProfile` relevance, and gated by cooldown windows, alert thresholds, and Shadow Mode.

---

## 2. Formal Notation & Observation Model

Consider a temporally ordered sequence of observations arriving at timestamps $t_1 \le t_2 \le \dots \le t_n$, where observation $i$ is defined as:

$$x_i = \big(c_i,\, b_i,\, w^{\text{src}}_i,\, t_i,\, \tau_i,\, \gamma_i\big)$$

* $c_i \in \mathcal{C}$: channel or source identifier, with $|\mathcal{C}| = N_c$ total monitored channels (`8` in `build_production_engine()`).
* $b_i$: transmission band or medium (e.g., `shortwave`, `teletext`, `rss`, `vhf`, `navtex`, `web-sdr`).
* $w^{\text{src}}_i \in [0.05, 0.95]$: reliability weight assigned to channel $c_i$.
* $t_i$: observation timestamp.
* $\tau_i \in [0, 1]$: transcription or optical/speech recognition confidence ($\tau_i = 1.0$ for direct manual text).
* $\gamma_i$: raw text string.

From $\gamma_i$, `default_claim_parser` produces:

* Certainty category $\kappa_i \in \{\text{confirmed}, \text{reported}, \text{speculative}\}$ with multiplier $\mu_{\text{cert}}(\kappa_i) \in \{1.00, 0.78, 0.45\}$.
* Directional signal $d_i \in [-1, 1]$, computed from positive escalation/disruption indicators (`+0.45` each) minus negative de-escalation/restoration indicators (`-0.45` each), clamped to $[-1.0, 1.0]$.
* Verbatim/truncated excerpt $e_i$ (up to 180 characters), token `Counter`, and extracted topic keywords.

---

## 3. Echo Detection & Evidential Weighting

### 3.1 Echo Criterion

For an observation $i$ assigned to cluster $k$ with prior evidence set $\mathcal{E}_k$, pairwise lexical sequence similarity `SequenceMatcher` ratio $r_{\text{seq}}(\gamma_i, \gamma_j)$ (on the first 240 lowercase characters) and token cosine similarity $s_{\cos}(\mathbf{v}_i, \mathbf{v}_j)$ are evaluated against $j \in \mathcal{E}_k$. Observation $i$ is classified as an echo ($\mathbb{I}_{\text{echo}}(i) = 1$) if:

$$\max_{j \in \mathcal{E}_k} r_{\text{seq}}(\gamma_i, \gamma_j) \ge 0.72 \quad \lor \quad \max_{j \in \mathcal{E}_k} s_{\cos}(\mathbf{v}_i, \mathbf{v}_j) \ge 0.78$$

When $\mathbb{I}_{\text{echo}}(i) = 1$, item $i$ inherits the origin identifier $o_i = o_{j^*}$ of its closest match; otherwise $o_i = i$. The independence multiplier is:

$$\mu_{\text{indep}}(i) = \begin{cases} 0.15, & \text{if } \mathbb{I}_{\text{echo}}(i) = 1 \\ 1.00, & \text{otherwise.} \end{cases}$$

### 3.2 Evidential Weight

The evidential weight $W_i \ge 0$ of observation $i$ is:

$$W_i = w^{\text{src}}_i \cdot \tau_i \cdot \mu_{\text{cert}}(\kappa_i) \cdot \mu_{\text{indep}}(i)$$

Discounting echoed text in $W_i$ prevents near-duplicate rebroadcasts from inflating independent corroboration while preserving their contribution to cross-channel salience.

---

## 4. Fermi Decomposition & Log-Odds Forecast Updating

### 4.1 Fermi Driver Aggregation & Base-Rate Blending

For a cluster $k$ with an attached `ForecastQuestion` (impact $I_k \in [0.10, 1.00]$) and $M$ conditional Fermi drivers ($m = 1, \dots, M$), each with probability $p_m \in [0.01, 0.99]$ and weight $\omega_m \ge 0$:

$$z^{\text{drv}}_k = \sum_{m=1}^{M} \tilde{\omega}_m \operatorname{logit}(p_m), \qquad \tilde{\omega}_m = \frac{\omega_m}{\sum_{r=1}^{M} \omega_r}$$

(If total driver weight is zero, `compute_fermi_prior()` averages driver probabilities directly; if there are no drivers, it defaults to `0.45`.)

When a reference-class base rate $p^{\text{base}}_k \in [0.01, 0.99]$ is set with weight $w_{\text{base}} \in [0, 0.90]$, the Fermi prior blends base rate and driver log-odds:

$$p^{(0)}_k = \operatorname{clip}\!\Big(\sigma\!\big(w_{\text{base}} \operatorname{logit}(p^{\text{base}}_k) + (1 - w_{\text{base}}) z^{\text{drv}}_k\big),\, 0.01,\, 0.99\Big)$$

In `NewsTickerGui_2.py`, saving changes in `FermiForecastDialog` resets probability to the new Fermi prior only when the cluster has no stored evidence yet (`reset_probability=not has_evidence`), preserving prior evidence updates on active clusters.

### 4.2 Sequential Log-Odds Update

When an unresolved forecast is attached at timestamp $t_i$, the pre-update probability $p^{\text{prior}}_k \in [0.01, 0.99]$ is recorded and updated with `step_gain` $\alpha = 0.55$:

$$z^{\text{post}}_k = \operatorname{logit}(p^{\text{prior}}_k) + \alpha \, d_i \, W_i$$

$$p^{\text{post}}_k = \operatorname{clip}\!\big(\sigma(z^{\text{post}}_k),\, 0.01,\, 0.99\big)$$

Each update appends a `ForecastSnapshot` to `fc.history`.

---

## 5. Diagnostic Signals & Composite Power

All quantities below are computed at observation timestamp $t = t_i$.

### 5.1 Salience ($S$)

Let $\mathcal{C}_{6\text{h}}(k, t)$ and $\mathcal{C}_{1\text{h}}(k, t)$ denote the sets of unique channels with evidence in cluster $k$ during $[t - 6\text{h}, t]$ and $[t - 1\text{h}, t]$:

$$S(k, t) = \operatorname{clip}\!\left(0.55 \frac{|\mathcal{C}_{6\text{h}}(k, t)|}{N_c} + 0.65 \frac{|\mathcal{C}_{1\text{h}}(k, t)|}{N_c},\; 0.0,\; 1.0\right)$$

### 5.2 Unexpectedness ($U$)

Unexpectedness scales the single-step probability shift relative to `0.20`:

$$U_i = \begin{cases} \operatorname{clip}\!\left(\dfrac{|p^{\text{post}}_k - p^{\text{prior}}_k|}{0.20},\; 0.0,\; 1.0\right), & \text{if } p^{\text{prior}}_k \text{ and } p^{\text{post}}_k \text{ exist} \\ 0.0, & \text{otherwise.} \end{cases}$$

### 5.3 Time-Decayed Accumulation ($A$)

Define outcome openness $\mathcal{O}_k(p) = 1.0$ if $p$ is `None` or $0.03 < p < 0.97$, and $0.25$ otherwise. With a $36\text{-hour}$ exponential decay constant and saturation denominator `1.4`:

$$A(k, t) = \operatorname{clip}\!\left(\mathcal{O}_k(p) \left[1 - \exp\!\left(-\frac{1}{1.4} \sum_{j \in \mathcal{E}_k,\, t_j \le t} W_j \exp\!\left(-\frac{\max(0, t - t_j)}{36\text{h}}\right)\right)\right],\; 0.0,\; 1.0\right)$$

### 5.4 Corroboration Confidence ($C$) & Provenance Classification

Let $\mathcal{I}_k \subseteq \mathcal{E}_k$ denote non-echo origin items (`not ev.is_echo`) with $W_j \ge 0.20$, and let $\bar{q}_k$ be the mean of $\operatorname{clamp}(w^{\text{src}}_j, 0.05, 0.95) \cdot \operatorname{clamp}(\tau_j, 0.0, 1.0)$ over $j \in \mathcal{I}_k$ (or `0.10` if $\mathcal{I}_k$ is empty). Confidence is:

$$C(k, t) = \operatorname{clip}\!\left(\big(1 - e^{-|\mathcal{I}_k|/2}\big)\big(0.40 + 0.60\,\bar{q}_k\big),\; 0.0,\; 1.0\right)$$

Provenance classification (`_compute_confidence`) assigns one of five labels based on origin count, quality, directional disagreement ($d_j > 0.2$ and $d_m < -0.2$ among origins with $|\mathcal{I}_k| \ge 2$), confidence, and surge threshold $\theta_S$:

* **DISPUTED:** Opposing directional signals across $|\mathcal{I}_k| \ge 2$ origin items.
* **CORROBORATED:** $|\mathcal{I}_k| \ge 2$ origin items with $C \ge 0.45$ and no dispute.
* **OBSERVED:** $|\mathcal{I}_k| = 1$ origin item with $\bar{q}_k \ge 0.60$.
* **CIRCULATING / SURGING:** Salience satisfies $S \ge \theta_S$ without corroborated or observed status.
* **UNVERIFIED / CIRCULATING:** Claims below surge and corroboration criteria.

### 5.5 Interest Modulation & Power Level ($\Psi$)

For interest score $\eta_i \in [0, 1]$ from `InterestProfile.score_text`:

$$\mu_{\text{int}}(\eta_i) = \begin{cases} 1.0 - (1.0 - m_{\text{out}})\,\operatorname{clip}\!\big(2(0.50 - \eta_i),\, 0.0,\, 1.0\big), & \text{if } \eta_i < 0.50 \\ 1.0 + (m_{\text{in}} - 1.0)\,\operatorname{clip}\!\big(2(\eta_i - 0.50),\, 0.0,\, 1.0\big), & \text{if } \eta_i \ge 0.50. \end{cases}$$

With effective impact $I^{\text{eff}}_{k,i} = I_k \cdot \mu_{\text{int}}(\eta_i)$ (default impact `0.45` if no forecast is attached), composite **Power Level** is:

$$\Psi_i = I^{\text{eff}}_{k,i} \Big(0.45\,S(k, t_i) + 0.55\,U_i\,C(k, t_i) + 0.45\,A(k, t_i)\,C(k, t_i)\Big)$$

---

## 6. Alert Gating, Dynamic TTL, Shadow Logging & Calibration

### 6.1 Trigger Set, Cooldown & Emission Rule

Three trigger conditions are checked at each ingestion:

* `rapid_broadcast_surge`: $S \ge \theta_S$ (`0.62` default)
* `unexpected_development`: $U_i\,C(k, t_i) \ge 0.18$
* `sustained_accumulation`: $A(k, t_i)\,C(k, t_i) \ge 0.28$

A cooldown window of `cooldown_sec = 120.0s` since `cluster.last_alert_at` suppresses repeated alerts unless `rapid_broadcast_surge` or `unexpected_development` is triggered. Outside cooldown (or when overridden), an alert is emitted when $\Psi_i \ge \theta_{\Psi}$ (`0.36` default), `rapid_broadcast_surge` is triggered, or `unexpected_development` is triggered—provided `shadow_mode` is `False`. Emitted alerts format as:

`[{provenance_tag} · Ψ={power_level:.2f} P={posterior_prob:.0%}] {excerpt}`

### 6.2 Dynamic Marquee Time-to-Live (TTL)

In `TickerMarqueeWidget`, queued alerts expire dynamically according to queue length $Q$, alert power $\Psi_i$, and surge flag $\mathbb{I}_{\text{surge}}$, clamped to $[\text{TTL}_{\min}, \text{TTL}_{\max}] = [180\text{s}, 10800\text{s}]$ around $\text{TTL}_{\text{base}} = 1800\text{s}$:

$$\text{TTL}_i = \operatorname{clip}\!\left(\text{TTL}_{\text{base}} \cdot \frac{1}{1 + 0.15\max(0, Q - 1)} \cdot \Big(0.75 + \min(0.65,\, 0.70\,\Psi_i) + 0.20\,\mathbb{I}_{\text{surge}}\Big),\; \text{TTL}_{\min},\; \text{TTL}_{\max}\right)$$

Users can click the marquee or use `pause scroll`, `prev`, and `next` to inspect items interactively.

### 6.3 Shadow Mode & Brier Score Calibration

When `shadow_mode` is enabled, evaluations are written to the `evaluations` table in `newsticker_shadow.sqlite` without emitting live ticker alerts. Resolving a cluster's forecast question (`YES` / `NO` in `FermiForecastDialog` or `engine.resolve_question`) computes its **Brier score** and updates the window's `mean brier` readout:

$$\text{BS} = (p - y)^2 \in [0, 1], \qquad y \in \{0, 1\}$$

---

## 7. Extensions & Methodological Refinements

* **Structured Claim Extraction:** Replace keyword/regex heuristics in `default_claim_parser` with typed schema extraction (e.g., BAML or structured LLM outputs) or specialized domain lexicons.
* **Dense Semantic Representation:** Augment token cosine similarity and `SequenceMatcher` ratios in `_match_or_create_cluster` and `_detect_echo` with dense embeddings for stronger paraphrase and cross-lingual matching.
* **Empirical Calibration:** Tune source weights, Fermi priors/base rates, decay constants, and alert thresholds $(\theta_{\Psi}, \theta_S)$ using logged SQLite evaluations and resolved Brier scores.