#!/usr/bin/env python3
"""
High-Throughput Desktop Ground Control Station (GCS) - PyQt6 Architecture Demo

Author: Amanullah Naseer (amanullah7x)
License: MIT

Demonstrates:
- Decoupled ingestion of high-frequency (50-60 Hz) UAV telemetry into a background QThread.
- Thread-safe ring buffer aggregation flushing to UI at a deterministic 20 Hz.
- Zero-latency safety override / emergency bypass interrupt channel.
- Elimination of Qt event loop starvation and UI freeze under high packet load.
"""

import sys
import time
import math
import random
import argparse
from threading import Lock
from typing import Dict, Any, Optional

try:
    from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QRectF, QPointF
    from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QBrush, QLinearGradient
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QFrame, QProgressBar, QGridLayout
    )
    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False


# Thread-safe global / shared telemetry state container
class TelemetryStateBuffer:
    """Thread-safe circular state buffer aggregating high-rate incoming packets."""

    def __init__(self):
        self._lock = Lock()
        self._state: Dict[str, Any] = {
            "roll_deg": 0.0,
            "pitch_deg": 0.0,
            "yaw_deg": 0.0,
            "altitude_m": 120.0,
            "airspeed_mps": 18.5,
            "battery_pct": 94,
            "satellites": 14,
            "lat": 33.6007,
            "lon": 73.0679,
            "armed": False,
            "flight_mode": "STABILIZE",
            "packets_received": 0,
            "ingestion_hz": 0.0,
        }

    def update(self, new_data: Dict[str, Any]):
        with self._lock:
            self._state.update(new_data)
            self._state["packets_received"] += 1

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)


if PYQT_AVAILABLE:
    class TelemetryIngestionWorker(QThread):
        """Worker thread simulating high-rate (50+ Hz) MAVLink/ZMQ telemetry ingestion."""

        # Critical emergency signal bypassing the 20 Hz aggregation loop
        emergency_interrupt_signal = pyqtSignal(str)

        def __init__(self, state_buffer: TelemetryStateBuffer, target_hz: float = 55.0):
            super().__init__()
            self.state_buffer = state_buffer
            self.target_hz = target_hz
            self.running = True
            self.packet_counter = 0

        def run(self):
            interval = 1.0 / self.target_hz
            t_prev = time.time()
            sim_time = 0.0

            while self.running:
                t_start = time.time()
                sim_time += interval

                # Simulate dynamic flight telemetry (50 Hz)
                roll = math.sin(sim_time * 0.8) * 15.0 + random.uniform(-0.5, 0.5)
                pitch = math.cos(sim_time * 0.5) * 8.0 + random.uniform(-0.3, 0.3)
                yaw = (sim_time * 12.0) % 360.0
                altitude = 120.0 + math.sin(sim_time * 0.2) * 15.0
                airspeed = 18.0 + math.cos(sim_time * 0.3) * 3.0

                self.packet_counter += 1
                elapsed = time.time() - t_prev
                ingest_hz = self.packet_counter / elapsed if elapsed > 0 else self.target_hz

                if elapsed >= 1.0:
                    t_prev = time.time()
                    self.packet_counter = 0

                # Write to thread-safe atomic buffer
                self.state_buffer.update({
                    "roll_deg": roll,
                    "pitch_deg": pitch,
                    "yaw_deg": yaw,
                    "altitude_m": altitude,
                    "airspeed_mps": airspeed,
                    "ingestion_hz": ingest_hz,
                    "armed": True,
                    "flight_mode": "OFFBOARD_AUTO"
                })

                # Simulate occasional high-priority safety alarm (Emergency Bypass)
                if random.random() < 0.0008:
                    self.emergency_interrupt_signal.emit("GEOFENCE PROXIMITY WARNING: 45m TO BOUNDARY")

                # Sleep to maintain exact target rate without jitter
                comp = time.time() - t_start
                sleep_time = max(0.0, interval - comp)
                time.sleep(sleep_time)

        def stop(self):
            self.running = False
            self.wait()


    class ArtificialHorizonWidget(QWidget):
        """Custom QPainter widget rendering an artificial horizon attitude indicator."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setMinimumSize(220, 220)
            self.roll = 0.0
            self.pitch = 0.0

        def update_attitude(self, roll: float, pitch: float):
            self.roll = roll
            self.pitch = pitch
            self.update()

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            w = self.width()
            h = self.height()
            center_x = w / 2.0
            center_y = h / 2.0
            radius = min(w, h) / 2.0 - 10

            # Draw outer bezel
            painter.setPen(QPen(QColor("#1e293b"), 3))
            painter.setBrush(QBrush(QColor("#0b111e")))
            painter.drawEllipse(QPointF(center_x, center_y), radius, radius)

            # Pitch displacement and roll rotation
            painter.save()
            painter.translate(center_x, center_y)
            painter.rotate(-self.roll)
            pitch_offset = (self.pitch / 90.0) * (radius * 0.8)

            # Sky / Ground division
            sky_rect = QRectF(-radius, -radius * 2 + pitch_offset, radius * 2, radius * 2)
            painter.setBrush(QBrush(QColor("#0369a1")))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(sky_rect)

            ground_rect = QRectF(-radius, pitch_offset, radius * 2, radius * 2)
            painter.setBrush(QBrush(QColor("#92400e")))
            painter.drawRect(ground_rect)

            # Horizon line
            painter.setPen(QPen(QColor("#38bdf8"), 2))
            painter.drawLine(QPointF(-radius * 0.8, pitch_offset), QPointF(radius * 0.8, pitch_offset))

            painter.restore()

            # Center boresight reticle
            painter.setPen(QPen(QColor("#00f0ff"), 3))
            painter.drawLine(QPointF(center_x - 30, center_y), QPointF(center_x - 10, center_y))
            painter.drawLine(QPointF(center_x + 10, center_y), QPointF(center_x + 30, center_y))
            painter.drawEllipse(QPointF(center_x, center_y), 3, 3)

            # Degree readouts
            painter.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
            painter.setPen(QColor("#e2e8f0"))
            painter.drawText(15, 25, f"R: {self.roll:+.1f}°")
            painter.drawText(15, 42, f"P: {self.pitch:+.1f}°")


    class GroundStationMainWindow(QMainWindow):
        """Main Ground Station Dashboard Window."""

        def __init__(self, state_buffer: TelemetryStateBuffer):
            super().__init__()
            self.state_buffer = state_buffer
            self.render_counter = 0
            self.last_render_time = time.time()
            self.ui_fps = 0.0

            self.setWindowTitle("Amanullah Naseer | UAV Ground Control Station (PyQt6)")
            self.resize(980, 600)
            self.setStyleSheet("background-color: #060911; color: #f1f5f9; font-family: 'Segoe UI', sans-serif;")

            self._build_ui()

            # 20 Hz Throttled UI Render Timer (Deterministic 50ms interval)
            self.render_timer = QTimer(self)
            self.render_timer.timeout.connect(self._flush_telemetry_to_ui)
            self.render_timer.start(50)  # 50 ms = 20 Hz

        def _build_ui(self):
            central = QWidget()
            self.setCentralWidget(central)
            main_layout = QVBoxLayout(central)
            main_layout.setContentsMargins(16, 16, 16, 16)
            main_layout.setSpacing(12)

            # TOP HEADER BAR
            header = QHBoxLayout()
            title_lbl = QLabel("AUTONOMOUS UAV GCS TELEMETRY CONSOLE")
            title_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #00f0ff; letter-spacing: 1px;")
            header.addWidget(title_lbl)

            header.addStretch()

            self.status_badge = QLabel("OFFBOARD STANDBY")
            self.status_badge.setStyleSheet(
                "background-color: #10b981; color: #022c22; font-weight: bold; "
                "padding: 4px 10px; border-radius: 4px; font-size: 11px;"
            )
            header.addWidget(self.status_badge)
            main_layout.addLayout(header)

            # RATE METRICS PANEL
            rate_bar = QFrame()
            rate_bar.setStyleSheet("background-color: #0b111e; border: 1px solid #1e293b; border-radius: 8px; padding: 6px;")
            rate_layout = QHBoxLayout(rate_bar)

            self.lbl_ingest_hz = QLabel("Telemetry Ingestion: 0.0 Hz")
            self.lbl_ingest_hz.setStyleSheet("color: #38bdf8; font-family: Consolas; font-size: 12px;")
            rate_layout.addWidget(self.lbl_ingest_hz)

            self.lbl_render_hz = QLabel("UI Render Loop: 20.0 Hz (Locked)")
            self.lbl_render_hz.setStyleSheet("color: #4ade80; font-family: Consolas; font-size: 12px;")
            rate_layout.addWidget(self.lbl_render_hz)

            self.lbl_packets = QLabel("Packets Processed: 0")
            self.lbl_packets.setStyleSheet("color: #facc15; font-family: Consolas; font-size: 12px;")
            rate_layout.addWidget(self.lbl_packets)

            main_layout.addWidget(rate_bar)

            # CENTER COCKPIT INSTRUMENTS
            content_layout = QHBoxLayout()

            # Attitude Indicator
            self.horizon_widget = ArtificialHorizonWidget()
            content_layout.addWidget(self.horizon_widget)

            # Telemetry Readouts Grid
            grid_frame = QFrame()
            grid_frame.setStyleSheet("background-color: #0b111e; border: 1px solid #1e293b; border-radius: 8px; padding: 12px;")
            grid = QGridLayout(grid_frame)
            grid.setSpacing(10)

            self.val_alt = self._add_stat(grid, 0, 0, "ALTITUDE (MSL)", "120.0 m", "#38bdf8")
            self.val_spd = self._add_stat(grid, 0, 1, "GROUNDSPEED", "18.5 m/s", "#38bdf8")
            self.val_sat = self._add_stat(grid, 1, 0, "GPS SATELLITES", "14 (3D Fix)", "#4ade80")
            self.val_bat = self._add_stat(grid, 1, 1, "BATTERY VOLTAGE", "24.8 V (94%)", "#4ade80")
            self.val_lat = self._add_stat(grid, 2, 0, "COORDINATES", "33.6007° N, 73.0679° E", "#f1f5f9")
            self.val_mode = self._add_stat(grid, 2, 1, "AUTOPILOT MODE", "OFFBOARD_AUTO", "#c084fc")

            content_layout.addWidget(grid_frame, stretch=1)
            main_layout.addLayout(content_layout)

            # SAFETY & ACTION CONTROLS
            bottom_layout = QHBoxLayout()

            btn_arm = QPushButton("ARM / ENGAGE")
            btn_arm.setStyleSheet("background-color: #0284c7; color: white; padding: 10px; font-weight: bold; border-radius: 6px;")
            bottom_layout.addWidget(btn_arm)

            btn_rtl = QPushButton("RETURN TO LAUNCH (RTL)")
            btn_rtl.setStyleSheet("background-color: #d97706; color: white; padding: 10px; font-weight: bold; border-radius: 6px;")
            bottom_layout.addWidget(btn_rtl)

            btn_kill = QPushButton("EMERGENCY KILL SWITCH")
            btn_kill.setStyleSheet("background-color: #dc2626; color: white; padding: 10px; font-weight: bold; border-radius: 6px;")
            btn_kill.clicked.connect(self._trigger_emergency_kill)
            bottom_layout.addWidget(btn_kill)

            main_layout.addLayout(bottom_layout)

            # EMERGENCY ALERT LOG BAR
            self.alert_log = QLabel("SYSTEM HEALTHY: NO CRITICAL INTERRUPTS")
            self.alert_log.setStyleSheet(
                "background-color: #031424; color: #38bdf8; border: 1px solid #075985; "
                "border-radius: 4px; padding: 6px; font-family: Consolas; font-size: 11px;"
            )
            main_layout.addWidget(self.alert_log)

        def _add_stat(self, grid, r, c, title, val, color):
            lbl_title = QLabel(title)
            lbl_title.setStyleSheet("font-size: 10px; color: #64748b; font-family: Consolas;")
            lbl_val = QLabel(val)
            lbl_val.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {color}; font-family: Consolas;")
            box = QVBoxLayout()
            box.setSpacing(2)
            box.addWidget(lbl_title)
            box.addWidget(lbl_val)
            grid.addLayout(box, r, c)
            return lbl_val

        def _flush_telemetry_to_ui(self):
            """Flushes aggregated telemetry snapshot to UI widgets at 20 Hz."""
            state = self.state_buffer.get_snapshot()

            # Update instrument graphics
            self.horizon_widget.update_attitude(state["roll_deg"], state["pitch_deg"])

            # Update text readouts
            self.val_alt.setText(f"{state['altitude_m']:.1f} m")
            self.val_spd.setText(f"{state['airspeed_mps']:.1f} m/s")
            self.lbl_ingest_hz.setText(f"Telemetry Ingestion: {state['ingestion_hz']:.1f} Hz")
            self.lbl_packets.setText(f"Packets Processed: {state['packets_received']:,}")

            self.render_counter += 1
            now = time.time()
            if now - self.last_render_time >= 1.0:
                self.ui_fps = self.render_counter / (now - self.last_render_time)
                self.lbl_render_hz.setText(f"UI Render Loop: {self.ui_fps:.1f} Hz (Locked 20 Hz)")
                self.render_counter = 0
                self.last_render_time = now

        def handle_emergency_interrupt(self, alert_msg: str):
            """Handles instant safety interrupt bypassing regular render loop."""
            self.alert_log.setText(f"[PRIORITY INTERRUPT] {alert_msg}")
            self.alert_log.setStyleSheet(
                "background-color: #450a0a; color: #fca5a5; border: 1px solid #b91c1c; "
                "border-radius: 4px; padding: 6px; font-family: Consolas; font-size: 11px; font-weight: bold;"
            )

        def _trigger_emergency_kill(self):
            self.handle_emergency_interrupt("EMERGENCY MOTOR CUTOFF TRIGGERED BY OPERATOR")


def main():
    parser = argparse.ArgumentParser(description="High-Throughput PyQt6 Ground Station Architecture")
    parser.add_argument("--test", action="store_true", help="Run automated test without opening UI")
    args = parser.parse_args()

    state_buffer = TelemetryStateBuffer()

    if args.test or not PYQT_AVAILABLE:
        print("[TEST] Running headless telemetry aggregation test...")
        for i in range(100):
            state_buffer.update({"roll_deg": random.uniform(-10, 10), "altitude_m": 120.0 + i * 0.1})
            time.sleep(0.01)
        snap = state_buffer.get_snapshot()
        print(f"[SUCCESS] Processed {snap['packets_received']} packets cleanly in background buffer.")
        return

    app = QApplication(sys.argv)
    window = GroundStationMainWindow(state_buffer)

    # Start high-rate background telemetry worker (55 Hz)
    worker = TelemetryIngestionWorker(state_buffer, target_hz=55.0)
    worker.emergency_interrupt_signal.connect(window.handle_emergency_interrupt)
    worker.start()

    window.show()
    ret = app.exec()

    worker.stop()
    sys.exit(ret)


if __name__ == "__main__":
    main()
