from __future__ import annotations

import os
import socket
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Deque, List, Optional

from PyQt5.QtCore import QRectF, QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QCloseEvent, QColor, QFont, QFontMetrics, QLinearGradient, QMouseEvent, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from Newsticker import (
    AmbientForecasterTicker,
    BroadcastItem,
    DEFAULT_REGISTRY_PATH,
    DEFAULT_SEARCH_QUERIES,
    DataSourceConfig,
    EvaluationResult,
    FermiDriver,
    ForecastQuestion,
    InterestProfile,
    TopicCluster,
    apply_candidate_approval,
    default_http_fetch,
    load_source_registry,
    normalize_route_mode,
    save_source_registry,
    scrape_web_for_news_sources,
)


ROUTE_MODES = ["auto", "rf_and_web", "web_only", "rf_only"]


PRO_DARK_STYLESHEET = """
QWidget {
    background-color: #0f0f0f;
    color: #e8e8e8;
    font-family: 'JetBrains Mono', 'Fira Code', 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
    font-size: 10pt;
    selection-background-color: #c9a227;
    selection-color: #0f0f0f;
}

QMainWindow, QDialog {
    background-color: #0f0f0f;
}

QGroupBox {
    background-color: #161616;
    border: 1px solid #2a2a2a;
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px 8px 8px 8px;
    font-weight: 600;
    color: #c9a227;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: #c9a227;
    background-color: #161616;
}

QLabel {
    background-color: transparent;
    color: #a0a0a0;
}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {
    background-color: #1c1c1c;
    color: #e8e8e8;
    border: 1px solid #2a2a2a;
    border-radius: 6px;
    padding: 5px 8px;
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus {
    border: 1px solid #c9a227;
    background-color: #242424;
    color: #e8e8e8;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox QAbstractItemView {
    background-color: #161616;
    color: #e8e8e8;
    border: 1px solid #2a2a2a;
    selection-background-color: #c9a227;
    selection-color: #0f0f0f;
}

QPushButton {
    background-color: #1c1c1c;
    color: #e8e8e8;
    border: 1px solid #2a2a2a;
    border-radius: 6px;
    padding: 6px 12px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #242424;
    border: 1px solid #c9a227;
    color: #ddb52f;
}

QPushButton:pressed {
    background-color: #c9a227;
    border: 1px solid #ddb52f;
    color: #0f0f0f;
}

QPushButton:disabled {
    background-color: #161616;
    color: #6a6a6a;
    border: 1px solid #1f1f1f;
}

QCheckBox {
    spacing: 6px;
    color: #e8e8e8;
    background-color: transparent;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #2a2a2a;
    background-color: #1c1c1c;
}

QCheckBox::indicator:checked {
    background-color: #c9a227;
    border: 1px solid #ddb52f;
}

QTableWidget {
    background-color: #161616;
    alternate-background-color: #1c1c1c;
    color: #e8e8e8;
    gridline-color: #1f1f1f;
    border: 1px solid #2a2a2a;
    border-radius: 6px;
}

QTableWidget::item {
    padding: 4px 6px;
}

QTableWidget::item:selected {
    background-color: #242424;
    color: #ddb52f;
}

QHeaderView::section {
    background-color: #1c1c1c;
    color: #c9a227;
    font-weight: 600;
    padding: 6px;
    border: none;
    border-right: 1px solid #2a2a2a;
    border-bottom: 1px solid #2a2a2a;
}

QSplitter::handle {
    background-color: #1f1f1f;
    height: 4px;
    width: 4px;
    border-radius: 2px;
}

QSplitter::handle:hover {
    background-color: #c9a227;
}

QTabWidget::pane {
    border: 1px solid #2a2a2a;
    border-radius: 8px;
    background-color: #161616;
}

QTabBar::tab {
    background-color: #1c1c1c;
    color: #a0a0a0;
    border: 1px solid #2a2a2a;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 7px 14px;
    margin-right: 3px;
}

QTabBar::tab:selected {
    background-color: #242424;
    color: #c9a227;
    font-weight: 600;
    border-color: #c9a227;
}
"""


def _make_read_only(cell: QTableWidgetItem) -> None:
    mask = int(cell.flags()) & ~int(Qt.ItemFlag.ItemIsEditable)
    cell.setFlags(Qt.ItemFlag(mask))


def _sync_approved_sources_to_config(engine: AmbientForecasterTicker, candidates: list) -> None:
    with engine._state_lock:
        reg = engine.registry_state
        cfg = reg.config
        existing_rss = list(cfg.rss_feed_urls)
        existing_rf = list(cfg.frequency_endpoints)
        seen_rss = set(existing_rss)
        seen_rf = set(existing_rf)

        for cand in candidates:
            if getattr(cand, "status", None) != "approved":
                continue
            url = (getattr(cand, "url", "") or "").strip()
            if not url:
                continue
            stype = (getattr(cand, "source_type", "") or "").lower()
            if any(k in stype for k in ("rf", "sdr", "radio", "freq")):
                if url not in seen_rf:
                    existing_rf.append(url)
                    seen_rf.add(url)
            else:
                if url not in seen_rss:
                    existing_rss.append(url)
                    seen_rss.add(url)

        cfg.rss_feed_urls = existing_rss
        cfg.frequency_endpoints = existing_rf
        cfg.mode = normalize_route_mode(cfg.mode)
        engine.set_source_config(cfg, mark_wizard_done=True)


def check_internet_available(timeout_sec: float = 2.0, fetcher=default_http_fetch) -> bool:
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 443), ("1.1.1.1", 53)):
        try:
            with socket.create_connection((host, port), timeout=min(1.2, timeout_sec)):
                return True
        except OSError:
            continue
    if fetcher is not None:
        for probe_url in ("https://feeds.bbci.co.uk/news/world/rss.xml", "https://www.reuters.com/"):
            try:
                body = fetcher(probe_url, timeout_sec)
                if body:
                    return True
            except Exception:
                continue
    return False


def detect_radio_antenna_or_sdr(engine: AmbientForecasterTicker) -> tuple[bool, str]:
    with engine._state_lock:
        cfg = engine.source_config
        has_cb = cfg.local_rf_reader is not None or cfg.frequency_scanner is not None
        endpoints = list(cfg.frequency_endpoints)

    if has_cb:
        return True, "RF/SDR callback registered"

    device_hints = [
        "/dev/rtl_sdr",
        "/dev/rtlsdr0",
        "/dev/sdr",
        "/dev/hackrf0",
        "/dev/airspy0",
        "/dev/ttyUSB0",
        "/dev/ttyACM0",
    ]
    for path in device_hints:
        if os.path.exists(path):
            return True, f"antenna/device {path}"

    by_id = "/dev/serial/by-id"
    if os.path.isdir(by_id):
        try:
            for name in os.listdir(by_id):
                low = name.lower()
                if any(k in low for k in ("sdr", "rtl", "radio", "rf", "ant", "hackrf", "airspy")):
                    return True, f"antenna/SDR {name}"
        except OSError:
            pass

    if endpoints:
        return True, f"RF endpoints ({len(endpoints)})"

    return False, "no antenna detected"


def build_cluster_short_summary(engine: AmbientForecasterTicker, cluster: TopicCluster) -> str:
    now = datetime.now(timezone.utc)
    with engine._state_lock:
        fc = cluster.forecast
        prob_str = f"{fc.current_probability:.0%}" if fc is not None else "n/a"
        sal = engine._compute_salience(cluster, now)
        acc = engine._compute_accumulation(
            cluster, now, fc.current_probability if fc is not None else None
        )
        conf, prov = engine._compute_confidence(cluster, sal)
        n_ev = len(cluster.evidence)
        channels = sorted({ev.channel for ev in cluster.evidence[-8:] if ev.channel})
        ch_preview = ", ".join(channels[:4]) if channels else "none"
        latest_excerpt = cluster.evidence[-1].excerpt if cluster.evidence else "No evidence ingested yet."

    return (
        f"[{prov}] {cluster.label} ({cluster.domain_tag}/{cluster.regime_tag}) — "
        f"P={prob_str}, S={sal:.2f}, A={acc:.2f}, C={conf:.2f}, Ψ={cluster.last_power_level:.2f} "
        f"across {n_ev} item(s) [{ch_preview}]. Latest: {latest_excerpt}"
    )


@dataclass
class _QueuedAlert:
    text: str
    created_at: datetime
    power_level: float = 0.4
    is_surge: bool = False


class TickerMarqueeWidget(QWidget):
    def __init__(
        self,
        parent: Optional[QWidget] = None,
        base_ttl_sec: float = 1800.0,
        min_ttl_sec: float = 180.0,
        max_ttl_sec: float = 10800.0,
    ) -> None:
        super().__init__(parent)
        self._queue: Deque[_QueuedAlert] = deque(maxlen=40)
        self._base_ttl_sec = max(10.0, base_ttl_sec)
        self._min_ttl_sec = max(5.0, min(min_ttl_sec, self._base_ttl_sec))
        self._max_ttl_sec = max(self._base_ttl_sec, max_ttl_sec)

        self._active_index: int = 0
        self._scroll_offset: float = 0.0
        self._paused: bool = False
        self._speed_px_per_tick: float = 1.6
        self._gap_px: int = 160
        self._separator: str = "          /          "
        self._idle_message = "initializing live routes — collecting headlines from active web/RSS and RF sources..."

        self._font = QFont("JetBrains Mono", 11, QFont.Weight.Bold)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(self._font)
        self.setMinimumHeight(48)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(25)

    @property
    def is_paused(self) -> bool:
        return self._paused

    def append_alert(
        self,
        ticker_line: str,
        power_level: float = 0.4,
        is_surge: bool = False,
        created_at: Optional[datetime] = None,
    ) -> None:
        clean = " ".join(ticker_line.split())
        if not clean:
            return
        ts = created_at or datetime.now(timezone.utc)
        if not self._queue or self._queue[-1].text != clean:
            self._queue.append(
                _QueuedAlert(
                    text=clean,
                    created_at=ts,
                    power_level=max(0.0, power_level),
                    is_surge=is_surge,
                )
            )
        self.update()

    def _dynamic_ttl_for(self, entry: _QueuedAlert, queue_len: int) -> float:
        pressure_factor = 1.0 / (1.0 + 0.15 * max(0, queue_len - 1))
        strength_factor = 0.75 + min(0.65, entry.power_level * 0.7) + (0.20 if entry.is_surge else 0.0)
        raw_ttl = self._base_ttl_sec * pressure_factor * strength_factor
        return max(self._min_ttl_sec, min(self._max_ttl_sec, raw_ttl))

    def _prune_expired(self, now: Optional[datetime] = None) -> None:
        if not self._queue:
            self._active_index = 0
            self._scroll_offset = 0.0
            return

        current_time = now or datetime.now(timezone.utc)
        q_len = len(self._queue)
        kept: Deque[_QueuedAlert] = deque(maxlen=self._queue.maxlen)
        removed_before_active = 0

        for idx, entry in enumerate(self._queue):
            age_sec = max(0.0, (current_time - entry.created_at).total_seconds())
            ttl_sec = self._dynamic_ttl_for(entry, q_len)
            if age_sec < ttl_sec:
                kept.append(entry)
            elif idx < self._active_index:
                removed_before_active += 1

        if len(kept) != q_len:
            self._queue = kept
            if not self._queue:
                self._active_index = 0
                self._scroll_offset = 0.0
            else:
                self._active_index = max(0, (self._active_index - removed_before_active) % len(self._queue))

    def step_alert(self, delta: int) -> None:
        self._prune_expired()
        if not self._queue:
            return
        self._active_index = (self._active_index + delta) % len(self._queue)
        self._scroll_offset = 0.0
        self.update()

    def toggle_pause(self) -> None:
        self._paused = not self._paused
        self.update()

    def mousePressEvent(self, a0: Optional[QMouseEvent]) -> None:
        if a0 is not None and a0.button() == Qt.MouseButton.LeftButton:
            self.toggle_pause()
            a0.accept()
            return
        super().mousePressEvent(a0)

    def _build_playlist_string(self, fm: QFontMetrics) -> tuple[str, int]:
        if not self._queue:
            return self._idle_message, fm.horizontalAdvance(self._idle_message)

        items = [e.text for e in self._queue]
        ordered = items[self._active_index :] + items[: self._active_index]
        full_text = self._separator.join(ordered) + self._separator
        text_width = fm.horizontalAdvance(full_text)
        return full_text, text_width

    def _tick(self) -> None:
        self._prune_expired()
        if self._paused or not self._queue:
            self.update()
            return

        fm = QFontMetrics(self._font)
        _, text_width = self._build_playlist_string(fm)
        cycle_width = max(text_width + self._gap_px, 1)

        self._scroll_offset += self._speed_px_per_tick
        if self._scroll_offset >= cycle_width:
            self._scroll_offset -= cycle_width
            self._active_index = (self._active_index + 1) % len(self._queue)

        self.update()

    def paintEvent(self, a0) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        rect = QRectF(self.rect())
        has_surge = any(e.is_surge for e in self._queue)

        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, QColor("#161616"))
        grad.setColorAt(0.5, QColor("#1c1c1c"))
        grad.setColorAt(1.0, QColor("#161616"))

        if self._paused:
            border_color = QColor("#a0a0a0")
        elif self._queue and has_surge:
            border_color = QColor("#ddb52f")
        else:
            border_color = QColor("#c9a227")

        painter.setPen(QPen(border_color, 1.2))
        painter.setBrush(grad)
        painter.drawRoundedRect(rect.adjusted(0.6, 0.6, -0.6, -0.6), 6.0, 6.0)

        painter.setFont(self._font)
        fm = QFontMetrics(self._font)
        text_y = int((rect.height() + fm.ascent() - fm.descent()) / 2)

        badge_text = (
            f" paused ({len(self._queue)}) "
            if self._paused
            else (
                f" surge · {min(self._active_index + 1, len(self._queue))}/{len(self._queue)} "
                if (self._queue and has_surge)
                else f" active · {min(self._active_index + 1, len(self._queue))}/{len(self._queue)} "
            )
        )
        badge_w = fm.horizontalAdvance(badge_text) + 14
        badge_rect = QRectF(rect.width() - badge_w - 8, 7.0, badge_w, rect.height() - 14.0)

        stream_clip = QRectF(10.0, 2.0, max(10.0, badge_rect.left() - 18.0), rect.height() - 4.0)
        painter.save()
        painter.setClipRect(stream_clip)

        full_text, text_width = self._build_playlist_string(fm)
        cycle_width = max(text_width + self._gap_px, 1)
        start_x = stream_clip.left() - self._scroll_offset

        if not self._queue:
            text_color = QColor("#6a6a6a")
        elif has_surge:
            text_color = QColor("#ddb52f")
        else:
            text_color = QColor("#e6b450")

        painter.setPen(text_color)

        x = start_x
        while x < stream_clip.right():
            painter.drawText(int(x), text_y, full_text)
            x += cycle_width

        painter.restore()

        if self._paused:
            painter.setBrush(QColor("#242424"))
            painter.setPen(QPen(QColor("#a0a0a0"), 1.0))
        elif self._queue and has_surge:
            painter.setBrush(QColor("#242424"))
            painter.setPen(QPen(QColor("#ddb52f"), 1.0))
        else:
            painter.setBrush(QColor("#1c1c1c"))
            painter.setPen(QPen(QColor("#c9a227"), 1.0))

        painter.drawRoundedRect(badge_rect, 4.0, 4.0)
        painter.drawText(
            badge_rect,
            int(Qt.AlignmentFlag.AlignCenter),
            badge_text.strip(),
        )
        painter.end()

    def stop(self) -> None:
        self._timer.stop()


class ScrapeWorker(QThread):
    finished_scrape = pyqtSignal(list)

    def __init__(
        self,
        queries: List[str],
        index_urls: List[str],
        known_urls: set,
        rejected_urls: set,
        rejected_domains: set,
        fetcher,
        timeout_sec: float,
    ) -> None:
        super().__init__()
        self.queries = queries
        self.index_urls = index_urls
        self.known_urls = known_urls
        self.rejected_urls = rejected_urls
        self.rejected_domains = rejected_domains
        self.fetcher = fetcher
        self.timeout_sec = timeout_sec

    def run(self) -> None:
        found = scrape_web_for_news_sources(
            search_queries=self.queries,
            index_urls=self.index_urls,
            known_urls=self.known_urls,
            rejected_urls=self.rejected_urls,
            rejected_domains=self.rejected_domains,
            fetcher=self.fetcher,
            timeout_sec=self.timeout_sec,
        )
        self.finished_scrape.emit(found)


class PollOnceWorker(QThread):
    finished_poll = pyqtSignal(list, str)

    def __init__(self, engine: AmbientForecasterTicker) -> None:
        super().__init__()
        self.engine = engine

    def run(self) -> None:
        self.engine.wait_for_warmup(timeout_sec=10.0)
        results, route = self.engine.poll_configured_sources()
        self.finished_poll.emit(results, route)


class WarmupWorker(QThread):
    finished_warmup = pyqtSignal(int)

    def __init__(self, engine: AmbientForecasterTicker, db_limit: int = 120) -> None:
        super().__init__()
        self.engine = engine
        self.db_limit = db_limit

    def run(self) -> None:
        seeded_count = self.engine.warmup_from_history(
            historical_items=None,
            load_db_history=True,
            db_limit=self.db_limit,
            reference_now=datetime.now(timezone.utc),
            mark_complete=True,
        )
        self.finished_warmup.emit(seeded_count)


class EngineSettingsDialog(QDialog):
    def __init__(self, engine: AmbientForecasterTicker, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("engine settings & sensitivity")
        self.resize(560, 440)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        with self.engine._state_lock:
            pwr_val = self.engine.power_alert_threshold
            surge_val = self.engine.surge_salience_threshold
            ch_val = self.engine.total_monitored_channels
            step_val = self.engine.step_gain
            cd_val = self.engine.cooldown_sec
            shadow_val = self.engine.shadow_mode
            prof = self.engine.interest_profile
            prompt_val = prof.prompt
            focus_val = ", ".join(prof.focus_terms)
            in_mult_val = prof.in_interest_multiplier
            out_mult_val = prof.out_of_interest_multiplier

        self.power_spin = QDoubleSpinBox()
        self.power_spin.setRange(0.05, 1.00)
        self.power_spin.setSingleStep(0.02)
        self.power_spin.setValue(pwr_val)

        self.surge_spin = QDoubleSpinBox()
        self.surge_spin.setRange(0.10, 1.00)
        self.surge_spin.setSingleStep(0.05)
        self.surge_spin.setValue(surge_val)

        self.channels_spin = QSpinBox()
        self.channels_spin.setRange(1, 64)
        self.channels_spin.setValue(ch_val)

        self.step_gain_spin = QDoubleSpinBox()
        self.step_gain_spin.setRange(0.10, 2.50)
        self.step_gain_spin.setSingleStep(0.05)
        self.step_gain_spin.setValue(step_val)

        self.cooldown_spin = QDoubleSpinBox()
        self.cooldown_spin.setRange(0.0, 3600.0)
        self.cooldown_spin.setSingleStep(10.0)
        self.cooldown_spin.setValue(cd_val)

        self.shadow_chk = QCheckBox("Shadow mode (log evaluations without emitting live alerts)")
        self.shadow_chk.setChecked(shadow_val)

        self.prompt_edit = QLineEdit(prompt_val)
        self.focus_terms_edit = QLineEdit(focus_val)
        self.focus_terms_edit.setPlaceholderText("Optional comma-separated keywords...")

        self.in_mult_spin = QDoubleSpinBox()
        self.in_mult_spin.setRange(1.00, 3.00)
        self.in_mult_spin.setSingleStep(0.05)
        self.in_mult_spin.setValue(in_mult_val)

        self.out_mult_spin = QDoubleSpinBox()
        self.out_mult_spin.setRange(0.10, 1.00)
        self.out_mult_spin.setSingleStep(0.05)
        self.out_mult_spin.setValue(out_mult_val)

        form.addRow("power threshold (ψ):", self.power_spin)
        form.addRow("surge salience (s):", self.surge_spin)
        form.addRow("monitored channels:", self.channels_spin)
        form.addRow("bayesian step gain:", self.step_gain_spin)
        form.addRow("alert cooldown (sec):", self.cooldown_spin)
        form.addRow("shadow logging:", self.shadow_chk)
        form.addRow("interest prompt:", self.prompt_edit)
        form.addRow("focus keywords:", self.focus_terms_edit)
        form.addRow("in-interest mult:", self.in_mult_spin)
        form.addRow("out-interest mult:", self.out_mult_spin)

        layout.addLayout(form)

        reset_btn = QPushButton("reset to balanced defaults")
        reset_btn.clicked.connect(self._reset_defaults)
        layout.addWidget(reset_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_settings)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _reset_defaults(self) -> None:
        self.power_spin.setValue(0.20)
        self.surge_spin.setValue(0.45)
        self.channels_spin.setValue(4)
        self.step_gain_spin.setValue(0.80)
        self.cooldown_spin.setValue(60.0)
        self.shadow_chk.setChecked(False)
        self.in_mult_spin.setValue(1.30)
        self.out_mult_spin.setValue(0.75)

    def _save_settings(self) -> None:
        focus_terms = [t.strip() for t in self.focus_terms_edit.text().split(",") if t.strip()]
        prompt_txt = self.prompt_edit.text().strip() or (
            "Consequential world, infrastructure, maritime, and geopolitical developments"
        )
        with self.engine._state_lock:
            self.engine.power_alert_threshold = float(self.power_spin.value())
            self.engine.surge_salience_threshold = float(self.surge_spin.value())
            self.engine.total_monitored_channels = int(self.channels_spin.value())
            self.engine.step_gain = float(self.step_gain_spin.value())
            self.engine.cooldown_sec = float(self.cooldown_spin.value())
            self.engine.shadow_mode = bool(self.shadow_chk.isChecked())
            self.engine.interest_profile = InterestProfile(
                prompt=prompt_txt,
                focus_terms=focus_terms,
                in_interest_multiplier=float(self.in_mult_spin.value()),
                out_of_interest_multiplier=float(self.out_mult_spin.value()),
            )
        self.accept()


class DataSourceDialog(QDialog):
    def __init__(
        self,
        engine: AmbientForecasterTicker,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.reg = engine.registry_state
        with engine._state_lock:
            cfg = self.reg.config
        self._scrape_thread: Optional[ScrapeWorker] = None

        self.setWindowTitle("sources, frequencies & background crawler")
        self.resize(880, 620)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        active_tab = QWidget()
        active_layout = QVBoxLayout(active_tab)
        form = QFormLayout()

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(ROUTE_MODES)
        idx = self.mode_combo.findText(normalize_route_mode(cfg.mode))
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)

        self.local_rf_chk = QCheckBox("Local RF / SDR / Teletext hardware route active")
        self.local_rf_chk.setChecked(cfg.local_rf_available)

        self.radio_chk = QCheckBox("Enable Radio / Frequency / WebSDR stream route")
        self.radio_chk.setChecked(cfg.enable_radio)

        self.teletext_chk = QCheckBox("Enable Teletext / Teletext webpage route")
        self.teletext_chk.setChecked(cfg.enable_teletext)

        self.rss_chk = QCheckBox("Enable Web / RSS / Atom & Index standard route")
        self.rss_chk.setChecked(cfg.enable_rss)

        self.freq_edit = QLineEdit(", ".join(cfg.frequency_endpoints))
        self.freq_edit.setPlaceholderText("e.g. 14100kHz, 156.8MHz, or RF/SDR endpoints...")
        self.websdr_edit = QLineEdit(cfg.websdr_url)
        self.radio_url_edit = QLineEdit(cfg.radio_stream_url)
        self.teletext_url_edit = QLineEdit(cfg.teletext_web_url)
        self.rss_urls_edit = QLineEdit(", ".join(cfg.rss_feed_urls))

        form.addRow("route mode:", self.mode_combo)
        form.addRow("hardware:", self.local_rf_chk)
        form.addRow("standard routes:", self.rss_chk)
        form.addRow("", self.radio_chk)
        form.addRow("", self.teletext_chk)
        form.addRow("frequencies / bands:", self.freq_edit)
        form.addRow("websdr / radio portal:", self.websdr_edit)
        form.addRow("radio stream url:", self.radio_url_edit)
        form.addRow("teletext web url:", self.teletext_url_edit)
        form.addRow("web / rss source urls:", self.rss_urls_edit)
        active_layout.addLayout(form)
        active_layout.addStretch()

        disc_tab = QWidget()
        disc_layout = QVBoxLayout(disc_tab)

        queries_initial = self.reg.search_queries or list(DEFAULT_SEARCH_QUERIES)
        self.queries_edit = QLineEdit(", ".join(queries_initial))
        self.queries_edit.setPlaceholderText("Search queries (comma-separated)...")

        self.index_urls_edit = QLineEdit(", ".join(self.reg.index_urls))
        self.index_urls_edit.setPlaceholderText("Optional index or directory URLs (comma-separated)...")

        self.scan_btn = QPushButton("scrape now")
        self.scan_btn.clicked.connect(self._scrape_web)

        q_row = QHBoxLayout()
        q_row.addWidget(QLabel("queries:"))
        q_row.addWidget(self.queries_edit, stretch=1)
        q_row.addWidget(self.scan_btn)
        disc_layout.addLayout(q_row)

        idx_row = QHBoxLayout()
        idx_row.addWidget(QLabel("index urls:"))
        idx_row.addWidget(self.index_urls_edit, stretch=1)
        disc_layout.addLayout(idx_row)

        bg_row = QHBoxLayout()
        self.auto_crawl_chk = QCheckBox("auto-crawl in background")
        self.auto_crawl_chk.setChecked(self.reg.auto_crawl_enabled)

        self.auto_approve_chk = QCheckBox("auto-activate non-blocked discovered feeds")
        self.auto_approve_chk.setChecked(self.reg.auto_approve_discovered)

        self.crawl_interval_spin = QSpinBox()
        self.crawl_interval_spin.setRange(30, 7200)
        self.crawl_interval_spin.setSingleStep(30)
        self.crawl_interval_spin.setValue(int(max(30, self.reg.crawl_interval_sec)))

        bg_row.addWidget(self.auto_crawl_chk)
        bg_row.addWidget(self.auto_approve_chk)
        bg_row.addWidget(QLabel("crawl every (sec):"))
        bg_row.addWidget(self.crawl_interval_spin)
        bg_row.addStretch()
        disc_layout.addLayout(bg_row)

        self.cand_table = QTableWidget(0, 6)
        self.cand_table.setAlternatingRowColors(True)
        self.cand_table.setHorizontalHeaderLabels([
            "Status", "Type", "Title", "Domain", "Sample Headline", "URL"
        ])
        ch = self.cand_table.horizontalHeader()
        if ch is not None:
            ch.setSectionResizeMode(QHeaderView.Stretch)
        self.cand_table.setSelectionBehavior(QTableWidget.SelectRows)
        disc_layout.addWidget(self.cand_table)

        action_row = QHBoxLayout()
        approve_btn = QPushButton("approve selected")
        approve_btn.clicked.connect(self._approve_selected)
        approve_all_btn = QPushButton("approve all pending")
        approve_all_btn.clicked.connect(self._approve_all_pending)
        reject_btn = QPushButton("reject selected")
        reject_btn.clicked.connect(self._reject_selected)
        block_dom_btn = QPushButton("block domain")
        block_dom_btn.clicked.connect(self._block_selected_domain)
        self.disc_status_label = QLabel("")
        action_row.addWidget(approve_btn)
        action_row.addWidget(approve_all_btn)
        action_row.addWidget(reject_btn)
        action_row.addWidget(block_dom_btn)
        action_row.addStretch()
        action_row.addWidget(self.disc_status_label)
        disc_layout.addLayout(action_row)

        tabs.addTab(active_tab, "active sources & mode")
        tabs.addTab(disc_tab, "background crawler & vet")

        self._refresh_candidate_table()

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _sync_fields_from_config(self) -> None:
        with self.engine._state_lock:
            cfg = self.reg.config
            rss_txt = ", ".join(cfg.rss_feed_urls)
            freq_txt = ", ".join(cfg.frequency_endpoints)
            radio_txt = cfg.radio_stream_url
            tt_txt = cfg.teletext_web_url
        self.rss_urls_edit.setText(rss_txt)
        self.freq_edit.setText(freq_txt)
        self.radio_url_edit.setText(radio_txt)
        self.teletext_url_edit.setText(tt_txt)

    def _refresh_candidate_table(self) -> None:
        with self.engine._state_lock:
            cands = list(self.reg.candidates)
            blocked_cnt = len(self.reg.rejected_domains)
        self.cand_table.setRowCount(len(cands))
        for r, c in enumerate(cands):
            vals = [c.status, c.source_type, c.title, c.domain, c.sample_headline or "—", c.url]
            for col, val in enumerate(vals):
                cell = QTableWidgetItem(val)
                _make_read_only(cell)
                if c.status == "approved":
                    cell.setForeground(QColor("#e6b450"))
                elif c.status == "rejected":
                    cell.setForeground(QColor("#6a6a6a"))
                self.cand_table.setItem(r, col, cell)

        pending_cnt = sum(1 for c in cands if c.status == "pending")
        approved_cnt = sum(1 for c in cands if c.status == "approved")
        self.disc_status_label.setText(
            f"approved: {approved_cnt} · pending: {pending_cnt} · blocked domains: {blocked_cnt}"
        )

    def _selected_candidate_indices(self) -> List[int]:
        sel = self.cand_table.selectionModel()
        if sel is None:
            return []
        with self.engine._state_lock:
            total = len(self.reg.candidates)
        return sorted({idx.row() for idx in sel.selectedRows() if 0 <= idx.row() < total})

    def _scrape_web(self) -> None:
        if self._scrape_thread is not None and self._scrape_thread.isRunning():
            return
        queries = [q.strip() for q in self.queries_edit.text().split(",") if q.strip()]
        idx_urls = [u.strip() for u in self.index_urls_edit.text().split(",") if u.strip()]
        with self.engine._state_lock:
            self.reg.search_queries = queries
            self.reg.index_urls = idx_urls
            self.reg.auto_approve_discovered = self.auto_approve_chk.isChecked()
            known = (
                set(self.reg.config.rss_feed_urls)
                | set(self.reg.config.frequency_endpoints)
                | {c.url for c in self.reg.candidates}
            )
            rej_urls = set(self.reg.rejected_urls)
            rej_doms = set(self.reg.rejected_domains)
            fetcher = self.reg.config.http_fetcher or default_http_fetch
            timeout = self.reg.config.request_timeout_sec

        self.scan_btn.setEnabled(False)
        self.disc_status_label.setText("scraping in background...")

        self._scrape_thread = ScrapeWorker(
            queries=queries,
            index_urls=idx_urls,
            known_urls=known,
            rejected_urls=rej_urls,
            rejected_domains=rej_doms,
            fetcher=fetcher,
            timeout_sec=timeout,
        )
        self._scrape_thread.finished_scrape.connect(self._on_scrape_finished)
        self._scrape_thread.start()

    def _on_scrape_finished(self, found: list) -> None:
        self.scan_btn.setEnabled(True)
        with self.engine._state_lock:
            if self.reg.auto_approve_discovered:
                for cand in found:
                    apply_candidate_approval(self.reg, cand, approved=True)
            self.reg.candidates.extend(found)
            cands_copy = list(self.reg.candidates)
        _sync_approved_sources_to_config(self.engine, cands_copy)
        self._sync_fields_from_config()
        self._refresh_candidate_table()

    def _approve_selected(self) -> None:
        with self.engine._state_lock:
            for r in self._selected_candidate_indices():
                apply_candidate_approval(self.reg, self.reg.candidates[r], approved=True)
            cands_copy = list(self.reg.candidates)
        _sync_approved_sources_to_config(self.engine, cands_copy)
        self._sync_fields_from_config()
        self._refresh_candidate_table()

    def _approve_all_pending(self) -> None:
        with self.engine._state_lock:
            for cand in self.reg.candidates:
                if cand.status == "pending":
                    apply_candidate_approval(self.reg, cand, approved=True)
            cands_copy = list(self.reg.candidates)
        _sync_approved_sources_to_config(self.engine, cands_copy)
        self._sync_fields_from_config()
        self._refresh_candidate_table()

    def _reject_selected(self) -> None:
        with self.engine._state_lock:
            for r in self._selected_candidate_indices():
                apply_candidate_approval(self.reg, self.reg.candidates[r], approved=False)
            save_source_registry(self.reg, self.engine.registry_path)
        self._refresh_candidate_table()

    def _block_selected_domain(self) -> None:
        with self.engine._state_lock:
            for r in self._selected_candidate_indices():
                cand = self.reg.candidates[r]
                cand.status = "rejected"
                self.reg.rejected_urls.add(cand.url)
                if cand.domain:
                    self.reg.rejected_domains.add(cand.domain.lower())
            save_source_registry(self.reg, self.engine.registry_path)
        self._refresh_candidate_table()

    def _save(self) -> None:
        with self.engine._state_lock:
            old_cfg = self.reg.config
            rss_list = [u.strip() for u in self.rss_urls_edit.text().split(",") if u.strip()]
            freq_list = [f.strip() for f in self.freq_edit.text().split(",") if f.strip()]
            self.reg.search_queries = [q.strip() for q in self.queries_edit.text().split(",") if q.strip()]
            self.reg.index_urls = [u.strip() for u in self.index_urls_edit.text().split(",") if u.strip()]
            self.reg.auto_crawl_enabled = self.auto_crawl_chk.isChecked()
            self.reg.auto_approve_discovered = self.auto_approve_chk.isChecked()
            self.reg.crawl_interval_sec = float(self.crawl_interval_spin.value())

            mode_val = normalize_route_mode(self.mode_combo.currentText())
            enable_rss_val = self.rss_chk.isChecked() if mode_val != "rf_only" else False
            enable_radio_val = self.radio_chk.isChecked() if mode_val != "web_only" else False

            new_cfg = DataSourceConfig(
                mode=mode_val,
                enable_radio=enable_radio_val,
                enable_teletext=self.teletext_chk.isChecked(),
                enable_rss=enable_rss_val,
                allow_web_fallback=enable_rss_val,
                local_rf_available=self.local_rf_chk.isChecked(),
                websdr_url=self.websdr_edit.text().strip(),
                radio_stream_url=self.radio_url_edit.text().strip(),
                teletext_web_url=self.teletext_url_edit.text().strip(),
                rss_feed_urls=rss_list,
                frequency_endpoints=freq_list,
                request_timeout_sec=old_cfg.request_timeout_sec,
                local_rf_reader=old_cfg.local_rf_reader,
                frequency_scanner=old_cfg.frequency_scanner,
                http_fetcher=old_cfg.http_fetcher,
            )
            self.engine.set_source_config(new_cfg, mark_wizard_done=True)
        self.accept()

    def closeEvent(self, a0: Optional[QCloseEvent]) -> None:
        if self._scrape_thread is not None and self._scrape_thread.isRunning():
            self._scrape_thread.wait(1000)
        super().closeEvent(a0)


class FermiForecastDialog(QDialog):
    def __init__(self, cluster: TopicCluster, engine: AmbientForecasterTicker, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.cluster = cluster
        self.engine = engine
        self.setWindowTitle(f"cluster summary & fermi forecast — {cluster.label}")
        self.resize(700, 600)

        fc = cluster.forecast
        now = datetime.now(timezone.utc)

        layout = QVBoxLayout(self)

        summary_box = QGroupBox("short cluster summary & recent excerpts")
        summary_layout = QVBoxLayout(summary_box)
        self.summary_label = QLabel(build_cluster_short_summary(self.engine, self.cluster))
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("color: #e6b450; font-weight: 600;")
        summary_layout.addWidget(self.summary_label)

        with self.engine._state_lock:
            recent_ev = [
                f"  • [{ev.timestamp.strftime('%H:%M:%S')} · {ev.channel}] {ev.excerpt}"
                for ev in reversed(self.cluster.evidence[-5:])
            ]
        self.excerpts_box = QTextEdit()
        self.excerpts_box.setReadOnly(True)
        self.excerpts_box.setMaximumHeight(110)
        self.excerpts_box.setPlainText("\n".join(recent_ev) if recent_ev else "  (no items recorded)")
        summary_layout.addWidget(self.excerpts_box)
        layout.addWidget(summary_box)

        form = QFormLayout()

        self.question_edit = QLineEdit(
            fc.question_text if fc else f"Will '{cluster.label}' escalate or persist over 7 days?"
        )
        self.ref_class_edit = QLineEdit(
            fc.reference_class_note if fc else "Historical reference class / domain base rate"
        )

        self.use_base_rate_chk = QCheckBox("Anchor with historical base rate")
        self.use_base_rate_chk.setChecked(fc.base_rate is not None if fc else True)

        self.base_rate_spin = QDoubleSpinBox()
        self.base_rate_spin.setRange(0.01, 0.99)
        self.base_rate_spin.setSingleStep(0.05)
        self.base_rate_spin.setValue(fc.base_rate if (fc and fc.base_rate is not None) else 0.25)

        self.br_weight_spin = QDoubleSpinBox()
        self.br_weight_spin.setRange(0.0, 0.90)
        self.br_weight_spin.setSingleStep(0.05)
        self.br_weight_spin.setValue(fc.base_rate_weight if fc else 0.35)

        self.impact_spin = QDoubleSpinBox()
        self.impact_spin.setRange(0.10, 1.00)
        self.impact_spin.setSingleStep(0.05)
        self.impact_spin.setValue(fc.impact_score if fc else 0.70)

        self.horizon_days_spin = QSpinBox()
        self.horizon_days_spin.setRange(1, 365)
        if fc is not None:
            days = max(1, int((fc.resolution_deadline - now).total_seconds() // 86400))
            self.horizon_days_spin.setValue(days)
        else:
            self.horizon_days_spin.setValue(7)

        form.addRow("forecast question:", self.question_edit)
        form.addRow("reference class:", self.ref_class_edit)
        form.addRow(self.use_base_rate_chk, self.base_rate_spin)
        form.addRow("base rate weight:", self.br_weight_spin)
        form.addRow("impact score (i):", self.impact_spin)
        form.addRow("horizon (days):", self.horizon_days_spin)
        layout.addLayout(form)

        layout.addWidget(QLabel("fermi drivers (name, assumption, prob 0.01..0.99, weight):"))
        self.drivers_table = QTableWidget(0, 4)
        self.drivers_table.setAlternatingRowColors(True)
        self.drivers_table.setHorizontalHeaderLabels(["Driver Name", "Assumption", "Prob", "Weight"])
        hdr = self.drivers_table.horizontalHeader()
        if hdr is not None:
            hdr.setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.drivers_table)

        initial_drivers = fc.drivers if (fc and fc.drivers) else [
            FermiDriver("Primary catalyst", "Key escalation condition holds", 0.50, 0.50),
            FermiDriver("Countervailing restraint", "Deterrence or mitigation holds", 0.40, 0.50),
        ]
        for drv in initial_drivers:
            self._add_driver_row(drv)

        drv_btn_row = QHBoxLayout()
        add_drv_btn = QPushButton("+ add driver")
        add_drv_btn.clicked.connect(
            lambda: self._add_driver_row(FermiDriver("New driver", "Explicit assumption", 0.50, 0.25))
        )
        rem_drv_btn = QPushButton("- remove selected")
        rem_drv_btn.clicked.connect(self._remove_selected_driver)
        drv_btn_row.addWidget(add_drv_btn)
        drv_btn_row.addWidget(rem_drv_btn)
        drv_btn_row.addStretch()
        layout.addLayout(drv_btn_row)

        res_box = QGroupBox("post-hoc resolution & brier score")
        res_layout = QHBoxLayout(res_box)
        brier_curr = fc.brier_score() if fc else None
        self.brier_label = QLabel(f"brier score: {brier_curr:.4f}" if brier_curr is not None else "brier score: unresolved")
        resolve_yes_btn = QPushButton("resolve yes (y=1)")
        resolve_yes_btn.clicked.connect(lambda: self._resolve_outcome(True))
        resolve_no_btn = QPushButton("resolve no (y=0)")
        resolve_no_btn.clicked.connect(lambda: self._resolve_outcome(False))
        res_layout.addWidget(self.brier_label)
        res_layout.addStretch()
        res_layout.addWidget(resolve_yes_btn)
        res_layout.addWidget(resolve_no_btn)
        layout.addWidget(res_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_forecast)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_driver_row(self, drv: FermiDriver) -> None:
        r = self.drivers_table.rowCount()
        self.drivers_table.insertRow(r)
        self.drivers_table.setItem(r, 0, QTableWidgetItem(drv.name))
        self.drivers_table.setItem(r, 1, QTableWidgetItem(drv.assumption))
        self.drivers_table.setItem(r, 2, QTableWidgetItem(f"{drv.probability:.2f}"))
        self.drivers_table.setItem(r, 3, QTableWidgetItem(f"{drv.weight:.2f}"))

    def _remove_selected_driver(self) -> None:
        sel = self.drivers_table.selectionModel()
        if sel is None:
            return
        rows = sorted({idx.row() for idx in sel.selectedRows()}, reverse=True)
        for r in rows:
            self.drivers_table.removeRow(r)

    def _collect_drivers(self) -> List[FermiDriver]:
        drivers: List[FermiDriver] = []
        for r in range(self.drivers_table.rowCount()):
            name_it = self.drivers_table.item(r, 0)
            assump_it = self.drivers_table.item(r, 1)
            prob_it = self.drivers_table.item(r, 2)
            wt_it = self.drivers_table.item(r, 3)

            name = name_it.text().strip() if name_it else f"Driver {r + 1}"
            assump = assump_it.text().strip() if assump_it else ""
            try:
                prob = min(max(float(prob_it.text()), 0.01), 0.99) if prob_it else 0.50
            except ValueError:
                prob = 0.50
            try:
                wt = max(float(wt_it.text()), 0.0) if wt_it else 0.25
            except ValueError:
                wt = 0.25

            drivers.append(FermiDriver(name=name or f"Driver {r + 1}", assumption=assump, probability=prob, weight=wt))
        return drivers

    def _resolve_outcome(self, outcome: bool) -> None:
        score = self.engine.resolve_question(self.cluster.cluster_id, outcome)
        if score is not None:
            label = "YES (1)" if outcome else "NO (0)"
            self.brier_label.setText(f"resolved {label} · brier score: {score:.4f}")

    def _save_forecast(self) -> None:
        now = datetime.now(timezone.utc)
        with self.engine._state_lock:
            existing_fc = self.cluster.forecast
            q_id = existing_fc.question_id if existing_fc else f"q_{self.cluster.cluster_id}"
            history = list(existing_fc.history) if existing_fc else []
            resolved = existing_fc.resolved_outcome if existing_fc else None
            has_evidence = len(self.cluster.evidence) > 0

            base_rate_val = self.base_rate_spin.value() if self.use_base_rate_chk.isChecked() else None

            fc = ForecastQuestion(
                question_id=q_id,
                topic_label=self.cluster.label,
                question_text=self.question_edit.text().strip() or self.cluster.label,
                resolution_deadline=now + timedelta(days=self.horizon_days_spin.value()),
                impact_score=self.impact_spin.value(),
                base_rate=base_rate_val,
                base_rate_weight=self.br_weight_spin.value(),
                reference_class_note=self.ref_class_edit.text().strip(),
                drivers=self._collect_drivers(),
                current_probability=existing_fc.current_probability if existing_fc else 0.45,
                history=history,
                resolved_outcome=resolved,
            )
            self.cluster.forecast = fc
            self.engine.register_cluster_with_forecast(
                self.cluster,
                reason="fermi_editor_update",
                reset_probability=not has_evidence,
            )
        self.accept()


class StreamWorker(QThread):
    item_evaluated = pyqtSignal(object)
    route_updated = pyqtSignal(str)
    discovered_count = pyqtSignal(int)
    hardware_status = pyqtSignal(bool, bool, str)

    def __init__(
        self,
        engine: AmbientForecasterTicker,
        poll_interval_sec: float = 15.0,
        poll_enabled: bool = True,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.poll_interval_sec = poll_interval_sec
        self.poll_enabled = poll_enabled
        self._running = True
        self._paused = False
        self._last_crawl_ts: float = -1e9
        self._last_hw_check_ts: float = -1e9
        self._initial_scrape_done: bool = False

    @property
    def is_paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

    def trigger_immediate_scrape(self) -> None:
        self._last_crawl_ts = -1e9
        self._initial_scrape_done = False

    def stop(self) -> None:
        self._running = False

    def _standard_route_probe(self, now_mono: float) -> None:
        if (now_mono - self._last_hw_check_ts) < 5.0:
            return
        self._last_hw_check_ts = now_mono

        with self.engine._state_lock:
            reg = self.engine.registry_state
            cfg = reg.config
            mode_val = normalize_route_mode(cfg.mode)
            fetcher = cfg.http_fetcher or default_http_fetch
            timeout = cfg.request_timeout_sec
            auto_crawl = reg.auto_crawl_enabled
            crawl_interval = reg.crawl_interval_sec

        has_net = check_internet_available(timeout_sec=timeout, fetcher=fetcher)
        has_ant, ant_detail = detect_radio_antenna_or_sdr(self.engine)

        with self.engine._state_lock:
            config_changed = False
            if cfg.mode != mode_val:
                cfg.mode = mode_val
                config_changed = True

            if has_ant and mode_val != "web_only":
                if not cfg.local_rf_available or not cfg.enable_radio:
                    cfg.local_rf_available = True
                    cfg.enable_radio = True
                    config_changed = True

            if has_net and mode_val != "rf_only":
                if not cfg.enable_rss or not cfg.allow_web_fallback:
                    cfg.enable_rss = True
                    cfg.allow_web_fallback = True
                    config_changed = True

            if config_changed:
                self.engine.set_source_config(cfg, mark_wizard_done=True)

        self.hardware_status.emit(has_net, has_ant, ant_detail)

        if has_net and mode_val != "rf_only" and auto_crawl:
            due = (not self._initial_scrape_done) or (
                (now_mono - self._last_crawl_ts) >= max(30.0, crawl_interval)
            )
            if due:
                self._initial_scrape_done = True
                self._last_crawl_ts = now_mono
                newly_found = self.engine.background_discover_sources()
                if newly_found:
                    with self.engine._state_lock:
                        cands_copy = list(self.engine.registry_state.candidates)
                    _sync_approved_sources_to_config(self.engine, cands_copy)
                    self.discovered_count.emit(len(newly_found))

    def run(self) -> None:
        while self._running:
            if not self.engine.wait_for_warmup(timeout_sec=0.5):
                continue
            if self._paused or not self.poll_enabled:
                time.sleep(0.2)
                continue

            now_mono = time.monotonic()
            self._standard_route_probe(now_mono)
            if not self._running:
                return

            live_results, route = self.engine.poll_configured_sources()
            self.route_updated.emit(route)
            for lres in live_results:
                if not self._running:
                    return
                self.item_evaluated.emit(lres)

            steps = int(max(1, round(self.poll_interval_sec / 0.2)))
            for _ in range(steps):
                if not self._running:
                    return
                time.sleep(0.2)


class AmbientTickerWindow(QMainWindow):
    def __init__(self, engine: AmbientForecasterTicker) -> None:
        super().__init__()
        self.engine = engine
        self._eval_cache: List[EvaluationResult] = []
        self._poll_once_thread: Optional[PollOnceWorker] = None
        self._warmup_thread: Optional[WarmupWorker] = None
        self._scrape_threads: List[ScrapeWorker] = []
        self._last_net_tag = "net:?"
        self._last_rf_tag = "rf:?"
        self._warmup_seeded_count = 0

        self.setWindowTitle("ambient news ticker — forecast & salience monitor")
        self.resize(1320, 830)
        self.setStyleSheet(PRO_DARK_STYLESHEET)
        self._init_ui()

        self.worker = StreamWorker(self.engine, poll_interval_sec=15.0, poll_enabled=True)
        self.worker.item_evaluated.connect(self.on_item_evaluated)
        self.worker.route_updated.connect(self._on_route_updated)
        self.worker.discovered_count.connect(self._on_background_discovered)
        self.worker.hardware_status.connect(self._on_hardware_status)
        self.worker.start()

        QTimer.singleShot(50, self._autostart_pipeline)

    def _format_route_status(self, route: str) -> str:
        warm_part = f" · warm:{self._warmup_seeded_count}" if self._warmup_seeded_count > 0 else ""
        return f"route: {route} [{self._last_net_tag} · {self._last_rf_tag}{warm_part}]"

    def _autostart_pipeline(self) -> None:
        with self.engine._state_lock:
            reg = self.engine.registry_state
            reg.wizard_completed = True
            reg.auto_crawl_enabled = True
            reg.auto_approve_discovered = True
            reg.config.mode = normalize_route_mode(reg.config.mode)
            if not reg.search_queries:
                reg.search_queries = list(DEFAULT_SEARCH_QUERIES)

            has_ant, ant_detail = detect_radio_antenna_or_sdr(self.engine)
            self._last_rf_tag = f"rf:{ant_detail}" if has_ant else "rf:none"
            if has_ant and reg.config.mode != "web_only":
                reg.config.local_rf_available = True
                reg.config.enable_radio = True

            fetcher = reg.config.http_fetcher or default_http_fetch
            timeout = reg.config.request_timeout_sec
            mode_val = reg.config.mode
            cands_copy = list(reg.candidates)

        has_net = check_internet_available(timeout_sec=timeout, fetcher=fetcher)
        self._last_net_tag = "net:ok" if has_net else "net:off"
        with self.engine._state_lock:
            if has_net and mode_val != "rf_only":
                self.engine.registry_state.config.enable_rss = True
                self.engine.registry_state.config.allow_web_fallback = True
            save_source_registry(self.engine.registry_state, self.engine.registry_path)

        _sync_approved_sources_to_config(self.engine, cands_copy)

        self.route_label.setText(self._format_route_status("warming up historical baseline..."))
        self._warmup_thread = WarmupWorker(self.engine, db_limit=120)
        self._warmup_thread.finished_warmup.connect(self._on_warmup_finished)
        self._warmup_thread.start()

    def _on_warmup_finished(self, seeded_count: int) -> None:
        self._warmup_seeded_count = max(0, int(seeded_count))
        with self.engine._state_lock:
            mode_val = normalize_route_mode(self.engine.source_config.mode)
            fetcher = self.engine.source_config.http_fetcher or default_http_fetch
            timeout = self.engine.source_config.request_timeout_sec

        has_net = check_internet_available(timeout_sec=timeout, fetcher=fetcher)
        self._last_net_tag = "net:ok" if has_net else "net:off"

        if has_net and mode_val != "rf_only":
            self._kickoff_standard_web_scrape()
        else:
            self.route_label.setText(self._format_route_status("baseline ready — polling..."))
            self._poll_sources_now()

    def _spawn_scrape_worker(self, on_done) -> None:
        with self.engine._state_lock:
            reg = self.engine.registry_state
            if not reg.search_queries:
                reg.search_queries = list(DEFAULT_SEARCH_QUERIES)
            queries = list(reg.search_queries)
            index_urls = list(reg.index_urls)
            known = (
                set(reg.config.rss_feed_urls)
                | set(reg.config.frequency_endpoints)
                | {c.url for c in reg.candidates}
            )
            rej_urls = set(reg.rejected_urls)
            rej_doms = set(reg.rejected_domains)
            fetcher = reg.config.http_fetcher or default_http_fetch
            timeout = reg.config.request_timeout_sec

        worker = ScrapeWorker(
            queries=queries,
            index_urls=index_urls,
            known_urls=known,
            rejected_urls=rej_urls,
            rejected_domains=rej_doms,
            fetcher=fetcher,
            timeout_sec=timeout,
        )
        worker.finished_scrape.connect(on_done)
        worker.finished.connect(lambda w=worker: self._cleanup_scrape_worker(w))
        self._scrape_threads.append(worker)
        worker.start()

    def _cleanup_scrape_worker(self, worker: ScrapeWorker) -> None:
        if worker in self._scrape_threads:
            self._scrape_threads.remove(worker)

    def _kickoff_standard_web_scrape(self) -> None:
        self.route_label.setText(self._format_route_status("discovering web/RSS sources..."))
        self._spawn_scrape_worker(self._on_autostart_scrape_finished)

    def _on_autostart_scrape_finished(self, found: list) -> None:
        with self.engine._state_lock:
            reg = self.engine.registry_state
            if found:
                if reg.auto_approve_discovered:
                    for cand in found:
                        apply_candidate_approval(reg, cand, approved=True)
                reg.candidates.extend(found)
            cands_copy = list(reg.candidates)
            route_now = self.engine.source_connector.last_active_route

        _sync_approved_sources_to_config(self.engine, cands_copy)
        with self.engine._state_lock:
            save_source_registry(self.engine.registry_state, self.engine.registry_path)

        if found:
            self.route_label.setText(
                self._format_route_status(f"{route_now} (+{len(found)} web/rss)")
            )
        else:
            self.route_label.setText(self._format_route_status(route_now))
        self._poll_sources_now()

    def _init_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(10)

        ticker_box = QGroupBox("ambient ticker strip — dynamic ttl alert queue (click to pause)")
        ticker_layout = QVBoxLayout(ticker_box)

        strip_row = QHBoxLayout()
        self.marquee_widget = TickerMarqueeWidget(self)
        self.prev_alert_btn = QPushButton("prev")
        self.prev_alert_btn.setMaximumWidth(78)
        self.prev_alert_btn.clicked.connect(lambda: self.marquee_widget.step_alert(-1))

        self.next_alert_btn = QPushButton("next")
        self.next_alert_btn.setMaximumWidth(78)
        self.next_alert_btn.clicked.connect(lambda: self.marquee_widget.step_alert(1))

        self.ticker_pause_btn = QPushButton("pause scroll")
        self.ticker_pause_btn.setMaximumWidth(124)
        self.ticker_pause_btn.clicked.connect(self._toggle_ticker_scroll)

        strip_row.addWidget(self.marquee_widget, stretch=1)
        strip_row.addWidget(self.prev_alert_btn)
        strip_row.addWidget(self.next_alert_btn)
        strip_row.addWidget(self.ticker_pause_btn)
        ticker_layout.addLayout(strip_row)

        main_layout.addWidget(ticker_box)

        ctrl_layout = QHBoxLayout()

        ctrl_layout.addWidget(QLabel("route mode:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(ROUTE_MODES)
        m_idx = self.mode_combo.findText(normalize_route_mode(self.engine.source_config.mode))
        if m_idx >= 0:
            self.mode_combo.setCurrentIndex(m_idx)
        self.mode_combo.currentTextChanged.connect(self._on_quick_mode_changed)
        ctrl_layout.addWidget(self.mode_combo)

        self.live_poll_chk = QCheckBox("auto-poll")
        self.live_poll_chk.setChecked(True)
        self.live_poll_chk.toggled.connect(self._toggle_live_poll)
        ctrl_layout.addWidget(self.live_poll_chk)

        self.bg_crawl_chk = QCheckBox("auto-crawl")
        self.bg_crawl_chk.setChecked(self.engine.registry_state.auto_crawl_enabled)
        self.bg_crawl_chk.toggled.connect(self._toggle_bg_crawl)
        ctrl_layout.addWidget(self.bg_crawl_chk)

        self.poll_now_btn = QPushButton("poll now")
        self.poll_now_btn.setStyleSheet(
            "QPushButton { background-color: #242424; color: #ddb52f; border: 1px solid #c9a227; }"
            "QPushButton:hover { background-color: #c9a227; color: #0f0f0f; }"
        )
        self.poll_now_btn.clicked.connect(self._poll_sources_now)
        ctrl_layout.addWidget(self.poll_now_btn)

        self.source_cfg_btn = QPushButton("sources & vet...")
        self.source_cfg_btn.clicked.connect(self._open_source_dialog)
        ctrl_layout.addWidget(self.source_cfg_btn)

        self.settings_btn = QPushButton("settings...")
        self.settings_btn.clicked.connect(self._open_settings_dialog)
        ctrl_layout.addWidget(self.settings_btn)

        self.edit_fermi_btn = QPushButton("cluster summary / fermi...")
        self.edit_fermi_btn.clicked.connect(self._open_fermi_editor)
        ctrl_layout.addWidget(self.edit_fermi_btn)

        self.pause_btn = QPushButton("pause poller")
        self.pause_btn.clicked.connect(self._toggle_stream_pause)
        ctrl_layout.addWidget(self.pause_btn)

        self.route_label = QLabel(self._format_route_status(self.engine.source_connector.last_active_route))
        self.route_label.setStyleSheet("color: #e6b450; font-weight: 600;")
        ctrl_layout.addWidget(self.route_label)

        self.brier_summary_label = QLabel("mean brier: n/a")
        self.brier_summary_label.setStyleSheet("color: #a0a0a0; font-weight: 600;")
        ctrl_layout.addWidget(self.brier_summary_label)

        ctrl_layout.addStretch()
        main_layout.addLayout(ctrl_layout)

        inject_layout = QHBoxLayout()
        self.channel_input = QLineEdit("HF-Maritime-14MHz")
        self.channel_input.setMaximumWidth(160)
        self.band_combo = QComboBox()
        self.band_combo.addItems(["shortwave", "teletext", "rss", "vhf", "navtex", "web-sdr"])
        self.weight_spin = QDoubleSpinBox()
        self.weight_spin.setRange(0.05, 0.99)
        self.weight_spin.setSingleStep(0.05)
        self.weight_spin.setValue(0.85)
        self.claim_input = QLineEdit()
        self.claim_input.setPlaceholderText("Ingest a broadcast claim into the model...")
        self.inject_btn = QPushButton("ingest claim")
        self.inject_btn.setStyleSheet(
            "QPushButton { background-color: #c9a227; color: #0f0f0f; border: 1px solid #ddb52f; }"
            "QPushButton:hover { background-color: #ddb52f; color: #0f0f0f; }"
        )
        self.inject_btn.clicked.connect(self._inject_custom_claim)

        inject_layout.addWidget(QLabel("channel:"))
        inject_layout.addWidget(self.channel_input)
        inject_layout.addWidget(QLabel("band:"))
        inject_layout.addWidget(self.band_combo)
        inject_layout.addWidget(QLabel("src wt:"))
        inject_layout.addWidget(self.weight_spin)
        inject_layout.addWidget(self.claim_input, stretch=1)
        inject_layout.addWidget(self.inject_btn)
        main_layout.addLayout(inject_layout)

        splitter = QSplitter(Qt.Orientation.Vertical)

        cluster_box = QGroupBox("active topic clusters & fermi forecasts (click to summarize · double-click for popup)")
        cluster_layout = QVBoxLayout(cluster_box)
        self.cluster_table = QTableWidget(0, 10)
        self.cluster_table.setAlternatingRowColors(True)
        self.cluster_table.setHorizontalHeaderLabels([
            "Cluster", "Provenance", "Base Rate", "P(Event)", "Impact",
            "Salience (S)", "Unexp (U)", "Accum (A)", "Conf (C)", "Power (Ψ)",
        ])
        cluster_header = self.cluster_table.horizontalHeader()
        if cluster_header is not None:
            cluster_header.setSectionResizeMode(QHeaderView.Stretch)
        self.cluster_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.cluster_table.itemSelectionChanged.connect(self._on_cluster_selected)
        self.cluster_table.cellDoubleClicked.connect(lambda _r, _c: self._open_fermi_editor())
        cluster_layout.addWidget(self.cluster_table)
        splitter.addWidget(cluster_box)

        bottom_splitter = QSplitter(Qt.Orientation.Horizontal)

        eval_box = QGroupBox("evaluated items (alerted & shadow-suppressed)")
        eval_layout = QVBoxLayout(eval_box)
        self.eval_table = QTableWidget(0, 7)
        self.eval_table.setAlternatingRowColors(True)
        self.eval_table.setHorizontalHeaderLabels([
            "Time", "Status", "Channel", "Cluster", "Echo", "Power", "Triggers",
        ])
        eval_header = self.eval_table.horizontalHeader()
        if eval_header is not None:
            eval_header.setSectionResizeMode(QHeaderView.Stretch)
        self.eval_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.eval_table.itemSelectionChanged.connect(self._on_eval_selected)
        eval_layout.addWidget(self.eval_table)
        bottom_splitter.addWidget(eval_box)

        detail_box = QGroupBox("cluster summary, grounded excerpts & forecast snapshot")
        detail_layout = QVBoxLayout(detail_box)
        self.detail_view = QTextEdit()
        self.detail_view.setReadOnly(True)
        self.detail_view.setFont(QFont("JetBrains Mono", 10))
        self.detail_view.font().setStyleHint(QFont.StyleHint.Monospace)
        detail_layout.addWidget(self.detail_view)
        bottom_splitter.addWidget(detail_box)

        bottom_splitter.setSizes([740, 520])
        splitter.addWidget(bottom_splitter)
        splitter.setSizes([290, 430])

        main_layout.addWidget(splitter, stretch=1)

    def _on_quick_mode_changed(self, mode_text: str) -> None:
        with self.engine._state_lock:
            cfg = self.engine.source_config
            mode_val = normalize_route_mode(mode_text)
            cfg.mode = mode_val
            if mode_val == "web_only":
                cfg.enable_rss = True
                cfg.allow_web_fallback = True
                cfg.enable_radio = False
            elif mode_val == "rf_only":
                cfg.enable_radio = True
                cfg.enable_rss = False
                cfg.allow_web_fallback = False
            elif mode_val == "rf_and_web":
                cfg.enable_rss = True
                cfg.allow_web_fallback = True
                cfg.enable_radio = True
            else:
                cfg.enable_rss = True
                cfg.allow_web_fallback = True
                if cfg.local_rf_available or cfg.frequency_endpoints:
                    cfg.enable_radio = True
            self.engine.set_source_config(cfg)
            route_now = self.engine.source_connector.last_active_route
        self.route_label.setText(self._format_route_status(route_now))
        self._poll_sources_now()

    def _toggle_live_poll(self, checked: bool) -> None:
        self.worker.poll_enabled = checked

    def _toggle_bg_crawl(self, checked: bool) -> None:
        with self.engine._state_lock:
            self.engine.registry_state.auto_crawl_enabled = checked
            save_source_registry(self.engine.registry_state, self.engine.registry_path)
        if checked:
            self.worker.trigger_immediate_scrape()
            self._kickoff_standard_web_scrape()

    def _on_hardware_status(self, has_net: bool, has_ant: bool, ant_detail: str) -> None:
        self._last_net_tag = "net:ok" if has_net else "net:off"
        self._last_rf_tag = f"rf:{ant_detail}" if has_ant else "rf:none"
        with self.engine._state_lock:
            cur_mode = normalize_route_mode(self.engine.source_config.mode)
            route_now = self.engine.source_connector.last_active_route
        m_idx = self.mode_combo.findText(cur_mode)
        if m_idx >= 0 and self.mode_combo.currentIndex() != m_idx:
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(m_idx)
            self.mode_combo.blockSignals(False)
        self.route_label.setText(self._format_route_status(route_now))

    def _on_background_discovered(self, count: int) -> None:
        with self.engine._state_lock:
            route_now = self.engine.source_connector.last_active_route
        self.route_label.setText(
            self._format_route_status(f"{route_now} (+{count} crawled)")
        )
        self._poll_sources_now()

    def _on_route_updated(self, route: str) -> None:
        self.route_label.setText(self._format_route_status(route))

    def _poll_sources_now(self) -> None:
        if self._poll_once_thread is not None and self._poll_once_thread.isRunning():
            return
        self.poll_now_btn.setEnabled(False)
        self.route_label.setText(self._format_route_status("polling..."))
        self._poll_once_thread = PollOnceWorker(self.engine)
        self._poll_once_thread.finished_poll.connect(self._on_poll_once_finished)
        self._poll_once_thread.start()

    def _on_poll_once_finished(self, results: list, route: str) -> None:
        self.poll_now_btn.setEnabled(True)
        self.route_label.setText(self._format_route_status(route))
        for res in results:
            self.on_item_evaluated(res)

    def _open_source_dialog(self) -> None:
        dlg = DataSourceDialog(self.engine, self)
        if dlg.exec_() == QDialog.Accepted:
            with self.engine._state_lock:
                cur_mode = normalize_route_mode(self.engine.source_config.mode)
                auto_crawl = self.engine.registry_state.auto_crawl_enabled
                route_now = self.engine.source_connector.last_active_route
            m_idx = self.mode_combo.findText(cur_mode)
            if m_idx >= 0:
                self.mode_combo.setCurrentIndex(m_idx)
            self.bg_crawl_chk.setChecked(auto_crawl)
            self.route_label.setText(self._format_route_status(route_now))
            self._poll_sources_now()

    def _open_settings_dialog(self) -> None:
        dlg = EngineSettingsDialog(self.engine, self)
        if dlg.exec_() == QDialog.Accepted:
            self._update_brier_summary()
            cl = self._selected_cluster()
            if cl is not None:
                self._render_cluster_summary(cl)

    def _selected_cluster(self) -> Optional[TopicCluster]:
        with self.engine._state_lock:
            clusters = list(self.engine.clusters.values())
        sel = self.cluster_table.selectionModel()
        if sel is not None:
            rows = sel.selectedRows()
            if rows:
                idx = rows[0].row()
                if 0 <= idx < len(clusters):
                    return clusters[idx]
        if clusters:
            return clusters[0]
        return None

    def _on_cluster_selected(self) -> None:
        cl = self._selected_cluster()
        if cl is not None:
            self._render_cluster_summary(cl)

    def _render_cluster_summary(self, cl: TopicCluster) -> None:
        now = datetime.now(timezone.utc)
        with self.engine._state_lock:
            fc = cl.forecast
            sal = self.engine._compute_salience(cl, now)
            acc = self.engine._compute_accumulation(
                cl, now, fc.current_probability if fc is not None else None
            )
            conf, prov = self.engine._compute_confidence(cl, sal)
            short_sum = build_cluster_short_summary(self.engine, cl)
            prior_s = (
                f"{fc.history[-1].prior_prob:.3f}"
                if (fc is not None and fc.history)
                else (f"{fc.compute_fermi_prior():.3f}" if fc is not None else "N/A")
            )
            post_s = f"{fc.current_probability:.3f}" if fc is not None else "N/A"
            br_line = (
                f"{fc.base_rate:.1%} (wt={fc.base_rate_weight:.2f}) — {fc.reference_class_note}"
                if (fc is not None and fc.base_rate is not None)
                else "None (Fermi drivers only)"
            )
            brier_line = (
                f"{fc.brier_score():.4f}"
                if (fc is not None and fc.brier_score() is not None)
                else "unresolved"
            )
            rule_line = fc.resolution_rule if fc is not None else "N/A"
            excerpts = [
                f"  • [{ev.timestamp.strftime('%H:%M:%S')} · {ev.channel}] {ev.excerpt}"
                for ev in reversed(cl.evidence[-5:])
            ]

        lines = [
            "Cluster Summary:",
            f"  {short_sum}",
            "",
            f"Cluster:      {cl.label} ({cl.cluster_id}) [{cl.domain_tag}/{cl.regime_tag}]",
            f"Provenance:   {prov} | Items: {len(cl.evidence)}",
            f"Base Rate:    {br_line}",
            f"Forecast P:   {prior_s} -> {post_s} | Brier: {brier_line}",
            f"Rule:         {rule_line}",
            f"Signals:      S={sal:.2f}, U={cl.last_unexpectedness:.2f}, A={acc:.2f}, C={conf:.2f}, Ψ={cl.last_power_level:.3f}",
            "",
            "Grounded Excerpts:",
            "\n".join(excerpts) if excerpts else "  (none)",
        ]
        self.detail_view.setPlainText("\n".join(lines))

    def _open_fermi_editor(self) -> None:
        cl = self._selected_cluster()
        if cl is None:
            return
        dlg = FermiForecastDialog(cl, self.engine, self)
        if dlg.exec_() == QDialog.Accepted:
            self._update_brier_summary()
            now = self._eval_cache[0].timestamp if self._eval_cache else datetime.now(timezone.utc)
            with self.engine._state_lock:
                sal = self.engine._compute_salience(cl, now)
                acc = self.engine._compute_accumulation(
                    cl, now, cl.forecast.current_probability if cl.forecast else None
                )
                post_p = cl.forecast.current_probability if cl.forecast else None
                excerpts = [f"[{cl.label}] {cl.forecast.question_text}"] if cl.forecast else []
            fallback_res = self._eval_cache[0] if self._eval_cache else EvaluationResult(
                timestamp=now,
                cluster_id=cl.cluster_id,
                cluster_label=cl.label,
                item_id="fermi_update",
                channel="Fermi-Editor",
                is_echo=False,
                interest_score=1.0,
                interest_multiplier=1.0,
                salience=sal,
                unexpectedness=0.0,
                accumulation=acc,
                confidence=0.5,
                power_level=0.0,
                prior_prob=None,
                posterior_prob=post_p,
                provenance_tag="FERMI-ANCHORED",
                trigger_reasons=[],
                alerted=False,
                ticker_line=None,
                supporting_excerpts=excerpts,
            )
            self._refresh_cluster_table(fallback_res)
            self._render_cluster_summary(cl)

    def _update_brier_summary(self) -> None:
        mean_bs = self.engine.mean_brier_score()
        if mean_bs is None:
            self.brier_summary_label.setText("mean brier: n/a")
        else:
            self.brier_summary_label.setText(f"mean brier: {mean_bs:.4f}")

    def _toggle_ticker_scroll(self) -> None:
        self.marquee_widget.toggle_pause()
        self.ticker_pause_btn.setText(
            "resume scroll" if self.marquee_widget.is_paused else "pause scroll"
        )

    def _toggle_stream_pause(self) -> None:
        next_paused = not self.worker.is_paused
        self.worker.set_paused(next_paused)
        self.pause_btn.setText("resume poller" if next_paused else "pause poller")

    def _inject_custom_claim(self) -> None:
        txt = self.claim_input.text().strip()
        if not txt:
            return
        channel_name = self.channel_input.text().strip() or "Manual-Feed"
        item = BroadcastItem(
            item_id=f"manual_{uuid.uuid4().hex[:6]}",
            channel=channel_name,
            frequency_band=self.band_combo.currentText(),
            source_weight=self.weight_spin.value(),
            timestamp=datetime.now(timezone.utc),
            text=txt,
            transcription_confidence=1.0,
        )
        res = self.engine.ingest(item)
        self.on_item_evaluated(res)
        self.claim_input.clear()

    def _refresh_cluster_table(self, latest_res: EvaluationResult) -> None:
        with self.engine._state_lock:
            clusters = list(self.engine.clusters.values())
            now = latest_res.timestamp
            rows_data = []
            for cl in clusters:
                fc = cl.forecast
                br_str = f"{fc.base_rate:.0%}" if (fc is not None and fc.base_rate is not None) else "—"
                prob_val = fc.current_probability if fc is not None else None
                prob_str = f"{prob_val:.1%}" if prob_val is not None else "—"
                impact_str = f"{fc.impact_score:.2f}" if fc is not None else "0.50"

                sal = self.engine._compute_salience(cl, now)
                acc = self.engine._compute_accumulation(cl, now, prob_val)
                conf, prov = self.engine._compute_confidence(cl, sal)
                unexp = latest_res.unexpectedness if cl.cluster_id == latest_res.cluster_id else cl.last_unexpectedness
                pwr = latest_res.power_level if cl.cluster_id == latest_res.cluster_id else cl.last_power_level
                rows_data.append((cl.label, prov, br_str, prob_str, impact_str, sal, unexp, acc, conf, pwr))

        self.cluster_table.setRowCount(len(rows_data))
        for row, (label, prov, br_str, prob_str, impact_str, sal, unexp, acc, conf, pwr) in enumerate(rows_data):
            vals = [
                label,
                prov,
                br_str,
                prob_str,
                impact_str,
                f"{sal:.2f}",
                f"{unexp:.2f}",
                f"{acc:.2f}",
                f"{conf:.2f}",
                f"{pwr:.2f}",
            ]
            for col, val in enumerate(vals):
                cell = QTableWidgetItem(val)
                _make_read_only(cell)
                if col == 0:
                    cell.setForeground(QColor("#e8e8e8"))
                elif col == 1:
                    if "SURGING" in val or "CORROBORATED" in val:
                        cell.setForeground(QColor("#ddb52f"))
                    elif "DISPUTED" in val:
                        cell.setForeground(QColor("#a0a0a0"))
                    elif "OBSERVED" in val:
                        cell.setForeground(QColor("#e6b450"))
                    else:
                        cell.setForeground(QColor("#a0a0a0"))
                elif col == 3 and prob_str != "—":
                    cell.setForeground(QColor("#e6b450"))
                elif col == 9 and pwr >= self.engine.power_alert_threshold:
                    cell.setForeground(QColor("#ddb52f"))
                self.cluster_table.setItem(row, col, cell)

    def on_item_evaluated(self, res: EvaluationResult) -> None:
        self._eval_cache.insert(0, res)
        if len(self._eval_cache) > 250:
            self._eval_cache.pop()

        if res.alerted and res.ticker_line:
            self.marquee_widget.append_alert(
                res.ticker_line,
                power_level=res.power_level,
                is_surge="rapid_broadcast_surge" in res.trigger_reasons,
                created_at=res.timestamp,
            )

        self.eval_table.insertRow(0)
        status_text = "ALERT" if res.alerted else ("SHADOW" if self.engine.shadow_mode else "SUPPRESSED")
        row_vals = [
            res.timestamp.strftime("%H:%M:%S"),
            status_text,
            res.channel,
            res.cluster_label,
            "YES" if res.is_echo else "no",
            f"{res.power_level:.2f}",
            ", ".join(res.trigger_reasons) if res.trigger_reasons else "-",
        ]
        for col, val in enumerate(row_vals):
            cell = QTableWidgetItem(val)
            _make_read_only(cell)
            if res.alerted:
                cell.setBackground(QColor("#242424"))
                cell.setForeground(QColor("#ddb52f"))
            elif col == 1 and status_text == "SHADOW":
                cell.setForeground(QColor("#e6b450"))
            elif res.is_echo:
                cell.setForeground(QColor("#6a6a6a"))
            self.eval_table.setItem(0, col, cell)

        if self.eval_table.rowCount() > 250:
            self.eval_table.removeRow(self.eval_table.rowCount() - 1)

        self._refresh_cluster_table(res)
        self._update_brier_summary()
        self._render_detail(res)

    def _on_eval_selected(self) -> None:
        sel_model = self.eval_table.selectionModel()
        if sel_model is None:
            return
        rows = sel_model.selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        if 0 <= idx < len(self._eval_cache):
            self._render_detail(self._eval_cache[idx])

    def _render_detail(self, res: EvaluationResult) -> None:
        prior_s = f"{res.prior_prob:.3f}" if res.prior_prob is not None else "N/A"
        post_s = f"{res.posterior_prob:.3f}" if res.posterior_prob is not None else "N/A"
        excerpts_block = "\n".join(f"  • {e}" for e in res.supporting_excerpts) or "  (none)"

        with self.engine._state_lock:
            cl = self.engine.clusters.get(res.cluster_id)
            fc = cl.forecast if cl is not None else None
            route_now = self.engine.source_connector.last_active_route
            short_sum = build_cluster_short_summary(self.engine, cl) if cl is not None else "N/A"
            br_line = (
                f"{fc.base_rate:.1%} (wt={fc.base_rate_weight:.2f}) — {fc.reference_class_note}"
                if (fc is not None and fc.base_rate is not None)
                else "None (Fermi drivers only)"
            )
            brier_line = f"{fc.brier_score():.4f}" if (fc is not None and fc.brier_score() is not None) else "unresolved"

        lines = [
            "Cluster Summary:",
            f"  {short_sum}",
            "",
            f"Cluster:      {res.cluster_label} ({res.cluster_id}) [{res.domain_tag}/{res.regime_tag}]",
            f"Channel:      {res.channel} | Echo: {res.is_echo} | Provenance: {res.provenance_tag}",
            f"Source Route: {route_now}",
            f"Interest:     score={res.interest_score:.2f} (mult={res.interest_multiplier:.2f}x)",
            f"Base Rate:    {br_line}",
            f"Forecast P:   {prior_s} -> {post_s} | Brier: {brier_line}",
            f"Rule:         {res.resolution_rule or 'N/A'}",
            f"Signals:      S={res.salience:.2f}, U={res.unexpectedness:.2f}, A={res.accumulation:.2f}, C={res.confidence:.2f}",
            f"Power Level:  {res.power_level:.3f} | Alerted: {res.alerted}",
            f"Triggers:     {', '.join(res.trigger_reasons) if res.trigger_reasons else 'none'}",
            "",
            "Synthesized Headline:",
            f"  {res.synthesized_headline or '[None]'}",
            "",
            "Ticker Output:",
            f"  {res.ticker_line or '[Suppressed / Shadow Logged]'}",
            "",
            "Grounded Excerpts:",
            excerpts_block,
        ]
        self.detail_view.setPlainText("\n".join(lines))

    def closeEvent(self, a0: Optional[QCloseEvent]) -> None:
        self.worker.stop()
        self.worker.wait(3000)
        if self._warmup_thread is not None and self._warmup_thread.isRunning():
            self._warmup_thread.wait(2000)
        if self._poll_once_thread is not None and self._poll_once_thread.isRunning():
            self._poll_once_thread.wait(2000)
        for w in list(self._scrape_threads):
            if w.isRunning():
                w.wait(2000)
        self.marquee_widget.stop()
        self.engine.logger.close()
        if a0 is not None:
            a0.accept()


def build_production_engine() -> AmbientForecasterTicker:
    profile = InterestProfile(
        prompt="Consequential world, infrastructure, maritime, and geopolitical developments",
        focus_terms=[],
        in_interest_multiplier=1.30,
        out_of_interest_multiplier=0.75,
    )
    registry_state = load_source_registry(DEFAULT_REGISTRY_PATH)
    registry_state.auto_crawl_enabled = True
    registry_state.auto_approve_discovered = True
    registry_state.config.mode = normalize_route_mode(registry_state.config.mode)
    registry_state.config.enable_rss = True
    registry_state.config.allow_web_fallback = True
    return AmbientForecasterTicker(
        total_monitored_channels=4,
        power_alert_threshold=0.20,
        surge_salience_threshold=0.45,
        cooldown_sec=60.0,
        step_gain=0.80,
        interest_profile=profile,
        registry_state=registry_state,
        registry_path=DEFAULT_REGISTRY_PATH,
        shadow_mode=False,
        db_path="newsticker_shadow.sqlite",
    )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ticker_engine = build_production_engine()
    win = AmbientTickerWindow(ticker_engine)
    win.show()
    sys.exit(app.exec_())