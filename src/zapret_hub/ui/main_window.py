from __future__ import annotations

import ctypes
import time
import sys
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from zapret_hub import __version__
from zapret_hub.domain import ComponentDefinition, ComponentState
from PySide6.QtCore import QCoreApplication, QEasingCurve, QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal, QPropertyAnimation, QParallelAnimationGroup, Property, QByteArray
from PySide6.QtGui import QAction, QActionGroup, QColor, QCloseEvent, QIcon, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMenu,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
    QTextEdit,
    QInputDialog,
    QLayout,
    QProgressBar,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from zapret_hub.bootstrap import ApplicationContext
from zapret_hub.ui.theme import build_stylesheet, is_light_theme


@dataclass(slots=True)
class NavItem:
    key: str
    icon_file: str
    tooltip: str


@dataclass(slots=True)
class StatusBadge:
    key: str
    icon_file: str
    title: str
    title_label: QLabel
    icon_label: QLabel
    value_label: QLabel


class _UiSignals(QObject):
    toggle_done = Signal()
    component_action_done = Signal(str)
    general_test_progress = Signal(int, int, str)
    general_test_done = Signal(object)
    update_check_done = Signal(object, bool)
    update_prepare_done = Signal(object)
    page_payload_ready = Signal(str, object)


class SidebarPanel(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._border_color = QColor("#24304a")
        self._cut_size = 18
        self._highlight_rect = QRect(0, 0, 0, 0)
        self._highlight_fill = QColor(69, 81, 109, 72)
        self._highlight_border = QColor("#4f73b3")
        self._highlight_animation: QPropertyAnimation | None = None

    def set_theme(self, theme: str) -> None:
        light = is_light_theme(theme)
        if light:
            self._border_color = QColor("#d2ddeb")
            self._highlight_fill = QColor(191, 211, 243, 118)
            self._highlight_border = QColor("#9cb7ea")
        elif theme == "night":
            self._border_color = QColor("#24304a")
            self._highlight_fill = QColor(79, 115, 179, 68)
            self._highlight_border = QColor("#4f73b3")
        else:
            self._border_color = QColor("#2f333a")
            self._highlight_fill = QColor(96, 108, 124, 66)
            self._highlight_border = QColor("#717a87")
        self.update()

    def paintEvent(self, event: QEvent) -> None:
        super().paintEvent(event)
        if not self._highlight_rect.isNull():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(self._highlight_border, 1))
            painter.setBrush(self._highlight_fill)
            painter.drawRoundedRect(QRectF(self._highlight_rect), 12, 12)

    def _get_highlight_rect(self) -> QRect:
        return QRect(self._highlight_rect)

    def _set_highlight_rect(self, rect: QRect) -> None:
        self._highlight_rect = QRect(rect)
        self.update()

    highlightRect = Property(QRect, _get_highlight_rect, _set_highlight_rect)

    def move_highlight(self, rect: QRect, *, animated: bool = True) -> None:
        target = QRect(rect)
        if target.isNull():
            return
        if self._highlight_animation is not None:
            self._highlight_animation.stop()
        if not animated or self._highlight_rect.isNull():
            self._highlight_rect = target
            self.update()
            return
        animation = QPropertyAnimation(self, b"highlightRect", self)
        animation.setDuration(260)
        animation.setStartValue(self._highlight_rect)
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        animation.start()
        self._highlight_animation = animation

    def clear_highlight(self) -> None:
        if self._highlight_animation is not None:
            self._highlight_animation.stop()
            self._highlight_animation = None
        self._highlight_rect = QRect()
        self.update()


class AnimatedNavButton(QToolButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover_progress = 0.0
        self._icon_dx = 0.0
        self._icon_dy = 0.0
        self._icon_scale = 1.0
        self._glow_pos = QPointF(22.0, 22.0)
        self._light_theme = False
        self._theme_name = "night"
        self._anims: list[QPropertyAnimation] = []

    def set_nav_theme(self, theme: str) -> None:
        self._theme_name = theme
        self._light_theme = is_light_theme(theme)
        self.update()

    def _stop_anims(self) -> None:
        for anim in self._anims:
            anim.stop()
        self._anims.clear()

    def _animate_property(self, name: bytes, start: float, end: float, duration: int) -> None:
        animation = QPropertyAnimation(self, name, self)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setDuration(duration)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        animation.finished.connect(lambda: self._anims.remove(animation) if animation in self._anims else None)
        self._anims.append(animation)
        animation.start()

    def enterEvent(self, event: QEvent) -> None:
        self._animate_property(b"hoverProgress", self._hover_progress, 1.0, 220)
        self._animate_property(b"iconScale", self._icon_scale, 1.035, 240)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._animate_property(b"hoverProgress", self._hover_progress, 0.0, 220)
        self._animate_property(b"iconScale", self._icon_scale, 1.0, 220)
        self._animate_property(b"iconDx", self._icon_dx, 0.0, 180)
        self._animate_property(b"iconDy", self._icon_dy, 0.0, 180)
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        self._glow_pos = QPointF(pos.x(), pos.y())
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        dx = max(-1.0, min(1.0, (pos.x() - center.x()) / max(8.0, center.x())))
        dy = max(-1.0, min(1.0, (pos.y() - center.y()) / max(8.0, center.y())))
        self._icon_dx += (dx * 1.1 - self._icon_dx) * 0.18
        self._icon_dy += (dy * 1.1 - self._icon_dy) * 0.18
        self.update()
        super().mouseMoveEvent(event)

    def paintEvent(self, event: QEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = 12.0
        checked = self.isChecked()

        if self._light_theme:
            base_fill = QColor(181, 204, 242, 10)
            hover_fill = QColor(194, 214, 245, int(30 * self._hover_progress))
            checked_fill = QColor(0, 0, 0, 0)
            border = QColor(191, 210, 240, int(88 * self._hover_progress))
            glow_color = QColor(255, 255, 255, int(44 * self._hover_progress))
        elif self._theme_name == "night":
            base_fill = QColor(90, 112, 152, 8)
            hover_fill = QColor(95, 124, 177, int(26 * self._hover_progress))
            checked_fill = QColor(0, 0, 0, 0)
            border = QColor(102, 132, 191, int(84 * self._hover_progress))
            glow_color = QColor(126, 164, 255, int(58 * self._hover_progress))
        else:
            base_fill = QColor(126, 133, 145, 7)
            hover_fill = QColor(144, 151, 165, int(24 * self._hover_progress))
            checked_fill = QColor(0, 0, 0, 0)
            border = QColor(154, 162, 174, int(78 * self._hover_progress))
            glow_color = QColor(208, 216, 232, int(34 * self._hover_progress))

        fill = QColor(checked_fill if checked else base_fill)
        if not checked and self._hover_progress > 0:
            fill = hover_fill
        painter.setPen(QPen(border if (border.alpha() > 0 and not checked) else QColor(0, 0, 0, 0), 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, radius, radius)

        if self._hover_progress > 0:
            glow = QRadialGradient(self._glow_pos, max(self.width(), self.height()) * 0.75)
            glow.setColorAt(0.0, glow_color)
            glow.setColorAt(1.0, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawRoundedRect(rect, radius, radius)

        icon_size = max(20, round(26 * self._icon_scale))
        pixmap = self.icon().pixmap(icon_size, icon_size)
        target = QRectF(
            (self.width() - icon_size) / 2.0 + self._icon_dx,
            (self.height() - icon_size) / 2.0 + self._icon_dy,
            icon_size,
            icon_size,
        )
        painter.drawPixmap(target, pixmap, QRectF(0, 0, pixmap.width(), pixmap.height()))

    def _get_hover_progress(self) -> float:
        return self._hover_progress

    def _set_hover_progress(self, value: float) -> None:
        self._hover_progress = float(value)
        self.update()

    def _get_icon_dx(self) -> float:
        return self._icon_dx

    def _set_icon_dx(self, value: float) -> None:
        self._icon_dx = float(value)
        self.update()

    def _get_icon_dy(self) -> float:
        return self._icon_dy

    def _set_icon_dy(self, value: float) -> None:
        self._icon_dy = float(value)
        self.update()

    def _get_icon_scale(self) -> float:
        return self._icon_scale

    def _set_icon_scale(self, value: float) -> None:
        self._icon_scale = float(value)
        self.update()

    hoverProgress = Property(float, _get_hover_progress, _set_hover_progress)
    iconDx = Property(float, _get_icon_dx, _set_icon_dx)
    iconDy = Property(float, _get_icon_dy, _set_icon_dy)
    iconScale = Property(float, _get_icon_scale, _set_icon_scale)


class ClickSelectComboBox(QComboBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        view = self.view()
        if view is not None:
            view.viewport().installEventFilter(self)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()

    def showPopup(self) -> None:
        super().showPopup()
        view = self.view()
        if view is not None:
            view.viewport().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        view = self.view()
        if view is not None and watched is view.viewport() and event.type() == QEvent.Type.MouseButtonRelease:
            index = view.indexAt(event.pos())
            if index.isValid():
                self.setCurrentIndex(index.row())
                self.hidePopup()
                self.activated.emit(index.row())
                return True
        return super().eventFilter(watched, event)


class AnimatedPowerButton(QToolButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._light_theme = False
        self._theme_name = "night"
        self._active = False
        self._visual_mode = "off"
        self._visual_scale = 1.0
        self._hover_progress = 0.0
        self._glow_pos = QPointF(66.0, 66.0)
        self._wave_progress = 0.0
        self._wave_strength = 0.0
        self._wave_outward = True
        self._scale_anim: QPropertyAnimation | None = None
        self._hover_anim: QPropertyAnimation | None = None
        self._wave_progress_anim: QPropertyAnimation | None = None
        self._wave_strength_anim: QPropertyAnimation | None = None

    def set_power_theme(self, theme: str) -> None:
        self._theme_name = theme
        self._light_theme = is_light_theme(theme)
        self.update()

    def set_active_state(self, active: bool, *, animate: bool = True) -> None:
        self._active = active
        self._visual_mode = "on" if active else "off"
        target = 1.14 if active else 1.0
        if self._scale_anim is not None:
            self._scale_anim.stop()
        if not animate:
            self._visual_scale = target
            self.update()
            return
        anim = QPropertyAnimation(self, b"visualScale", self)
        anim.setDuration(220)
        anim.setStartValue(self._visual_scale)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.start()
        self._scale_anim = anim

    def set_loading_state(self, loading: bool, *, animate: bool = True) -> None:
        self._visual_mode = "loading" if loading else ("on" if self._active else "off")
        target = 1.06 if loading else (1.14 if self._active else 1.0)
        if self._scale_anim is not None:
            self._scale_anim.stop()
        if not animate:
            self._visual_scale = target
            self.update()
            return
        anim = QPropertyAnimation(self, b"visualScale", self)
        anim.setDuration(190)
        anim.setStartValue(self._visual_scale)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.start()
        self._scale_anim = anim

    def enterEvent(self, event: QEvent) -> None:
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._glow_pos = event.position()
        self.update()
        super().mouseMoveEvent(event)

    def _animate_hover(self, target: float) -> None:
        if self._hover_anim is not None:
            self._hover_anim.stop()
        anim = QPropertyAnimation(self, b"hoverProgress", self)
        anim.setDuration(240)
        anim.setStartValue(self._hover_progress)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.start()
        self._hover_anim = anim

    def play_wave(self, outward: bool) -> None:
        self._wave_outward = outward
        if self._wave_progress_anim is not None:
            self._wave_progress_anim.stop()
        if self._wave_strength_anim is not None:
            self._wave_strength_anim.stop()
        self._wave_progress = 0.0
        self._wave_strength = 0.22
        prog = QPropertyAnimation(self, b"waveProgress", self)
        prog.setDuration(560)
        prog.setStartValue(0.0)
        prog.setEndValue(1.0)
        prog.setEasingCurve(QEasingCurve.Type.OutCubic)
        strength = QPropertyAnimation(self, b"waveStrength", self)
        strength.setDuration(560)
        strength.setStartValue(0.24)
        strength.setEndValue(0.0)
        strength.setEasingCurve(QEasingCurve.Type.OutCubic)
        prog.start()
        strength.start()
        self._wave_progress_anim = prog
        self._wave_strength_anim = strength

    def paintEvent(self, event: QEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = QRectF(self.rect())
        center = rect.center()
        base_radius = min(rect.width(), rect.height()) * 0.39
        radius = base_radius * self._visual_scale

        if self._light_theme:
            off_top = QColor("#f7f9ff")
            off_bottom = QColor("#dfe8f7")
            off_border = QColor("#bfd2f0")
            on_top = QColor("#7b86ff")
            on_bottom = QColor("#4c58d8")
            on_border = QColor("#7b87ff")
            loading_top = QColor("#c7d3e6")
            loading_bottom = QColor("#9ba8bd")
            loading_border = QColor("#b9c6db")
        else:
            off_top = QColor("#5a5f67")
            off_bottom = QColor("#3c4148")
            off_border = QColor("#70757d")
            on_top = QColor("#7380ff")
            on_bottom = QColor("#4551cb")
            on_border = QColor("#7b87ff")
            loading_top = QColor("#707785")
            loading_bottom = QColor("#565d69")
            loading_border = QColor("#8b94a3")
            if self._theme_name == "night":
                off_top = QColor("#45506a")
                off_bottom = QColor("#313a4d")
                off_border = QColor("#56627d")
            elif self._theme_name == "oled":
                off_top = QColor("#2a2d33")
                off_bottom = QColor("#181b20")
                off_border = QColor("#3d424b")
                loading_top = QColor("#4f535b")
                loading_bottom = QColor("#353941")
                loading_border = QColor("#5b626d")

        gradient = QRadialGradient(center.x(), center.y() - radius * 0.36, radius * 1.3)
        if self._visual_mode == "loading":
            gradient.setColorAt(0.0, loading_top)
            gradient.setColorAt(1.0, loading_bottom)
            border = loading_border
        elif self._active:
            gradient.setColorAt(0.0, on_top)
            gradient.setColorAt(1.0, on_bottom)
            border = on_border
        else:
            gradient.setColorAt(0.0, off_top)
            gradient.setColorAt(1.0, off_bottom)
            border = off_border
        painter.setPen(QPen(border, 2))
        painter.setBrush(gradient)
        painter.drawEllipse(center, radius, radius)

        if self._hover_progress > 0.001:
            if self._light_theme:
                if self._active or self._visual_mode == "loading":
                    glow_color = QColor(232, 243, 255, int(62 * self._hover_progress))
                else:
                    glow_color = QColor(109, 154, 255, int(34 * self._hover_progress))
            else:
                glow_color = QColor(148, 206, 255, int(34 * self._hover_progress))
            dx = self._glow_pos.x() - center.x()
            dy = self._glow_pos.y() - center.y()
            distance = max(1.0, (dx * dx + dy * dy) ** 0.5)
            max_offset = radius * 0.34
            focus = QPointF(
                center.x() + dx / distance * min(distance, max_offset),
                center.y() + dy / distance * min(distance, max_offset),
            )
            button_path = QPainterPath()
            button_path.addEllipse(center, radius, radius)
            painter.save()
            painter.setClipPath(button_path)
            glow = QRadialGradient(focus, radius * (1.08 if self._light_theme else 0.98))
            glow.setColorAt(0.0, glow_color)
            glow.setColorAt(0.65, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), max(0, glow_color.alpha() // 2)))
            glow.setColorAt(1.0, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(center, radius, radius)
            painter.restore()

        icon_size = 48 if self._active else 44
        if self._visual_mode == "loading":
            icon_size = 46
        pixmap = self.icon().pixmap(icon_size, icon_size)
        target = QRectF(center.x() - icon_size / 2.0, center.y() - icon_size / 2.0, icon_size, icon_size)
        painter.drawPixmap(target, pixmap, QRectF(0, 0, pixmap.width(), pixmap.height()))

    def _get_visual_scale(self) -> float:
        return self._visual_scale

    def _set_visual_scale(self, value: float) -> None:
        self._visual_scale = float(value)
        self.update()

    def _get_wave_progress(self) -> float:
        return self._wave_progress

    def _set_wave_progress(self, value: float) -> None:
        self._wave_progress = float(value)
        self.update()

    def _get_wave_strength(self) -> float:
        return self._wave_strength

    def _set_wave_strength(self, value: float) -> None:
        self._wave_strength = float(value)
        self.update()

    def _get_hover_progress(self) -> float:
        return self._hover_progress

    def _set_hover_progress(self, value: float) -> None:
        self._hover_progress = float(value)
        self.update()

    visualScale = Property(float, _get_visual_scale, _set_visual_scale)
    waveProgress = Property(float, _get_wave_progress, _set_wave_progress)
    waveStrength = Property(float, _get_wave_strength, _set_wave_strength)
    hoverProgress = Property(float, _get_hover_progress, _set_hover_progress)


class PowerAuraWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._light_theme = False
        self._theme_name = "night"
        self._wave_progress = 0.0
        self._wave_strength = 0.0
        self._wave_outward = True
        self._center_point = QPointF()
        self._wave_base_radius = 74.0
        self._wave_travel_radius = 124.0
        self._idle_enabled = False
        self._idle_pulse_timer = QTimer(self)
        self._idle_pulse_timer.setInterval(1480)
        self._idle_pulse_timer.timeout.connect(self._play_idle_pulse)
        self._wave_progress_anim: QPropertyAnimation | None = None
        self._wave_strength_anim: QPropertyAnimation | None = None

    def set_power_theme(self, theme: str) -> None:
        self._theme_name = theme
        self._light_theme = is_light_theme(theme)
        self.update()

    def set_center_point(self, point: QPointF) -> None:
        self._center_point = QPointF(point)
        self.update()

    def set_idle_pulse_enabled(self, enabled: bool) -> None:
        self._idle_enabled = enabled
        if enabled:
            if not self._idle_pulse_timer.isActive():
                self._idle_pulse_timer.start()
            if self._wave_strength <= 0.02:
                self._play_idle_pulse()
        else:
            self._idle_pulse_timer.stop()

    def _play_idle_pulse(self) -> None:
        if not self._idle_enabled or self._wave_strength > 0.08:
            return
        self._play_wave_internal(outward=True, strength=0.30, duration=1450, base_radius=62.0, travel_radius=62.0)

    def _play_wave_internal(self, *, outward: bool, strength: float, duration: int, base_radius: float, travel_radius: float) -> None:
        self._wave_outward = outward
        if self._wave_progress_anim is not None:
            self._wave_progress_anim.stop()
        if self._wave_strength_anim is not None:
            self._wave_strength_anim.stop()
        self._wave_progress = 0.0
        self._wave_strength = strength
        self._wave_base_radius = base_radius
        self._wave_travel_radius = travel_radius
        prog = QPropertyAnimation(self, b"waveProgress", self)
        prog.setDuration(duration)
        prog.setStartValue(0.0)
        prog.setEndValue(1.0)
        prog.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade = QPropertyAnimation(self, b"waveStrength", self)
        fade.setDuration(duration)
        fade.setStartValue(strength)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        prog.start()
        fade.start()
        self._wave_progress_anim = prog
        self._wave_strength_anim = fade

    def play_wave(self, outward: bool) -> None:
        self._play_wave_internal(outward=outward, strength=0.48, duration=820, base_radius=74.0, travel_radius=118.0)

    def paintEvent(self, event: QEvent) -> None:
        if self._wave_strength <= 0.001:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0), 18.0, 18.0)
        painter.setClipPath(clip)
        center = self._center_point if not self._center_point.isNull() else QRectF(self.rect()).center()
        if self._theme_name == "oled":
            color = QColor(124, 134, 182, int(132 * self._wave_strength))
        elif self._light_theme:
            color = QColor(64, 116, 255, int(176 * self._wave_strength))
        else:
            color = QColor(122, 214, 255, int(168 * self._wave_strength))
        base = self._wave_base_radius
        travel = self._wave_travel_radius * (self._wave_progress if self._wave_outward else (1.0 - self._wave_progress))
        for factor, width, alpha_factor in ((1.0, 14.0, 1.0), (0.8, 9.0, 0.78), (0.62, 5.5, 0.52)):
            radius = base * factor + travel
            ring = QColor(color)
            ring.setAlpha(int(color.alpha() * alpha_factor))
            pen = QPen(ring, max(1.4, width * self._wave_strength))
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, radius, radius)

    def _get_wave_progress(self) -> float:
        return self._wave_progress

    def _set_wave_progress(self, value: float) -> None:
        self._wave_progress = float(value)
        self.update()

    def _get_wave_strength(self) -> float:
        return self._wave_strength

    def _set_wave_strength(self, value: float) -> None:
        self._wave_strength = float(value)
        self.update()

    waveProgress = Property(float, _get_wave_progress, _set_wave_progress)
    waveStrength = Property(float, _get_wave_strength, _set_wave_strength)


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, margin: int = 0, spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list[QWidgetItem] = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self.spacing()
            if line_height > 0 and next_x - self.spacing() > effective.right() + 1:
                x = effective.x()
                y += line_height + self.spacing()
                next_x = x + hint.width() + self.spacing()
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class ClickableCard(QFrame):
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("class", "fileModeCard")
        self.setProperty("hovered", False)

    def enterEvent(self, event: QEvent) -> None:
        self.setProperty("hovered", True)
        self.style().unpolish(self)
        self.style().polish(self)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.setProperty("hovered", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _disable_native_window_rounding(widget: QWidget) -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        hwnd = int(widget.winId())
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_DONOTROUND = 1
        value = ctypes.c_int(DWMWCP_DONOTROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
            ctypes.c_void_p(hwnd),
            ctypes.c_uint(DWMWA_WINDOW_CORNER_PREFERENCE),
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
    except Exception:
        return


def _bring_widget_to_front(widget: QWidget) -> None:
    widget.raise_()
    widget.activateWindow()
    if not sys.platform.startswith("win"):
        return
    try:
        hwnd = int(widget.winId())
        SW_RESTORE = 9
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)  # type: ignore[attr-defined]
        ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)  # type: ignore[attr-defined]
        ctypes.windll.user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)  # type: ignore[attr-defined]
        ctypes.windll.user32.SetForegroundWindow(hwnd)  # type: ignore[attr-defined]
    except Exception:
        return


class AppDialog(QDialog):
    def __init__(self, parent: QWidget, context: ApplicationContext, title: str) -> None:
        super().__init__(parent)
        self.context = context
        self._drag_pos: QPoint | None = None
        self.setObjectName("AppDialogWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlag(Qt.WindowType.Dialog, True)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        self.setModal(True)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        root = QFrame()
        root.setObjectName("DialogRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        title_bar = QFrame()
        title_bar.setObjectName("DialogTitleBar")
        title_bar.setFixedHeight(42)
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(10, 8, 10, 8)
        title_row.setSpacing(8)

        title_label = QLabel(title)
        title_label.setProperty("class", "title")
        title_row.addWidget(title_label)
        title_row.addStretch(1)

        close_btn = QToolButton()
        close_btn.setProperty("class", "window")
        close_btn.setProperty("role", "close")
        suffix = "light" if is_light_theme(context.settings.get().theme) else "dark"
        close_btn.setIcon(QIcon(str(context.paths.ui_assets_dir / "icons" / f"window_close_{suffix}.svg")))
        close_btn.setIconSize(QSize(14, 14))
        close_btn.clicked.connect(self.reject)
        title_row.addWidget(close_btn)

        root_layout.addWidget(title_bar)
        self.body = QWidget()
        self.body.setObjectName("DialogBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(14, 12, 14, 12)
        self.body_layout.setSpacing(10)
        root_layout.addWidget(self.body)
        shell.addWidget(root)
        _disable_native_window_rounding(self)

    def prepare_and_center(self) -> None:
        self.adjustSize()
        if self.parentWidget() is not None:
            parent_rect = self.parentWidget().frameGeometry()
            target = parent_rect.center() - self.rect().center()
            self.move(target)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= 42:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Print:
            event.ignore()
            return
        super().keyPressEvent(event)

    def showEvent(self, event: QEvent) -> None:
        _disable_native_window_rounding(self)
        super().showEvent(event)
        QTimer.singleShot(0, lambda: _bring_widget_to_front(self))


class SettingsDialog(AppDialog):
    def __init__(self, parent: QWidget, context: ApplicationContext) -> None:
        self.context = context
        super().__init__(parent, context, self._t("Настройки", "Settings"))
        self.setMinimumWidth(430)
        layout = self.body_layout

        form = QFormLayout()
        self.theme_combo = ClickSelectComboBox()
        for theme_id in ("night", "dark", "oled", "light", "light blue"):
            self.theme_combo.addItem(theme_id, theme_id)
        self.language_combo = ClickSelectComboBox()
        self.language_combo.addItems(["ru", "en"])
        self.tg_host_input = QLineEdit()
        self.tg_port_input = QLineEdit()
        self.tg_secret_input = QLineEdit()
        self.ipset_mode_combo = ClickSelectComboBox()
        self.ipset_mode_combo.addItem("loaded", "loaded")
        self.ipset_mode_combo.addItem("none", "none")
        self.ipset_mode_combo.addItem("any", "any")
        self.game_mode_combo = ClickSelectComboBox()
        self.game_mode_combo.addItem(self._t("как в конфиге", "from config"), "auto")
        self.game_mode_combo.addItem(self._t("выключен", "disabled"), "disabled")
        self.game_mode_combo.addItem(self._t("tcp + udp", "tcp + udp"), "all")
        self.game_mode_combo.addItem(self._t("только tcp", "tcp only"), "tcp")
        self.game_mode_combo.addItem(self._t("только udp", "udp only"), "udp")
        self.autostart_checkbox = QCheckBox(self._t("Запускать вместе с Windows", "Run with Windows"))
        self.tray_checkbox = QCheckBox(self._t("Стартовать в трее", "Start in tray"))
        self.auto_components_checkbox = QCheckBox(self._t("Автозапуск компонентов", "Auto-run components"))
        self.check_updates_checkbox = QCheckBox(self._t("Проверять наличие обновлений", "Check for updates"))
        form.addRow(self._t("Тема", "Theme"), self.theme_combo)
        form.addRow(self._t("Язык", "Language"), self.language_combo)
        form.addRow(self._t("Хост TG proxy", "TG proxy host"), self.tg_host_input)
        form.addRow(self._t("Порт TG proxy", "TG proxy port"), self.tg_port_input)
        form.addRow(self._t("Секрет TG proxy", "TG proxy secret"), self.tg_secret_input)
        form.addRow("IPSet mode", self.ipset_mode_combo)
        form.addRow(self._t("Gaming mode", "Gaming mode"), self.game_mode_combo)
        form.addRow("", self.autostart_checkbox)
        form.addRow("", self.tray_checkbox)
        form.addRow("", self.auto_components_checkbox)
        form.addRow("", self.check_updates_checkbox)
        layout.addLayout(form)

        credits = QLabel(
            self._t(
                "Credits: original zapret bundle and tg-ws-proxy by Flowseal.\n"
                "Original zapret ecosystem by bol-van.\n"
                f"This app is a separate management UI.\nVersion: {__version__}",
                "Credits: original zapret bundle and tg-ws-proxy by Flowseal.\n"
                "Original zapret ecosystem by bol-van.\n"
                f"This app is a separate management UI.\nVersion: {__version__}",
            )
        )
        credits.setProperty("class", "muted")
        layout.addWidget(credits)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton(self._t("Отмена", "Cancel"))
        save_btn = QPushButton(self._t("Сохранить", "Save"))
        save_btn.setProperty("class", "primary")
        cancel_btn.clicked.connect(self.reject)
        save_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)
        self._load()

    def _t(self, ru: str, en: str) -> str:
        return ru if self.context.settings.get().language == "ru" else en

    def _load(self) -> None:
        settings = self.context.settings.get()
        theme_index = self.theme_combo.findData(settings.theme)
        self.theme_combo.setCurrentIndex(theme_index if theme_index >= 0 else 0)
        self.language_combo.setCurrentText(settings.language)
        self.tg_host_input.setText(settings.tg_proxy_host)
        self.tg_port_input.setText(str(settings.tg_proxy_port))
        self.tg_secret_input.setText(settings.tg_proxy_secret)
        ipset_idx = self.ipset_mode_combo.findData(settings.zapret_ipset_mode)
        self.ipset_mode_combo.setCurrentIndex(ipset_idx if ipset_idx >= 0 else 0)
        game_idx = self.game_mode_combo.findData(settings.zapret_game_filter_mode)
        self.game_mode_combo.setCurrentIndex(game_idx if game_idx >= 0 else 0)
        self.autostart_checkbox.setChecked(self.context.autostart.is_enabled() or settings.autostart_windows)
        self.tray_checkbox.setChecked(settings.start_in_tray)
        self.auto_components_checkbox.setChecked(settings.auto_run_components)
        self.check_updates_checkbox.setChecked(settings.check_updates_on_start)

    def payload(self) -> dict[str, object]:
        try:
            tg_port = int(self.tg_port_input.text().strip() or "1443")
        except ValueError:
            tg_port = 1443
        return {
            "theme": self.theme_combo.currentData() or "night",
            "active_profile_id": self.context.settings.get().active_profile_id,
            "language": self.language_combo.currentText(),
            "mods_index_url": self.context.settings.get().mods_index_url,
            "tg_proxy_host": self.tg_host_input.text().strip() or "127.0.0.1",
            "tg_proxy_port": tg_port,
            "tg_proxy_secret": self.tg_secret_input.text().strip(),
            "zapret_ipset_mode": self.ipset_mode_combo.currentData() or "loaded",
            "zapret_game_filter_mode": self.game_mode_combo.currentData() or "auto",
            "autostart_windows": self.autostart_checkbox.isChecked(),
            "start_in_tray": self.tray_checkbox.isChecked(),
            "auto_run_components": self.auto_components_checkbox.isChecked(),
            "check_updates_on_start": self.check_updates_checkbox.isChecked(),
        }


class MainWindow(QMainWindow):
    def __init__(self, context: ApplicationContext, launch_hidden: bool = False) -> None:
        super().__init__()
        self.context = context
        self._launch_hidden = launch_hidden
        self._skip_next_show_focus = launch_hidden
        self._drag_pos: QPoint | None = None
        self._tray_notifications_shown = False
        self._force_exit = False
        self._shutdown_started = False
        self._nav_buttons: list[QToolButton] = []
        self._status_badges: dict[str, StatusBadge] = {}
        self._min_btn: QToolButton | None = None
        self._close_btn: QToolButton | None = None
        self._toggle_in_progress = False
        self._loading_frame = 0
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(220)
        self._loading_timer.timeout.connect(self._advance_loading_caption)
        self._component_loading_timer = QTimer(self)
        self._component_loading_timer.setInterval(200)
        self._component_loading_timer.timeout.connect(self._advance_component_loading)
        self._ui_signals = _UiSignals()
        self._ui_signals.toggle_done.connect(self._on_master_toggle_finished)
        self._ui_signals.component_action_done.connect(self._on_component_action_done)
        self._ui_signals.general_test_progress.connect(self._on_general_test_progress)
        self._ui_signals.general_test_done.connect(self._on_general_test_done)
        self._ui_signals.update_check_done.connect(self._on_update_check_done)
        self._ui_signals.update_prepare_done.connect(self._on_update_prepare_done)
        self._ui_signals.page_payload_ready.connect(self._on_page_payload_ready)
        self._updating_general_combo = False
        self._pending_info_message: tuple[str, str] | None = None
        self._components_cards_root: QWidget | None = None
        self._components_cards_layout: QHBoxLayout | None = None
        self._component_loading_buttons: dict[str, QPushButton] = {}
        self._component_loading_base_text: dict[str, str] = {}
        self._component_loading_frame = 0
        self._power_caption_base_text = "OFF"
        self._general_loading_combo: QComboBox | None = None
        self._general_loading_label: QLabel | None = None
        self._general_test_dialog: AppDialog | None = None
        self._general_test_status_label: QLabel | None = None
        self._general_test_eta_label: QLabel | None = None
        self._general_test_progress_bar: QProgressBar | None = None
        self._general_test_started_at = 0.0
        self._general_test_current_index = 0
        self._general_test_total = 0
        self._general_test_last_progress_at = 0.0
        self._general_test_running = False
        self._general_test_cancelled = False
        self._general_test_show_results = True
        self._general_test_auto_apply = False
        self._general_test_eta_timer = QTimer(self)
        self._general_test_eta_timer.setInterval(1000)
        self._general_test_eta_timer.timeout.connect(self._update_general_test_eta)
        self._general_test_task_id: str | None = None
        self._first_general_prompt: AppDialog | None = None
        self._loading_action = "connect"
        self._tools_btn: QToolButton | None = None
        self._settings_btn: QToolButton | None = None
        self._dashboard_title_label: QLabel | None = None
        self._components_title_label: QLabel | None = None
        self._mods_title_label: QLabel | None = None
        self._mods_subtitle_label: QLabel | None = None
        self._mods_add_btn: QPushButton | None = None
        self.power_aura: PowerAuraWidget | None = None
        self.power_caption_text: QLabel | None = None
        self.power_caption_dots: QLabel | None = None
        self._power_caption_dots_opacity: QGraphicsOpacityEffect | None = None
        self._power_caption_dots_blur: QGraphicsBlurEffect | None = None
        self._files_title_label: QLabel | None = None
        self._editor_title_label: QLabel | None = None
        self._logs_title_label: QLabel | None = None
        self._logs_refresh_btn: QPushButton | None = None
        self._tray_show_action: QAction | None = None
        self._tray_quit_action: QAction | None = None
        self._tray_toggle_action: QAction | None = None
        self._tray_general_menu: QMenu | None = None
        self._tray_general_action_group: QActionGroup | None = None
        self._update_check_in_progress = False
        self._update_prepare_dialog: AppDialog | None = None
        self._last_prompted_update_version = ""
        self._file_mode_stack: QStackedWidget | None = None
        self._file_home_page: QWidget | None = None
        self._file_tags_page: QWidget | None = None
        self._file_advanced_page: QWidget | None = None
        self._file_tag_title: QLabel | None = None
        self._file_tag_subtitle: QLabel | None = None
        self._file_tag_input: QLineEdit | None = None
        self._file_tag_canvas: QWidget | None = None
        self._file_tag_flow: FlowLayout | None = None
        self._files_intro_label: QLabel | None = None
        self._file_mode_cards: list[dict[str, object]] = []
        self._current_file_collection = "domains"
        self._favorite_general_buttons: dict[str, QToolButton] = {}
        self._general_options_cache: list[dict[str, str]] | None = None
        self._refresh_dirty_sections = {"dashboard", "components", "mods", "files", "logs", "tray"}
        self._refresh_scheduled = False
        self._initial_refresh_pending = False
        self._merge_ensure_in_progress = False
        self._page_refresh_in_progress: set[str] = set()
        self._page_payload_cache: dict[str, object] = {}
        self._settings_dialog: SettingsDialog | None = None
        self._settings_dialog_signature: tuple[str, str] | None = None
        self._loading_overlay_fade: QPropertyAnimation | None = None
        self._loading_overlay_context = ""
        self._current_file_values_cache: list[str] = []
        self._backend_tasks: dict[str, str] = {}
        self._component_defs_cache: dict[str, ComponentDefinition] = {}
        self._component_states_cache: dict[str, ComponentState] = {}
        self._page_blur_effect: QGraphicsBlurEffect | None = None
        self._page_opacity_effect: QGraphicsOpacityEffect | None = None
        self._page_transition_overlay: QWidget | None = None
        self._page_transition_overlay_label: QLabel | None = None
        self._page_transition_overlay_blur_effect: QGraphicsBlurEffect | None = None
        self._page_transition_overlay_opacity_effect: QGraphicsOpacityEffect | None = None
        self._pages_shell: QWidget | None = None
        self._page_transition_out: QPropertyAnimation | None = None
        self._page_transition_in: QPropertyAnimation | None = None
        self._page_transition_target = -1
        self._page_transition_running = False
        self._window_opacity_animation: QPropertyAnimation | None = None
        self._window_fade_pending_action: str | None = None
        self._nav_highlight_initialized = False

        self._icons_dir = self.context.paths.ui_assets_dir / "icons"
        self._icon_cache: dict[str, QIcon] = {}
        self._nav_items = [
            NavItem("home", "home.svg", self._t("Главная", "Dashboard")),
            NavItem("components", "components.svg", self._t("Компоненты", "Components")),
            NavItem("mods", "mods.svg", self._t("Модификации", "Mods")),
            NavItem("files", "files.svg", self._t("Файлы", "Files")),
            NavItem("logs", "logs.svg", self._t("Логи", "Logs")),
        ]

        self.setFixedSize(860, 520)
        self.setWindowTitle("Zapret Hub")
        self.setWindowIcon(self._icon("app.ico"))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        self._build_ui()
        self._setup_tray()
        self._apply_theme()
        self._sync_window_icon()
        self._prime_runtime_snapshot_cache()
        self.refresh_components()
        self.refresh_mods()
        if self.context.backend is not None:
            self.context.backend.task_finished.connect(self._on_backend_task_finished)
            self.context.backend.task_failed.connect(self._on_backend_task_failed)
            self.context.backend.task_progress.connect(self._on_backend_task_progress)
        self.schedule_refresh_all()
        if not self._launch_hidden:
            QTimer.singleShot(240, self._prime_cached_dialogs)
            QTimer.singleShot(800, self._maybe_run_first_general_autotest)
            QTimer.singleShot(1400, self._check_updates_on_start)
            QTimer.singleShot(0, lambda: _bring_widget_to_front(self))

    def _t(self, ru: str, en: str) -> str:
        return ru if self.context.settings.get().language == "ru" else en

    def _icon(self, filename: str) -> QIcon:
        cached = self._icon_cache.get(filename)
        if cached is not None:
            return cached
        icon_path = self._icons_dir / filename
        icon = QIcon(str(icon_path))
        self._icon_cache[filename] = icon
        return icon

    def _component_defs(self) -> dict[str, ComponentDefinition]:
        if self._component_defs_cache:
            return dict(self._component_defs_cache)
        return {component.id: component for component in self.context.processes.list_components()}

    def _component_states(self) -> dict[str, ComponentState]:
        if self._component_states_cache:
            return dict(self._component_states_cache)
        return {state.component_id: state for state in self.context.processes.list_states()}

    def _prime_runtime_snapshot_cache(self) -> None:
        try:
            self._component_defs_cache = {
                component.id: component for component in self.context.processes.list_components()
            }
        except Exception:
            self._component_defs_cache = {}
        try:
            self._component_states_cache = {
                state.component_id: state for state in self.context.processes.list_states()
            }
        except Exception:
            self._component_states_cache = {}

    def _update_runtime_snapshot_from_payload(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        component_items = payload.get("components")
        if isinstance(component_items, list):
            snapshot: dict[str, ComponentDefinition] = {}
            for item in component_items:
                if isinstance(item, dict) and item.get("id"):
                    try:
                        snapshot[str(item["id"])] = ComponentDefinition(**item)
                    except Exception:
                        continue
            if snapshot:
                self._component_defs_cache = snapshot
        state_items = payload.get("states")
        if isinstance(state_items, list):
            snapshot_states: dict[str, ComponentState] = {}
            for item in state_items:
                if isinstance(item, dict) and item.get("component_id"):
                    try:
                        snapshot_states[str(item["component_id"])] = ComponentState(**item)
                    except Exception:
                        continue
            if snapshot_states:
                self._component_states_cache = snapshot_states

    def showEvent(self, event: QEvent) -> None:
        super().showEvent(event)
        self._sync_window_icon()
        _disable_native_window_rounding(self)
        self._sync_nav_highlight(animated=self._nav_highlight_initialized)
        if not self._nav_highlight_initialized:
            self._nav_highlight_initialized = True
        self._animate_window_fade(showing=True)
        if self._skip_next_show_focus:
            self._skip_next_show_focus = False
            return
        QTimer.singleShot(0, lambda: _bring_widget_to_front(self))

    def _sync_window_icon(self) -> None:
        icon = self._icon("app.ico")
        self.setWindowIcon(icon)
        app = QCoreApplication.instance()
        if app is not None and hasattr(app, "setWindowIcon"):
            try:
                app.setWindowIcon(icon)  # type: ignore[attr-defined]
            except Exception:
                pass

    def _build_ui(self) -> None:
        shell = QWidget()
        shell.setObjectName("WindowShell")
        root = QVBoxLayout(shell)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(0)

        frame = QFrame()
        frame.setObjectName("RootFrame")
        root_frame = QVBoxLayout(frame)
        root_frame.setContentsMargins(0, 0, 0, 0)
        root_frame.setSpacing(0)

        title_bar = self._build_title_bar()
        root_frame.addWidget(title_bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root_frame.addLayout(body)

        body.addWidget(self._build_sidebar())
        body.addWidget(self._build_content(), 1)

        root.addWidget(frame)
        self.setCentralWidget(shell)
        self._build_loading_overlay(shell)

    def _build_loading_overlay(self, parent: QWidget) -> None:
        overlay = QFrame(parent)
        overlay.setObjectName("LoadingOverlay")
        overlay.hide()
        layout = QVBoxLayout(overlay)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addStretch(1)
        card = QFrame()
        card.setObjectName("LoadingCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(26, 24, 26, 24)
        card_layout.setSpacing(10)
        icon = QLabel()
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(self._icon("app.png").pixmap(58, 58))
        self._loading_overlay_title = QLabel(self._t("Запуск Zapret Hub", "Launching Zapret Hub"))
        self._loading_overlay_title.setProperty("class", "title")
        self._loading_overlay_label = QLabel(self._t("Загрузка...", "Loading..."))
        self._loading_overlay_label.setProperty("class", "muted")
        self._loading_overlay_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_overlay_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setProperty("class", "loadingLogo")
        card_layout.addWidget(icon)
        card_layout.addWidget(self._loading_overlay_title)
        card_layout.addWidget(self._loading_overlay_label)
        layout.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        self._loading_overlay = overlay
        self._reposition_loading_overlay()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_loading_overlay()
        self._reposition_page_transition_overlay()
        self._sync_power_aura_geometry()

    def _sync_power_aura_geometry(self) -> None:
        if self.power_aura is None or not hasattr(self, "_power_aura_host") or not hasattr(self, "power_button"):
            return
        aura_host = getattr(self, "_power_aura_host", None)
        power_button = getattr(self, "power_button", None)
        if aura_host is None or power_button is None:
            return
        self.power_aura.setGeometry(aura_host.rect())
        button_top_left = power_button.mapTo(aura_host, QPoint(0, 0))
        button_center = QPointF(
            float(button_top_left.x()) + power_button.width() / 2.0,
            float(button_top_left.y()) + power_button.height() / 2.0,
        )
        self.power_aura.set_center_point(button_center)

    def _reposition_loading_overlay(self) -> None:
        overlay = getattr(self, "_loading_overlay", None)
        central = self.centralWidget()
        if overlay is None or central is None:
            return
        overlay.setGeometry(0, 0, central.width(), central.height())

    def _reposition_page_transition_overlay(self) -> None:
        overlay = self._page_transition_overlay
        overlay_label = self._page_transition_overlay_label
        shell = self._pages_shell
        if overlay is None or overlay_label is None or shell is None:
            return
        overlay.setGeometry(shell.rect())
        overlay_label.setGeometry(overlay.rect())

    def _show_loading_overlay(self, text: str | None = None, *, title: str | None = None, context: str = "general") -> None:
        self._loading_overlay_context = context

    def _hide_loading_overlay(self) -> None:
        self._loading_overlay_context = ""

    def _build_title_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TitleBar")
        bar.setFixedHeight(52)
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 9, 12, 9)
        row.setSpacing(8)

        icon = QLabel()
        icon.setPixmap(self._icon("app.png").pixmap(20, 20))
        row.addWidget(icon)

        title = QLabel("Zapret Hub")
        title.setProperty("class", "title")
        row.addWidget(title)

        author = QLabel("by goshkow")
        author.setProperty("class", "muted")
        row.addWidget(author)
        row.addStretch(1)

        tools_btn = QToolButton()
        tools_btn.setProperty("class", "action")
        tools_btn.setIcon(self._icon("tool.svg"))
        tools_btn.setIconSize(QSize(16, 16))
        tools_btn.setToolTip(self._t("Инструменты", "Tools"))
        tools_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tools_btn.setMenu(self._build_tools_menu())
        self._attach_button_animations(tools_btn)
        self._tools_btn = tools_btn
        row.addWidget(tools_btn)

        settings_btn = QToolButton()
        settings_btn.setProperty("class", "action")
        settings_btn.setIcon(self._icon("settings.svg"))
        settings_btn.setIconSize(QSize(16, 16))
        settings_btn.setToolTip(self._t("Настройки", "Settings"))
        settings_btn.clicked.connect(self._open_settings_dialog)
        self._attach_button_animations(settings_btn)
        self._settings_btn = settings_btn
        row.addWidget(settings_btn)

        min_btn = self._window_btn("", "min")
        self._min_btn = min_btn
        min_btn.setIconSize(QSize(15, 15))
        min_btn.clicked.connect(self._minimize_window_native)
        self._attach_button_animations(min_btn)
        close_btn = self._window_btn("", "close")
        self._close_btn = close_btn
        close_btn.setIconSize(QSize(15, 15))
        close_btn.clicked.connect(self.close)
        self._attach_button_animations(close_btn)
        row.addWidget(min_btn)
        row.addWidget(close_btn)
        return bar

    def _window_btn(self, text: str, role: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setProperty("class", "window")
        btn.setProperty("role", role)
        return btn

    def _build_tools_menu(self) -> QMenu:
        menu = QMenu(self)
        run_tests = QAction(self._t("Проверить конфигурации", "Run general tests"), self)
        run_tests.triggered.connect(self._run_general_tests_popup)
        menu.addAction(run_tests)

        run_diag = QAction(self._t("Запустить диагностику", "Run diagnostics"), self)
        run_diag.triggered.connect(self._run_diagnostics_popup)
        menu.addAction(run_diag)

        check_updates = QAction(self._t("Проверить обновления", "Check updates"), self)
        check_updates.triggered.connect(self._check_updates_popup)
        menu.addAction(check_updates)

        rebuild = QAction(self._t("Пересобрать merged", "Rebuild merged"), self)
        rebuild.triggered.connect(self._rebuild_runtime)
        menu.addAction(rebuild)

        refresh = QAction(self._t("Обновить всё", "Refresh all"), self)
        refresh.triggered.connect(self.refresh_all)
        menu.addAction(refresh)
        return menu

    def _build_sidebar(self) -> QWidget:
        side = SidebarPanel()
        side.setObjectName("Sidebar")
        side.setFixedWidth(78)
        col = QVBoxLayout(side)
        col.setContentsMargins(12, 12, 12, 12)
        col.setSpacing(10)

        for idx, item in enumerate(self._nav_items):
            btn = AnimatedNavButton()
            btn.setProperty("class", "nav")
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setIcon(self._icon(item.icon_file))
            btn.setIconSize(QSize(26, 26))
            btn.setToolTip(item.tooltip)
            btn.clicked.connect(lambda _=False, index=idx: self._switch_page(index))
            self._attach_button_animations(btn)
            self._nav_buttons.append(btn)
            col.addWidget(btn)

        col.addStretch(1)
        if self._nav_buttons:
            self._nav_buttons[0].setChecked(True)
        QTimer.singleShot(0, lambda: self._sync_nav_highlight(animated=False))
        return side

    def _build_content(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("Content")
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        body = QFrame()
        body.setObjectName("ContentSurface")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 12, 12, 12)
        body_layout.setSpacing(8)

        pages_shell = QWidget()
        pages_shell.setObjectName("PagesShell")
        pages_shell.setProperty("class", "pageCanvas")
        pages_shell.setAutoFillBackground(False)
        pages_shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        pages_shell_layout = QVBoxLayout(pages_shell)
        pages_shell_layout.setContentsMargins(0, 0, 0, 0)
        pages_shell_layout.setSpacing(0)
        self._pages_shell = pages_shell

        pages_host = QWidget()
        pages_host.setObjectName("PagesHost")
        pages_host.setProperty("class", "pageCanvas")
        pages_host.setAutoFillBackground(False)
        pages_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        pages_host_layout = QVBoxLayout(pages_host)
        pages_host_layout.setContentsMargins(0, 0, 0, 0)
        pages_host_layout.setSpacing(0)

        self.pages = QStackedWidget()
        self.pages.setObjectName("PagesStack")
        self.pages.setProperty("class", "pageCanvas")
        self.pages.setAutoFillBackground(False)
        self.pages.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.pages.addWidget(self._build_dashboard_page())
        self.pages.addWidget(self._build_components_page())
        self.pages.addWidget(self._build_mods_page())
        self.pages.addWidget(self._build_files_page())
        self.pages.addWidget(self._build_logs_page())
        self._page_blur_effect = QGraphicsBlurEffect(self.pages)
        self._page_blur_effect.setBlurRadius(0.0)
        self.pages.setGraphicsEffect(self._page_blur_effect)
        pages_host_layout.addWidget(self.pages)
        pages_shell_layout.addWidget(pages_host)
        self._page_opacity_effect = QGraphicsOpacityEffect(pages_host)
        self._page_opacity_effect.setOpacity(1.0)
        pages_host.setGraphicsEffect(self._page_opacity_effect)
        overlay = QWidget(pages_shell)
        overlay.setObjectName("PageTransitionOverlay")
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        overlay.setStyleSheet("background: transparent;")
        overlay.hide()
        overlay_label = QLabel(overlay)
        overlay_label.setScaledContents(True)
        overlay_label.setStyleSheet("background: transparent;")
        overlay_label_blur = QGraphicsBlurEffect(overlay_label)
        overlay_label_blur.setBlurRadius(0.0)
        overlay_label.setGraphicsEffect(overlay_label_blur)
        overlay_opacity = QGraphicsOpacityEffect(overlay)
        overlay_opacity.setOpacity(1.0)
        overlay.setGraphicsEffect(overlay_opacity)
        self._page_transition_overlay = overlay
        self._page_transition_overlay_label = overlay_label
        self._page_transition_overlay_blur_effect = overlay_label_blur
        self._page_transition_overlay_opacity_effect = overlay_opacity
        self._reposition_page_transition_overlay()
        body_layout.addWidget(pages_shell)
        layout.addWidget(body, 1)
        return pane

    def _card(self) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setProperty("class", "card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 6, 14, 14)
        layout.setSpacing(10)
        return card, layout

    def _build_dashboard_page(self) -> QWidget:
        page = QWidget()
        page.setProperty("class", "pageRoot")
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        top, top_layout = self._card()
        top_layout.setContentsMargins(14, 14, 14, 14)
        title = QLabel(self._t("Быстрый доступ", "Quick Access"))
        title.setObjectName("DashboardTitle")
        title.setProperty("class", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        title.setContentsMargins(0, 0, 0, 0)
        title.setMaximumHeight(22)
        self._dashboard_title_label = title
        top_layout.addWidget(title)

        # настройка general перенесена в компоненты
        general_label = QLabel(self._t("Конфигурация", "General"))
        self.general_combo = ClickSelectComboBox()
        self.general_combo.currentIndexChanged.connect(self._on_general_selected)
        self.general_combo.hide()

        power_block = QWidget()
        power_block.setObjectName("DashboardPowerBlock")
        power_block_layout = QVBoxLayout(power_block)
        power_block_layout.setContentsMargins(0, 0, 18, 0)
        power_block_layout.setSpacing(8)

        self.power_aura = PowerAuraWidget(top)
        self.power_aura.set_power_theme(self.context.settings.get().theme)
        self.power_aura.lower()

        power_stage = QWidget(power_block)
        power_stage.setFixedSize(224, 188)
        power_stage.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        power_stage.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        power_stage.setStyleSheet("background: transparent;")
        self.power_button = AnimatedPowerButton(power_stage)
        self.power_button.setProperty("class", "power")
        self.power_button.setIcon(self._icon("power.svg"))
        self.power_button.setIconSize(QSize(42, 42))
        self.power_button.setGeometry(46, 28, 132, 132)
        self.power_button.clicked.connect(self._toggle_master_runtime)
        self._attach_button_animations(self.power_button)
        self.power_button.set_power_theme(self.context.settings.get().theme)

        self.power_caption = QWidget()
        self.power_caption.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.power_caption.setStyleSheet("background: transparent;")
        self.power_caption.setFixedWidth(power_stage.width())
        caption_layout = QHBoxLayout(self.power_caption)
        caption_layout.setContentsMargins(0, 0, 0, 0)
        caption_layout.setSpacing(0)
        self.power_caption_text = QLabel("OFF")
        self.power_caption_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.power_caption_text.setProperty("class", "title")
        self.power_caption_dots = None
        self._power_caption_dots_blur = None
        self._power_caption_dots_opacity = None
        caption_layout.addWidget(self.power_caption_text, 1, Qt.AlignmentFlag.AlignCenter)
        power_block_layout.addWidget(power_stage, 0, Qt.AlignmentFlag.AlignHCenter)
        power_block_layout.addWidget(self.power_caption, 0, Qt.AlignmentFlag.AlignHCenter)
        self._power_aura_host = top
        self._power_block = power_block
        self._power_stage = power_stage
        QTimer.singleShot(0, self._sync_power_aura_geometry)

        top_layout.addStretch(1)
        top_layout.addWidget(power_block, 0, Qt.AlignmentFlag.AlignHCenter)
        top_layout.addStretch(1)

        badges_row = QHBoxLayout()
        badges_row.setSpacing(10)
        for key, icon_name, title_text in [
            ("app", "status_ok.svg", self._t("Приложение", "App")),
            ("zapret", "status_warn.svg", "Zapret"),
            ("tg", "status_warn.svg", "TG Proxy"),
            ("mods", "status_mod.svg", "Mods"),
            ("theme", "status_theme.svg", self._t("Тема", "Theme")),
        ]:
            badge = self._build_status_badge(key, icon_name, title_text)
            badges_row.addWidget(badge)
        badges_row.setStretch(0, 1)
        badges_row.setStretch(1, 1)
        badges_row.setStretch(2, 1)
        badges_row.setStretch(3, 1)
        badges_row.setStretch(4, 1)
        top_layout.addLayout(badges_row)
        root.addWidget(top)

        return page

    def _build_status_badge(self, key: str, icon_name: str, title: str) -> QWidget:
        card, layout = self._card()
        card.setMinimumHeight(96)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        head = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(self._icon(icon_name).pixmap(18, 18))
        text_label = QLabel(title)
        text_label.setProperty("class", "muted")
        head.addWidget(icon_label)
        head.addWidget(text_label)
        head.addStretch(1)
        layout.addLayout(head)

        value = QLabel("...")
        value.setProperty("class", "title")
        value.setWordWrap(False)
        layout.addWidget(value)
        self._status_badges[key] = StatusBadge(key, icon_name, title, text_label, icon_label, value)
        return card

    def _build_components_page(self) -> QWidget:
        page = QWidget()
        page.setProperty("class", "pageRoot")
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        label = QLabel(self._t("Компоненты", "Components"))
        label.setProperty("class", "title")
        self._components_title_label = label
        root.addWidget(label)

        self.components_list = QListWidget()
        self.components_list.setObjectName("ComponentList")
        self.components_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.components_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.components_list.setSpacing(8)
        self.components_list.hide()
        root.addWidget(self.components_list)
        self._components_cards_root = QWidget()
        self._components_cards_root.setProperty("class", "pageCanvas")
        self._components_cards_layout = QHBoxLayout(self._components_cards_root)
        self._components_cards_layout.setContentsMargins(0, 0, 0, 0)
        self._components_cards_layout.setSpacing(12)
        root.addWidget(self._components_cards_root, 1)
        return page

    def _build_mods_page(self) -> QWidget:
        page = QWidget()
        page.setProperty("class", "pageRoot")
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        hero, hero_layout = self._card()
        hero.setProperty("class", "modHero")

        hero_top = QHBoxLayout()
        hero_top.setContentsMargins(0, 0, 0, 0)
        hero_top.setSpacing(10)

        title_wrap = QVBoxLayout()
        title_wrap.setContentsMargins(0, 0, 0, 0)
        title_wrap.setSpacing(4)
        label = QLabel(self._t("Модификации", "Mods"))
        label.setProperty("class", "title")
        self._mods_title_label = label
        subtitle = QLabel(
            self._t(
                "Здесь можно аккуратно подключать свои сборки, не ломая базовую конфигурацию.",
                "This is where you can attach your own packs without touching the base configuration.",
            )
        )
        subtitle.setProperty("class", "muted")
        subtitle.setWordWrap(True)
        self._mods_subtitle_label = subtitle
        title_wrap.addWidget(label)
        title_wrap.addWidget(subtitle)
        hero_top.addLayout(title_wrap, 1)

        import_btn = QPushButton(self._t("Добавить", "Add"))
        import_btn.setProperty("class", "primary")
        import_btn.setIcon(self._icon("plus.svg"))
        import_btn.setIconSize(QSize(14, 14))
        import_btn.setMinimumHeight(38)
        import_btn.clicked.connect(self._import_mod_any)
        self._attach_button_animations(import_btn)
        self._mods_add_btn = import_btn
        hero_top.addWidget(import_btn)
        hero_layout.addLayout(hero_top)

        summary_row = QHBoxLayout()
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.setSpacing(10)

        self.mods_summary_chip = QLabel()
        self.mods_summary_chip.setObjectName("ModsSummaryChip")
        self.mods_summary_chip.setProperty("class", "modMeta")
        summary_row.addWidget(self.mods_summary_chip)

        self.mods_enabled_chip = QLabel()
        self.mods_enabled_chip.setObjectName("ModsEnabledChip")
        self.mods_enabled_chip.setProperty("class", "modMeta")
        summary_row.addWidget(self.mods_enabled_chip)

        self.mods_import_hint = QLabel(
            self._t(
                "Можно добавить папку, ZIP, отдельные файлы или целый GitHub-репозиторий. Приложение само заберет только совместимые файлы.",
                "You can add a folder, ZIP, selected files, or a full GitHub repository. The app will keep only compatible files.",
            )
        )
        self.mods_import_hint.setProperty("class", "modHint")
        self.mods_import_hint.setWordWrap(True)
        summary_row.addWidget(self.mods_import_hint, 1)
        hero_layout.addLayout(summary_row)
        root.addWidget(hero)

        self.mods_scroll = QScrollArea()
        self.mods_scroll.setObjectName("ModsScroll")
        self.mods_scroll.setWidgetResizable(True)
        self.mods_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.mods_canvas = QWidget()
        self.mods_canvas.setObjectName("ModsCanvas")
        self.mods_canvas.setProperty("class", "pageCanvas")
        self.mods_cards_layout = QVBoxLayout(self.mods_canvas)
        self.mods_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.mods_cards_layout.setSpacing(12)
        self.mods_scroll.setWidget(self.mods_canvas)
        root.addWidget(self.mods_scroll, 1)
        return page

    def _build_files_page(self) -> QWidget:
        page = QWidget()
        page.setProperty("class", "pageRoot")
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        title = QLabel(self._t("Файлы", "Files"))
        title.setProperty("class", "title")
        self._files_title_label = title
        root.addWidget(title)

        stack = QStackedWidget()
        self._file_mode_stack = stack

        chooser, chooser_layout = self._card()
        self._file_home_page = chooser
        chooser_layout.setContentsMargins(14, 10, 14, 14)
        chooser_layout.setSpacing(8)
        intro = QLabel(
            self._t(
                "Выберите удобный режим: быстрый список доменов, IP-адреса или полноценное редактирование файлов.",
                "Choose the mode you need: quick domain/IP editing or full file editing.",
            )
        )
        intro.setWordWrap(True)
        self._files_intro_label = intro
        chooser_layout.addWidget(intro)
        chooser_grid = QGridLayout()
        chooser_grid.setContentsMargins(0, 2, 0, 0)
        chooser_grid.setHorizontalSpacing(12)
        chooser_grid.setVerticalSpacing(12)
        chooser_layout.addLayout(chooser_grid, 1)
        file_modes = [
            (
                self._t("Домены", "Domains"),
                self._t("Добавляйте сервисы, которые нужно направить в общий список обхода.", "Add services that should be placed into the general bypass list."),
                "domains",
                "files_domains.svg",
            ),
            (
                self._t("Исключения", "Exclude domains"),
                self._t("Отдельный список доменов, которые нужно исключить из правил.", "A separate list of domains that should be excluded from rules."),
                "exclude_domains",
                "files_exclude.svg",
            ),
            (
                self._t("IP-адреса", "IP addresses"),
                self._t("Ручной список IP и подсетей, которые нужно исключить из IPSet.", "A manual list of IPs and subnets to exclude from IPSet."),
                "ips",
                "files_ip.svg",
            ),
            (
                self._t("Редактирование файлов", "Advanced editor"),
                self._t("Открыть полноценный список файлов и текстовый редактор.", "Open the full file list and the text editor."),
                "advanced",
                "files_editor.svg",
            ),
        ]
        self._file_mode_cards = []
        for index, (label, description, kind, icon_name) in enumerate(file_modes):
            card = ClickableCard()
            card.setMinimumHeight(126)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 12, 16, 12)
            card_layout.setSpacing(8)
            card_layout.addStretch(1)

            icon_label = QLabel()
            icon_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            icon_label.setPixmap(self._icon(icon_name).pixmap(28, 28))
            icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            card_layout.addWidget(icon_label)

            title_label = QLabel(label)
            title_label.setProperty("class", "title")
            title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            card_layout.addWidget(title_label)

            desc_label = QLabel(description)
            desc_label.setProperty("class", "muted")
            desc_label.setWordWrap(True)
            desc_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            desc_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            card_layout.addWidget(desc_label)
            card_layout.addStretch(1)

            card.clicked.connect(lambda target=kind: self._open_files_mode(target))
            chooser_grid.addWidget(card, index // 2, index % 2)
            self._file_mode_cards.append(
                {
                    "kind": kind,
                    "title": title_label,
                    "description": desc_label,
                }
            )
        chooser_grid.setColumnStretch(0, 1)
        chooser_grid.setColumnStretch(1, 1)

        tags_page, tags_layout = self._card()
        self._file_tags_page = tags_page
        back_row = QHBoxLayout()
        back_btn = QToolButton()
        back_btn.setProperty("class", "action")
        back_btn.setIcon(self._icon("back.svg"))
        back_btn.setIconSize(QSize(16, 16))
        back_btn.setToolTip(self._t("Назад", "Back"))
        back_btn.clicked.connect(lambda: self._open_files_mode("home"))
        back_row.addWidget(back_btn, 0)
        back_row.addStretch(1)
        tags_layout.addLayout(back_row)
        tag_title = QLabel()
        tag_title.setProperty("class", "title")
        self._file_tag_title = tag_title
        tags_layout.addWidget(tag_title)
        tag_subtitle = QLabel()
        tag_subtitle.setProperty("class", "muted")
        tag_subtitle.setWordWrap(True)
        self._file_tag_subtitle = tag_subtitle
        tags_layout.addWidget(tag_subtitle)
        tag_input = QLineEdit()
        tag_input.setPlaceholderText(self._t("Введите домен или IP и нажмите Enter", "Type a domain or IP and press Enter"))
        tag_input.returnPressed.connect(self._commit_tag_input)
        tag_input.installEventFilter(self)
        self._file_tag_input = tag_input
        tags_layout.addWidget(tag_input)
        tag_scroll = QScrollArea()
        tag_scroll.setWidgetResizable(True)
        tag_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tag_canvas = QWidget()
        tag_flow = FlowLayout(tag_canvas, margin=0, spacing=8)
        tag_canvas.setLayout(tag_flow)
        tag_scroll.setWidget(tag_canvas)
        self._file_tag_canvas = tag_canvas
        self._file_tag_flow = tag_flow
        tags_layout.addWidget(tag_scroll, 1)
        advanced_btn = QPushButton(self._t("Открыть редактор файлов", "Open file editor"))
        advanced_btn.clicked.connect(lambda: self._open_files_mode("advanced"))
        tags_layout.addWidget(advanced_btn)

        advanced_page = QWidget()
        self._file_advanced_page = advanced_page
        advanced_root = QVBoxLayout(advanced_page)
        advanced_root.setContentsMargins(0, 0, 0, 0)
        advanced_root.setSpacing(12)
        advanced_top = QHBoxLayout()
        advanced_back = QToolButton()
        advanced_back.setProperty("class", "action")
        advanced_back.setIcon(self._icon("back.svg"))
        advanced_back.setIconSize(QSize(16, 16))
        advanced_back.setToolTip(self._t("Назад", "Back"))
        advanced_back.clicked.connect(lambda: self._open_files_mode("home"))
        advanced_top.addWidget(advanced_back, 0)
        advanced_top.addStretch(1)
        advanced_root.addLayout(advanced_top)
        advanced_split = QHBoxLayout()
        advanced_split.setContentsMargins(0, 0, 0, 0)
        advanced_split.setSpacing(12)

        left, left_layout = self._card()
        left_title = QLabel(self._t("Список файлов", "Files list"))
        left_title.setProperty("class", "title")
        left_layout.addWidget(left_title)
        self.files_list = QListWidget()
        self.files_list.setObjectName("FilesList")
        self.files_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.files_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.files_list.setSpacing(8)
        self.files_list.currentItemChanged.connect(self._load_selected_file)
        left_layout.addWidget(self.files_list)
        advanced_split.addWidget(left, 1)

        right, right_layout = self._card()
        right_title = QLabel(self._t("Редактор", "Editor"))
        right_title.setProperty("class", "title")
        self._editor_title_label = right_title
        right_layout.addWidget(right_title)
        self.file_path_label = QLabel(self._t("Выберите файл", "Select a file"))
        self.file_path_label.setProperty("class", "muted")
        path_row = QHBoxLayout()
        path_row.addWidget(self.file_path_label, 1)
        self.rename_file_btn = QToolButton()
        self.rename_file_btn.setProperty("class", "action")
        self.rename_file_btn.setIcon(self._icon("edit.svg"))
        self.rename_file_btn.setToolTip(self._t("Переименовать выбранный файл", "Rename selected file"))
        self.rename_file_btn.clicked.connect(self._rename_current_file)
        self._attach_button_animations(self.rename_file_btn)
        path_row.addWidget(self.rename_file_btn)
        right_layout.addLayout(path_row)
        self.file_editor = QTextEdit()
        self.file_editor.setObjectName("FileEditor")
        right_layout.addWidget(self.file_editor, 1)
        save_btn = QPushButton(self._t("Сохранить файл", "Save file"))
        save_btn.clicked.connect(self._save_current_file)
        self._attach_button_animations(save_btn)
        right_layout.addWidget(save_btn)
        advanced_split.addWidget(right, 2)
        advanced_root.addLayout(advanced_split, 1)

        stack.addWidget(chooser)
        stack.addWidget(tags_page)
        stack.addWidget(advanced_page)
        root.addWidget(stack, 1)
        return page

    def _build_logs_page(self) -> QWidget:
        page = QWidget()
        page.setProperty("class", "pageRoot")
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        top = QHBoxLayout()
        label = QLabel(self._t("Логи", "Logs"))
        label.setProperty("class", "title")
        self._logs_title_label = label
        top.addWidget(label)
        top.addStretch(1)
        refresh_btn = QPushButton(self._t("Обновить", "Refresh"))
        refresh_btn.clicked.connect(self.refresh_logs)
        self._attach_button_animations(refresh_btn)
        self._logs_refresh_btn = refresh_btn
        top.addWidget(refresh_btn)
        root.addLayout(top)
        self.logs_text = QTextEdit()
        self.logs_text.setReadOnly(True)
        root.addWidget(self.logs_text)
        return page

    def _setup_tray(self) -> None:
        self.tray_icon = QSystemTrayIcon(self._icon("app.ico"), self)
        menu = QMenu(self)
        show_action = QAction(self._t("Открыть", "Open"), self)
        toggle_action = QAction(self._t("Компоненты", "Components"), self)
        general_menu = QMenu(self._t("Конфигурация Zapret", "Zapret configuration"), self)
        quit_action = QAction(self._t("Выход", "Exit"), self)
        show_action.triggered.connect(self._restore_from_tray)
        toggle_action.triggered.connect(self._tray_toggle_master_runtime)
        quit_action.triggered.connect(self._exit_application)
        self._tray_show_action = show_action
        self._tray_toggle_action = toggle_action
        self._tray_general_menu = general_menu
        self._tray_quit_action = quit_action
        menu.addAction(show_action)
        menu.addAction(toggle_action)
        menu.addMenu(general_menu)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.setToolTip("Zapret Hub")
        self.tray_icon.show()
        self._rebuild_tray_menu()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= 48:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Print:
            event.ignore()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._window_fade_pending_action is not None:
            event.ignore()
            return
        if not self._force_exit:
            if self._should_minimize_to_tray():
                event.ignore()
                self._animate_window_fade(showing=False, action="tray")
                return
            self._force_exit = True
            event.ignore()
            self._animate_window_fade(showing=False, action="exit")
            return
        event.accept()
        super().closeEvent(event)

    def _restore_from_tray(self) -> None:
        self._sync_window_icon()
        self.showNormal()
        self._mark_dirty("dashboard", "components", "mods", "files", "logs", "tray")
        _bring_widget_to_front(self)

    def _tray_toggle_master_runtime(self) -> None:
        if self._toggle_in_progress:
            return
        self._toggle_master_runtime()

    def start_enabled_components_async(self) -> None:
        if self._toggle_in_progress:
            return
        self._loading_action = "connect"
        self._toggle_in_progress = True
        self._loading_timer.start()
        self._advance_loading_caption()
        self._submit_backend_task("start_enabled_components")

    def _tray_select_general(self, general_id: str) -> None:
        if not general_id:
            return
        current = self.context.settings.get().selected_zapret_general
        if general_id == current:
            return
        self.context.settings.get().selected_zapret_general = general_id
        states = self._component_states()
        if states.get("zapret") and states["zapret"].status == "running":
            self._toggle_in_progress = True
            self._loading_action = "connect"
            self._loading_timer.start()
            self._advance_loading_caption()
            self._submit_backend_task("select_general", {"selected": general_id})
        else:
            self._submit_backend_task("select_general", {"selected": general_id})
            self.refresh_all()

    def restore_from_external_launch(self) -> None:
        self._restore_from_tray()

    def _exit_application(self) -> None:
        self._force_exit = True
        self._animate_window_fade(showing=False, action="exit")

    def _finalize_exit(self) -> None:
        self._shutdown_runtime()
        app = QCoreApplication.instance()
        if app is not None:
            app.quit()

    def _shutdown_runtime(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self._loading_timer.stop()
        self._component_loading_timer.stop()
        self._general_test_eta_timer.stop()
        self._general_test_running = False
        try:
            self.context.processes.stop_all()
        except Exception:
            pass
        if hasattr(self, "tray_icon") and self.tray_icon is not None:
            try:
                self.tray_icon.hide()
                self.tray_icon.setContextMenu(None)
                self.tray_icon.deleteLater()
            except Exception:
                pass

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._restore_from_tray()

    def _rebuild_tray_menu(self) -> None:
        if self._tray_general_menu is None:
            return
        self._tray_general_menu.clear()
        group = QActionGroup(self)
        group.setExclusive(True)
        selected = self.context.settings.get().selected_zapret_general
        for option in self._sorted_general_options():
            action = QAction(self._format_general_option_label(option), self)
            action.setCheckable(True)
            action.setChecked(option["id"] == selected)
            action.triggered.connect(lambda _=False, gid=option["id"]: self._tray_select_general(gid))
            group.addAction(action)
            self._tray_general_menu.addAction(action)
        self._tray_general_action_group = group
        states = self._component_states()
        active_ids = self._master_active_components()
        running_ids = {cid for cid in active_ids if states.get(cid) and states[cid].status == "running"}
        if self._tray_toggle_action is not None:
            fully_running = bool(active_ids) and running_ids == set(active_ids)
            partially_running = bool(running_ids) and not fully_running
            if fully_running:
                icon_name = "status_ok.svg"
                state_text = self._t("Включены", "Enabled")
            elif partially_running:
                icon_name = "status_warn.svg"
                state_text = self._t("Частично", "Partial")
            else:
                icon_name = "status_off.svg"
                state_text = self._t("Выключены", "Disabled")
            self._tray_toggle_action.setIcon(self._icon(icon_name))
            self._tray_toggle_action.setText(f"{self._t('Компоненты', 'Components')}: {state_text}")

    def _should_minimize_to_tray(self) -> bool:
        # в трей уходим только когда реально есть активный runtime
        try:
            states = self._component_states()
        except Exception:
            return False
        for component_id in ("zapret", "tg-ws-proxy"):
            state = states.get(component_id)
            if state and state.status == "running":
                return True
        return False

    def _attach_button_animations(self, widget: QWidget) -> None:
        if isinstance(widget, AnimatedNavButton):
            widget.set_nav_theme(self.context.settings.get().theme)

    def _animate_button_opacity(self, widget: QWidget, target: float, duration: int) -> None:
        return

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._file_tag_input and isinstance(event, QKeyEvent) and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Comma, Qt.Key.Key_Semicolon):
                self._commit_tag_input()
                return True
        return super().eventFilter(watched, event)

    def _switch_page(self, index: int) -> None:
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)
        self._sync_nav_highlight(animated=True)
        if index != self.pages.currentIndex():
            self._animate_page_switch(index)
        section_map = {
            0: "dashboard",
            1: "components",
            2: "mods",
            3: "files",
            4: "logs",
        }
        section = section_map.get(index)
        if section:
            self._mark_dirty(section)
        else:
            self._schedule_dirty_refresh()

    def _sync_nav_highlight(self, *, animated: bool) -> None:
        sidebar = self.findChild(SidebarPanel, "Sidebar")
        if sidebar is None:
            return
        current = next((btn for btn in self._nav_buttons if btn.isChecked()), None)
        if current is None:
            sidebar.clear_highlight()
            return
        sidebar.move_highlight(current.geometry(), animated=animated)

    def _animate_page_switch(self, index: int) -> None:
        if self._page_opacity_effect is None:
            self.pages.setCurrentIndex(index)
            return
        if self._page_transition_running:
            self._page_transition_target = index
            return
        if self.pages.currentIndex() == index:
            return
        overlay = self._page_transition_overlay
        overlay_label = self._page_transition_overlay_label
        overlay_blur = self._page_transition_overlay_blur_effect
        overlay_opacity = self._page_transition_overlay_opacity_effect
        if overlay is None or overlay_label is None or overlay_blur is None or overlay_opacity is None:
            self.pages.setCurrentIndex(index)
            return
        self._page_transition_target = index
        self._page_transition_running = True
        snapshot = self.pages.grab()
        if snapshot.isNull():
            self._page_transition_running = False
            self.pages.setCurrentIndex(index)
            return
        self._reposition_page_transition_overlay()
        overlay_label.setPixmap(snapshot)
        overlay_blur.setBlurRadius(0.0)
        overlay_opacity.setOpacity(1.0)
        if self._page_blur_effect is not None:
            self._page_blur_effect.setBlurRadius(0.0)
        self._page_opacity_effect.setOpacity(0.0)
        overlay.show()
        overlay.raise_()
        self.pages.setCurrentIndex(index)

        overlay_fade_anim = QPropertyAnimation(overlay_opacity, b"opacity", self)
        overlay_fade_anim.setDuration(170)
        overlay_fade_anim.setStartValue(1.0)
        overlay_fade_anim.setEndValue(0.0)
        overlay_fade_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        page_fade_anim = QPropertyAnimation(self._page_opacity_effect, b"opacity", self)
        page_fade_anim.setDuration(190)
        page_fade_anim.setStartValue(0.0)
        page_fade_anim.setEndValue(1.0)
        page_fade_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        group = QParallelAnimationGroup(self)
        group.addAnimation(overlay_fade_anim)
        group.addAnimation(page_fade_anim)

        def _finish() -> None:
            overlay.hide()
            overlay_label.clear()
            overlay_blur.setBlurRadius(0.0)
            overlay_opacity.setOpacity(1.0)
            self._page_opacity_effect.setOpacity(1.0)
            self._page_transition_running = False
            if self._page_transition_target != self.pages.currentIndex():
                self._animate_page_switch(self._page_transition_target)

        group.finished.connect(_finish)
        self._page_transition_out = overlay_fade_anim
        self._page_transition_in = page_fade_anim
        group.start()

    def _animate_window_fade(self, *, showing: bool, action: str | None = None) -> None:
        if self._window_opacity_animation is not None:
            self._window_opacity_animation.stop()
        animation = QPropertyAnimation(self, QByteArray(b"windowOpacity"), self)
        animation.setDuration(170 if showing else 130)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic if showing else QEasingCurve.Type.InCubic)
        if showing:
            self.setWindowOpacity(0.0)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
        else:
            self._window_fade_pending_action = action
            animation.setStartValue(float(self.windowOpacity()))
            animation.setEndValue(0.0)

            def _finish_hide() -> None:
                pending = self._window_fade_pending_action
                self._window_fade_pending_action = None
                if pending == "tray":
                    self.setWindowOpacity(1.0)
                    self.hide()
                    if not self._tray_notifications_shown:
                        self.tray_icon.showMessage("Zapret Hub", self._t("Приложение свернуто в трей.", "Minimized to tray."), QSystemTrayIcon.MessageIcon.Information, 2200)
                        self._tray_notifications_shown = True
                elif pending == "minimize":
                    self.showMinimized()
                    QTimer.singleShot(0, lambda: self.setWindowOpacity(1.0))
                elif pending == "exit":
                    self.setWindowOpacity(1.0)
                    self.hide()
                    QTimer.singleShot(0, self._finalize_exit)
                else:
                    self.setWindowOpacity(1.0)

            animation.finished.connect(_finish_hide)
        self._window_opacity_animation = animation
        animation.start()


    def _open_settings_dialog(self) -> None:
        signature = (self.context.settings.get().theme, self.context.settings.get().language)
        if self._settings_dialog is None or self._settings_dialog_signature != signature:
            if self._settings_dialog is not None:
                self._settings_dialog.deleteLater()
            self._settings_dialog = SettingsDialog(self, self.context)
            self._settings_dialog_signature = signature
        dialog = self._settings_dialog
        dialog._load()
        dialog.prepare_and_center()
        if dialog.exec():
            before = self.context.settings.get()
            payload = dialog.payload()
            if signature != (str(payload["theme"]), str(payload["language"])):
                self._settings_dialog = None
                self._settings_dialog_signature = None
            QTimer.singleShot(0, lambda p=payload, b=before: self._apply_settings_payload(b, p))

    def _apply_settings_payload(self, before, payload: dict[str, object]) -> None:
        self._submit_backend_task("apply_settings", payload, action_id="__settings__")

    def _prime_cached_dialogs(self) -> None:
        if self._launch_hidden:
            return
        signature = (self.context.settings.get().theme, self.context.settings.get().language)
        if self._settings_dialog is None or self._settings_dialog_signature != signature:
            self._settings_dialog = SettingsDialog(self, self.context)
            self._settings_dialog_signature = signature

    def _submit_backend_task(self, action: str, payload: dict[str, object] | None = None, *, action_id: str | None = None) -> str:
        if self.context.backend is None:
            raise RuntimeError("Backend worker is not available")
        task_id = self.context.backend.submit(action, payload or {})
        self._backend_tasks[task_id] = action_id or action
        return task_id

    def _on_backend_task_finished(self, message: dict) -> None:
        task_id = str(message.get("id", ""))
        action = str(message.get("action", ""))
        action_id = self._backend_tasks.pop(task_id, action)
        payload = message.get("payload", {})
        self.context.settings.reload()
        self._update_runtime_snapshot_from_payload(payload)
        if action in {"toggle_mod", "apply_settings", "select_general", "toggle_component_enabled"}:
            self._invalidate_general_options_cache()
            self._page_payload_cache.clear()
        if action == "apply_settings":
            if bool(payload.get("autostart_changed")):
                self.context.autostart.set_enabled(bool(self.context.settings.get().autostart_windows))
            if bool(payload.get("theme_changed")):
                self._apply_theme()
            if bool(payload.get("language_changed")):
                self._retranslate_ui()
            self._mark_dirty("dashboard", "components", "mods", "files", "logs", "tray")
        if action in {"toggle_master_runtime", "start_enabled_components", "select_general"}:
            self._mark_dirty("dashboard", "components", "tray")
            self._ui_signals.toggle_done.emit()
            if action == "select_general":
                self._ui_signals.component_action_done.emit("__general__")
            return
        if action == "apply_settings":
            self._ui_signals.component_action_done.emit("__settings__")
            return
        if action == "toggle_component_enabled":
            self._mark_dirty("dashboard", "components", "tray")
            self._ui_signals.component_action_done.emit(action_id)
            return
        if action == "toggle_component_autostart":
            self._mark_dirty("components")
            self._ui_signals.component_action_done.emit(action_id)
            return
        if action == "toggle_mod":
            self._mark_dirty("dashboard", "mods", "files", "logs", "tray")
            return
        if action == "restart_zapret_if_running":
            self._mark_dirty("dashboard", "components", "tray")
            return
        if action == "run_general_diagnostics":
            self._ui_signals.general_test_done.emit(payload.get("results", []))

    def _on_backend_task_failed(self, message: dict) -> None:
        task_id = str(message.get("id", ""))
        action = str(message.get("action", ""))
        action_id = self._backend_tasks.pop(task_id, action)
        error = str(message.get("error", self._t("Неизвестная ошибка.", "Unknown error.")))
        if action in {"toggle_master_runtime", "start_enabled_components", "select_general"}:
            self._ui_signals.toggle_done.emit()
            if action == "select_general":
                self._ui_signals.component_action_done.emit("__general__")
        if action == "apply_settings":
            self._ui_signals.component_action_done.emit("__settings__")
        if action in {"toggle_component_enabled", "toggle_component_autostart"}:
            self._ui_signals.component_action_done.emit(action_id)
        if action == "run_general_diagnostics":
            self._general_test_running = False
            self._general_test_task_id = None
            self._general_test_eta_timer.stop()
            if self._general_test_dialog is not None:
                self._general_test_dialog.reject()
            self._general_test_dialog = None
            self._general_test_status_label = None
            self._general_test_eta_label = None
            self._general_test_progress_bar = None
        self._show_error("Zapret Hub", error)

    def _on_backend_task_progress(self, message: dict) -> None:
        action = str(message.get("action", ""))
        payload = message.get("payload", {})
        if action == "run_general_diagnostics" and isinstance(payload, dict):
            self._ui_signals.general_test_progress.emit(
                int(payload.get("current", 0) or 0),
                int(payload.get("total", 0) or 0),
                str(payload.get("name", "") or ""),
            )

    def _apply_theme(self) -> None:
        theme = self.context.settings.get().theme
        chevron = str((self._icons_dir / "chevron_down.svg").resolve())
        check = str((self._icons_dir / "check.svg").resolve())
        self.setStyleSheet(build_stylesheet(theme, chevron_icon=chevron, check_icon=check))
        self._update_power_icon()
        if isinstance(self.power_button, AnimatedPowerButton):
            self.power_button.set_power_theme(theme)
        if self.power_aura is not None:
            self.power_aura.set_power_theme(theme)
        sidebar = self.findChild(SidebarPanel, "Sidebar")
        if sidebar is not None:
            sidebar.set_theme(theme)
        for btn in self._nav_buttons:
            if isinstance(btn, AnimatedNavButton):
                btn.set_nav_theme(theme)
        self._sync_nav_highlight(animated=False)
        self._apply_titlebar_icons(theme)

    def _apply_titlebar_icons(self, theme: str) -> None:
        if self._min_btn is None or self._close_btn is None:
            return
        suffix = "light" if is_light_theme(theme) else "dark"
        self._min_btn.setIcon(self._icon(f"window_min_{suffix}.svg"))
        self._close_btn.setIcon(self._icon(f"window_close_{suffix}.svg"))

    def _theme_status_icon_name(self) -> str:
        return "status_sun.svg" if is_light_theme(self.context.settings.get().theme) else "status_theme.svg"

    def _update_power_icon(self) -> None:
        if not hasattr(self, "power_button") or self.power_button is None:
            return
        theme = self.context.settings.get().theme
        state = str(self.power_button.property("state") or "off")
        if self._toggle_in_progress or state != "off" or not is_light_theme(theme):
            power_icon = "power_dark.svg"
        else:
            power_icon = "power_light.svg"
        self.power_button.setIcon(self._icon(power_icon))

    def _retranslate_ui(self) -> None:
        nav_tooltips = [
            self._t("Главная", "Dashboard"),
            self._t("Компоненты", "Components"),
            self._t("Модификации", "Mods"),
            self._t("Файлы", "Files"),
            self._t("Логи", "Logs"),
        ]
        for index, btn in enumerate(self._nav_buttons):
            if index < len(nav_tooltips):
                btn.setToolTip(nav_tooltips[index])

        if self._tools_btn is not None:
            self._tools_btn.setToolTip(self._t("Инструменты", "Tools"))
            self._tools_btn.setMenu(self._build_tools_menu())
        if self._settings_btn is not None:
            self._settings_btn.setToolTip(self._t("Настройки", "Settings"))

        if self._dashboard_title_label is not None:
            self._dashboard_title_label.setText(self._t("Быстрый доступ", "Quick Access"))
        if self._components_title_label is not None:
            self._components_title_label.setText(self._t("Компоненты", "Components"))
        if self._mods_title_label is not None:
            self._mods_title_label.setText(self._t("Модификации", "Mods"))
        if self._mods_subtitle_label is not None:
            self._mods_subtitle_label.setText(
                self._t(
                    "Здесь можно аккуратно подключать свои сборки, не ломая базовую конфигурацию.",
                    "This is where you can attach your own packs without touching the base configuration.",
                )
            )
        if self._mods_add_btn is not None:
            self._mods_add_btn.setText(self._t("Добавить", "Add"))
        if hasattr(self, "mods_import_hint") and self.mods_import_hint is not None:
            self.mods_import_hint.setText(
                self._t(
                    "Можно добавить папку, ZIP, отдельные файлы или целый GitHub-репозиторий. Приложение само заберет только совместимые файлы.",
                    "You can add a folder, ZIP, selected files, or a full GitHub repository. The app will keep only compatible files.",
                )
            )
        if self._files_title_label is not None:
            self._files_title_label.setText(self._t("Файлы", "Files"))
        if self._files_intro_label is not None:
            self._files_intro_label.setText(
                self._t(
                    "Выберите удобный режим: быстрый список доменов, IP-адреса или полноценное редактирование файлов.",
                    "Choose the mode you need: quick domain/IP editing or full file editing.",
                )
            )
        file_mode_texts = {
            "domains": (
                self._t("Домены", "Domains"),
                self._t(
                    "Добавляйте сервисы, которые нужно направить в общий список обхода.",
                    "Add services that should be placed into the general bypass list.",
                ),
            ),
            "exclude_domains": (
                self._t("Исключения", "Exclude domains"),
                self._t(
                    "Отдельный список доменов, которые нужно исключить из правил.",
                    "A separate list of domains that should be excluded from rules.",
                ),
            ),
            "ips": (
                self._t("IP-адреса", "IP addresses"),
                self._t(
                    "Ручной список IP и подсетей, которые нужно исключить из IPSet.",
                    "A manual list of IPs and subnets to exclude from IPSet.",
                ),
            ),
            "advanced": (
                self._t("Редактирование файлов", "Advanced editor"),
                self._t(
                    "Открыть полноценный список файлов и текстовый редактор.",
                    "Open the full file list and the text editor.",
                ),
            ),
        }
        for entry in self._file_mode_cards:
            kind = str(entry.get("kind", ""))
            title_desc = file_mode_texts.get(kind)
            if not title_desc:
                continue
            title_label = entry.get("title")
            desc_label = entry.get("description")
            if isinstance(title_label, QLabel):
                title_label.setText(title_desc[0])
            if isinstance(desc_label, QLabel):
                desc_label.setText(title_desc[1])
        if self._editor_title_label is not None:
            self._editor_title_label.setText(self._t("Редактор", "Editor"))
        if self._logs_title_label is not None:
            self._logs_title_label.setText(self._t("Логи", "Logs"))
        if self._logs_refresh_btn is not None:
            self._logs_refresh_btn.setText(self._t("Обновить", "Refresh"))

        title_map = {
            "app": self._t("Приложение", "App"),
            "zapret": "Zapret",
            "tg": "TG Proxy",
            "mods": "Mods",
            "theme": self._t("Тема", "Theme"),
        }
        for key, title in title_map.items():
            badge = self._status_badges.get(key)
            if badge is None:
                continue
            badge.title = title
            badge.title_label.setText(title)

        if self._tray_show_action is not None:
            self._tray_show_action.setText(self._t("Открыть", "Open"))
        if self._tray_toggle_action is not None:
            self._tray_toggle_action.setText(self._t("Компоненты", "Components"))
        if self._tray_general_menu is not None:
            self._tray_general_menu.setTitle(self._t("Конфигурация Zapret", "Zapret configuration"))
        if self._tray_quit_action is not None:
            self._tray_quit_action.setText(self._t("Выход", "Exit"))

        if hasattr(self, "files_list") and self.files_list.currentItem() is None:
            self.file_path_label.setText(self._t("Выберите файл", "Select a file"))

        self._rebuild_tray_menu()

    def _format_general_option_label(self, option: dict[str, str]) -> str:
        favorite = str(option.get("id", "")) in self._favorite_general_ids()
        bundle = (option.get("bundle") or "").strip()
        name = option.get("name", "").strip()
        label = name if not bundle else f"({bundle}) {name}"
        return f"★ {label}" if favorite else label

    def _favorite_general_ids(self) -> list[str]:
        return list(self.context.settings.get().favorite_zapret_generals or [])

    def _is_general_favorite(self, general_id: str) -> bool:
        return general_id in set(self._favorite_general_ids())

    def _set_general_favorite(self, general_id: str, favorite: bool) -> None:
        favorites = [item for item in self._favorite_general_ids() if item]
        if favorite and general_id not in favorites:
            favorites.append(general_id)
        if not favorite:
            favorites = [item for item in favorites if item != general_id]
        self.context.settings.update(favorite_zapret_generals=favorites)

    def _invalidate_general_options_cache(self) -> None:
        self._general_options_cache = None

    def _sorted_general_options(self) -> list[dict[str, str]]:
        if self._general_options_cache is None:
            self._general_options_cache = self.context.processes.list_zapret_generals()
        options = list(self._general_options_cache)
        favorites = {item for item in self._favorite_general_ids() if item}
        return sorted(
            options,
            key=lambda item: (
                0 if item["id"] in favorites else 1,
                (item.get("bundle") or "").lower(),
                (item.get("name") or "").lower(),
            ),
        )

    def _start_component_loading(self, component_id: str, button: QPushButton, base_text: str) -> None:
        self._component_loading_buttons[component_id] = button
        self._component_loading_base_text[component_id] = base_text
        button.setEnabled(False)
        self._component_loading_frame = 0
        if not self._component_loading_timer.isActive():
            self._component_loading_timer.start()
        self._advance_component_loading()

    def _stop_component_loading(self, component_id: str) -> None:
        button = self._component_loading_buttons.pop(component_id, None)
        base_text = self._component_loading_base_text.pop(component_id, None)
        if button is not None:
            try:
                button.setEnabled(True)
                if base_text is not None:
                    button.setText(base_text)
            except RuntimeError:
                pass
        if not self._component_loading_buttons and self._general_loading_label is None:
            self._component_loading_timer.stop()

    def _animate_label_text(self, label: QLabel, text: str, *, duration: int = 170) -> None:
        try:
            if label.text() == text:
                return
            parent = label.parentWidget()
            if parent is None:
                label.setText(text)
                return
            old = QLabel(parent)
            old.setText(label.text())
            old.setGeometry(label.geometry())
            old.setFont(label.font())
            old.setAlignment(label.alignment())
            old.setObjectName(label.objectName())
            old.setProperty("class", label.property("class"))
            old.setStyleSheet("background: transparent;")
            old.show()
            old.raise_()
            old.style().unpolish(old)
            old.style().polish(old)
            old_opacity = QGraphicsOpacityEffect(old)
            old_opacity.setOpacity(1.0)
            old.setGraphicsEffect(old_opacity)
            fade_old = QPropertyAnimation(old_opacity, b"opacity", self)
            fade_old.setDuration(duration)
            fade_old.setStartValue(1.0)
            fade_old.setEndValue(0.0)
            fade_old.setEasingCurve(QEasingCurve.Type.InCubic)
            blur_effect = getattr(label, "_text_blur_effect", None)
            if blur_effect is None:
                blur_effect = QGraphicsBlurEffect(label)
                blur_effect.setBlurRadius(0.0)
                label.setGraphicsEffect(blur_effect)
                setattr(label, "_text_blur_effect", blur_effect)
            label.setText(text)
            blur_effect.setBlurRadius(7.0)
            blur_anim = QPropertyAnimation(blur_effect, b"blurRadius", self)
            blur_anim.setDuration(duration + 40)
            blur_anim.setStartValue(7.0)
            blur_anim.setEndValue(0.0)
            blur_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            group = QParallelAnimationGroup(self)
            group.addAnimation(fade_old)
            group.addAnimation(blur_anim)
            group.finished.connect(old.deleteLater)
            group.start()
        except Exception:
            label.setText(text)

    def _animate_caption_dots(self, dots: str, *, duration: int = 150) -> None:
        if self.power_caption_dots is None:
            return
        if self.power_caption_dots.text() == dots:
            return
        if self._power_caption_dots_opacity is None or self._power_caption_dots_blur is None:
            self.power_caption_dots.setText(dots)
            return
        fade_out = QPropertyAnimation(self._power_caption_dots_opacity, b"opacity", self)
        fade_out.setDuration(max(70, duration // 2))
        fade_out.setStartValue(float(self._power_caption_dots_opacity.opacity()))
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        blur_out = QPropertyAnimation(self._power_caption_dots_blur, b"blurRadius", self)
        blur_out.setDuration(max(70, duration // 2))
        blur_out.setStartValue(float(self._power_caption_dots_blur.blurRadius()))
        blur_out.setEndValue(6.0)
        blur_out.setEasingCurve(QEasingCurve.Type.InCubic)
        out_group = QParallelAnimationGroup(self)
        out_group.addAnimation(fade_out)
        out_group.addAnimation(blur_out)

        def _show_new() -> None:
            if self.power_caption_dots is None or self._power_caption_dots_opacity is None or self._power_caption_dots_blur is None:
                return
            self.power_caption_dots.setText(dots)
            self._power_caption_dots_blur.setBlurRadius(6.0)
            fade_in = QPropertyAnimation(self._power_caption_dots_opacity, b"opacity", self)
            fade_in.setDuration(duration)
            fade_in.setStartValue(0.0)
            fade_in.setEndValue(1.0)
            fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
            blur_in = QPropertyAnimation(self._power_caption_dots_blur, b"blurRadius", self)
            blur_in.setDuration(duration)
            blur_in.setStartValue(6.0)
            blur_in.setEndValue(0.0)
            blur_in.setEasingCurve(QEasingCurve.Type.OutCubic)
            in_group = QParallelAnimationGroup(self)
            in_group.addAnimation(fade_in)
            in_group.addAnimation(blur_in)
            in_group.start()

        out_group.finished.connect(_show_new)
        out_group.start()

    def _advance_component_loading(self) -> None:
        frames = ["", ".", "..", "...", "..", "."]
        frame = frames[self._component_loading_frame % len(frames)]
        self._component_loading_frame += 1
        for button in list(self._component_loading_buttons.values()):
            try:
                button.setText(frame)
            except RuntimeError:
                continue
        if self._general_loading_label is not None:
            try:
                self._general_loading_label.setText(f"{self._t('Применение', 'Applying')}{frame}")
            except RuntimeError:
                self._general_loading_label = None
        if not self._component_loading_buttons and self._general_loading_label is None:
            self._component_loading_timer.stop()

    def _minimize_window_native(self) -> None:
        self._animate_window_fade(showing=False, action="minimize")

    def _selected_component_id(self) -> str | None:
        item = self.components_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _selected_mod_id(self) -> str | None:
        if not hasattr(self, "mods_list"):
            return None
        item = self.mods_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _open_files_mode(self, mode: str) -> None:
        if self._file_mode_stack is None:
            return
        if mode == "home":
            self._file_mode_stack.setCurrentIndex(0)
            return
        if mode == "advanced":
            self._file_mode_stack.setCurrentIndex(2)
            self._mark_dirty("files")
            return
        self._current_file_collection = mode
        self._refresh_file_collection_view()
        self._file_mode_stack.setCurrentIndex(1)

    def _refresh_file_collection_view(self) -> None:
        self._refresh_file_collection_view_with_values(None)

    def _refresh_file_collection_view_with_values(self, values: list[str] | None) -> None:
        titles = {
            "domains": (
                self._t("Домены", "Domains"),
                self._t(
                    "Добавляйте домены, которые нужно включить в пользовательский список обхода.",
                    "Add domains that should be included in the user bypass list.",
                ),
            ),
            "exclude_domains": (
                self._t("Исключения", "Exclude domains"),
                self._t(
                    "Здесь можно указать домены, которые нужно исключить из правил Zapret.",
                    "Here you can list domains that should be excluded from Zapret rules.",
                ),
            ),
            "ips": (
                self._t("IP-адреса", "IP addresses"),
                self._t(
                    "Добавляйте IP-адреса и подсети, которые нужно исключить из IPSet.",
                    "Add IP addresses and subnets that should be excluded from IPSet.",
                ),
            ),
        }
        title, subtitle = titles.get(self._current_file_collection, (self._t("Файлы", "Files"), ""))
        if self._file_tag_title is not None:
            self._file_tag_title.setText(title)
        if self._file_tag_subtitle is not None:
            self._file_tag_subtitle.setText(subtitle)
        if self._file_tag_input is not None:
            placeholder = self._t("Введите значение и нажмите Enter", "Type a value and press Enter")
            self._file_tag_input.setPlaceholderText(placeholder)
            self._file_tag_input.clear()
        self._render_file_tags(values)

    def _render_file_tags(self, values: list[str] | None = None) -> None:
        if self._file_tag_flow is None:
            return
        while self._file_tag_flow.count():
            item = self._file_tag_flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        resolved_values = list(values if values is not None else self.context.files.read_collection(self._current_file_collection))
        self._current_file_values_cache = resolved_values
        for value in resolved_values:
            chip = QFrame()
            chip.setProperty("class", "modMeta")
            chip_layout = QHBoxLayout(chip)
            chip_layout.setContentsMargins(10, 6, 8, 6)
            chip_layout.setSpacing(8)
            label = QLabel(value)
            remove_btn = QToolButton()
            remove_btn.setProperty("class", "action")
            remove_btn.setText("×")
            remove_btn.clicked.connect(lambda _=False, item=value: self._remove_file_tag(item))
            chip_layout.addWidget(label)
            chip_layout.addWidget(remove_btn)
            self._file_tag_flow.addWidget(chip)
        if self._file_tag_canvas is not None:
            self._file_tag_canvas.adjustSize()

    def _commit_tag_input(self) -> None:
        if self._file_tag_input is None:
            return
        raw = self._file_tag_input.text().strip()
        if not raw:
            return
        self.context.files.add_collection_values(self._current_file_collection, raw)
        self._file_tag_input.clear()
        self._render_file_tags()
        self._restart_zapret_if_running()

    def _remove_file_tag(self, value: str) -> None:
        self.context.files.remove_collection_value(self._current_file_collection, value)
        self._render_file_tags()
        self._restart_zapret_if_running()

    def _restart_zapret_if_running(self) -> None:
        try:
            states = self._component_states()
            if states.get("zapret") and states["zapret"].status == "running":
                self._submit_backend_task("restart_zapret_if_running")
        except Exception:
            return

    def _selected_file_path(self) -> str | None:
        item = self.files_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _toggle_master_runtime(self) -> None:
        if self._toggle_in_progress:
            return
        states = self._component_states()
        active_ids = self._master_active_components()
        running_ids = {cid for cid in active_ids if states.get(cid) and states[cid].status == "running"}
        self._loading_action = "disconnect" if active_ids and running_ids == set(active_ids) else "connect"
        self._toggle_in_progress = True
        self.power_button.setEnabled(False)
        if isinstance(self.power_button, AnimatedPowerButton):
            self.power_button.play_wave(outward=self._loading_action == "connect")
        if self.power_aura is not None:
            self.power_aura.play_wave(outward=self._loading_action == "connect")
        self._loading_frame = 0
        self._loading_timer.start()
        self._advance_loading_caption()
        self._submit_backend_task("toggle_master_runtime")

    def _toggle_master_runtime_worker(self) -> None:
        try:
            states = self._component_states()
            active_ids = self._master_active_components()
            if not active_ids:
                return
            running_ids = {cid for cid in active_ids if states.get(cid) and states[cid].status == "running"}
            if running_ids == set(active_ids):
                for cid in active_ids:
                    self.context.processes.stop_component(cid)
            else:
                for cid in active_ids:
                    if cid not in running_ids:
                        self.context.processes.start_component(cid)
        finally:
            self._ui_signals.toggle_done.emit()

    def _on_master_toggle_finished(self) -> None:
        self._loading_timer.stop()
        self._toggle_in_progress = False
        self.power_button.setEnabled(True)
        self._update_power_icon()
        self.refresh_all()
        if self._pending_info_message is not None:
            title, text = self._pending_info_message
            self._pending_info_message = None
            self._show_info(title, text)

    def _advance_loading_caption(self) -> None:
        if not self._toggle_in_progress:
            return
        base = self._t("Подключение", "Connecting") if self._loading_action == "connect" else self._t("Отключение", "Disconnecting")
        dots_frames = ["", ".", "..", "...", "..", "."]
        full_text = f"{base}{dots_frames[self._loading_frame % len(dots_frames)]}"
        if self.power_caption_dots is not None:
            self.power_caption_dots.setText("")
            self.power_caption_dots.hide()
        if self.power_caption_text is not None:
            self.power_caption_text.setText(full_text)
        self._power_caption_base_text = base
        self._loading_frame += 1
        self.power_button.setProperty("state", "loading")
        if isinstance(self.power_button, AnimatedPowerButton):
            self.power_button.set_loading_state(True, animate=True)
        if self.power_aura is not None:
            self.power_aura.set_idle_pulse_enabled(False)
        self._update_power_icon()

    def _start_selected_component(self) -> None:
        component_id = self._selected_component_id()
        if component_id:
            self.context.processes.start_component(component_id)
            self.refresh_all()

    def _stop_selected_component(self) -> None:
        component_id = self._selected_component_id()
        if component_id:
            self.context.processes.stop_component(component_id)
            self.refresh_all()

    def _toggle_selected_component_enabled(self) -> None:
        component_id = self._selected_component_id()
        if component_id:
            self._submit_backend_task("toggle_component_enabled", {"component_id": component_id}, action_id=component_id)

    def _toggle_selected_component_autostart(self) -> None:
        component_id = self._selected_component_id()
        if component_id:
            self._submit_backend_task("toggle_component_autostart", {"component_id": component_id}, action_id=component_id)

    def _toggle_component_card(self, component_id: str, button: QPushButton) -> None:
        if component_id in self._component_loading_buttons:
            return
        self._start_component_loading(component_id, button, button.text())
        self._submit_backend_task("toggle_component_enabled", {"component_id": component_id}, action_id=component_id)

    def _toggle_component_card_worker(self, component_id: str) -> None:
        self._submit_backend_task("toggle_component_enabled", {"component_id": component_id}, action_id=component_id)

    def _install_selected_mod(self) -> None:
        mod_id = self._selected_mod_id()
        if mod_id:
            self.context.mods.install(mod_id)
            self._invalidate_general_options_cache()
            self.refresh_all()

    def _toggle_selected_mod(self) -> None:
        mod_id = self._selected_mod_id()
        if not mod_id:
            return
        installed = {item.id: item for item in self.context.mods.list_installed()}
        if mod_id not in installed:
            self._show_info(self._t("Модификация", "Mod"), self._t("Сначала установите модификацию, затем включайте её.", "Install selected mod before enabling it."))
            return
        self._submit_backend_task("toggle_mod", {"mod_id": mod_id}, action_id=f"mod:{mod_id}")

    def _remove_selected_mod(self) -> None:
        mod_id = self._selected_mod_id()
        if mod_id:
            self.context.mods.remove(mod_id)
            self._invalidate_general_options_cache()
            self.refresh_all()

    def _import_mod_any(self) -> None:
        chooser = AppDialog(self, self.context, self._t("Добавить модификацию", "Add modification"))
        chooser.setMinimumWidth(520)
        chooser_text = QLabel(
            self._t(
                "Выберите удобный источник. Хаб сам вытащит только совместимые general-файлы, списки и нужные runtime-файлы.",
                "Choose the source you want. The hub will keep only compatible general files, lists, and required runtime files.",
            )
        )
        chooser_text.setWordWrap(True)
        chooser_text.setProperty("class", "muted")
        chooser.body_layout.addWidget(chooser_text)

        buttons = QGridLayout()
        buttons.setHorizontalSpacing(10)
        buttons.setVerticalSpacing(10)
        folder_btn = QPushButton(self._t("Папка", "Folder"))
        folder_btn.setProperty("class", "primary")
        zip_btn = QPushButton(self._t("ZIP-архив", "ZIP archive"))
        zip_btn.setProperty("class", "primary")
        files_btn = QPushButton(self._t("Файл(ы)", "File(s)"))
        files_btn.setProperty("class", "primary")
        github_btn = QPushButton(self._t("GitHub", "GitHub"))
        github_btn.setProperty("class", "primary")
        cancel_btn = QPushButton(self._t("Отмена", "Cancel"))
        self._attach_button_animations(folder_btn)
        self._attach_button_animations(zip_btn)
        self._attach_button_animations(files_btn)
        self._attach_button_animations(github_btn)
        self._attach_button_animations(cancel_btn)
        buttons.addWidget(folder_btn, 0, 0)
        buttons.addWidget(zip_btn, 0, 1)
        buttons.addWidget(files_btn, 1, 0)
        buttons.addWidget(github_btn, 1, 1)
        buttons.addWidget(cancel_btn, 2, 0, 1, 2)
        chooser.body_layout.addLayout(buttons)

        selected_kind: dict[str, str] = {"kind": ""}
        folder_btn.clicked.connect(lambda: (selected_kind.__setitem__("kind", "folder"), chooser.accept()))
        zip_btn.clicked.connect(lambda: (selected_kind.__setitem__("kind", "zip"), chooser.accept()))
        files_btn.clicked.connect(lambda: (selected_kind.__setitem__("kind", "files"), chooser.accept()))
        github_btn.clicked.connect(lambda: (selected_kind.__setitem__("kind", "github"), chooser.accept()))
        cancel_btn.clicked.connect(chooser.reject)
        chooser.prepare_and_center()
        if chooser.exec() != QDialog.DialogCode.Accepted:
            return

        path = ""
        paths: list[str] = []
        if selected_kind["kind"] == "folder":
            path = QFileDialog.getExistingDirectory(self, self._t("Выберите папку модификации", "Select modification folder"))
            if path:
                paths = [path]
        elif selected_kind["kind"] == "zip":
            path, _ = QFileDialog.getOpenFileName(
                self,
                self._t("Выберите ZIP-архив модификации", "Select modification ZIP archive"),
                filter=self._t("ZIP-архив (*.zip)", "ZIP archive (*.zip)"),
            )
            if path:
                paths = [path]
        elif selected_kind["kind"] == "files":
            paths, _ = QFileDialog.getOpenFileNames(
                self,
                self._t("Выберите файлы модификации", "Select modification files"),
                filter=self._t(
                    "Совместимые файлы (*.bat *.cmd *.txt *.json *.yaml *.yml *.zip);;Все файлы (*.*)",
                    "Compatible files (*.bat *.cmd *.txt *.json *.yaml *.yml *.zip);;All files (*.*)",
                ),
            )
        elif selected_kind["kind"] == "github":
            repo_url = self._ask_text_value(
                self._t("GitHub-модификация", "GitHub modification"),
                self._t("Вставьте ссылку на GitHub-репозиторий.", "Paste a GitHub repository link."),
                self._t("Например: https://github.com/user/repo", "Example: https://github.com/user/repo"),
            )
            if not repo_url:
                return
            try:
                self.context.mods.import_from_github(repo_url)
                self._invalidate_general_options_cache()
                self.refresh_all()
            except Exception as error:
                self._show_error(self._t("Модификации", "Mods"), f"{self._t('Не удалось импортировать репозиторий', 'Failed to import repository')}:\n{error}")
            return

        if not paths:
            return
        try:
            self.context.mods.import_from_paths(paths)
            self._invalidate_general_options_cache()
            self.refresh_all()
        except Exception as error:
            self._show_error(self._t("Модификации", "Mods"), f"{self._t('Не удалось импортировать модификацию', 'Failed to import modification')}:\n{error}")

    def _import_mod_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select mod folder")
        if not path:
            return
        try:
            self.context.mods.import_from_path(path)
            self._invalidate_general_options_cache()
            self.refresh_all()
        except Exception as error:
            self._show_error(self._t("Модификации", "Mods"), f"{self._t('Не удалось импортировать папку', 'Failed to import folder')}:\n{error}")

    def _import_mod_archive(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select mod archive", filter="ZIP archive (*.zip)")
        if not path:
            return
        try:
            self.context.mods.import_from_path(path)
            self._invalidate_general_options_cache()
            self.refresh_all()
        except Exception as error:
            self._show_error(self._t("Модификации", "Mods"), f"{self._t('Не удалось импортировать архив', 'Failed to import archive')}:\n{error}")

    def _rebuild_runtime(self) -> None:
        def _worker() -> None:
            try:
                self.context.merge.rebuild()
            finally:
                self._ui_signals.component_action_done.emit("__merge_rebuild__")

        threading.Thread(target=_worker, daemon=True).start()

    def _check_updates_popup(self) -> None:
        self._start_update_check(manual=True)

    def _check_updates_on_start(self) -> None:
        if self._launch_hidden:
            return
        if not self.context.settings.get().check_updates_on_start:
            return
        self._start_update_check(manual=False)

    def _start_update_check(self, manual: bool) -> None:
        if self._update_check_in_progress:
            return
        self._update_check_in_progress = True
        thread = threading.Thread(target=self._run_update_check_worker, args=(manual,), daemon=True)
        thread.start()

    def _run_update_check_worker(self, manual: bool) -> None:
        restart_zapret = False
        try:
            states = self._component_states()
            restart_zapret = bool(states.get("zapret") and states["zapret"].status == "running")
        except Exception:
            restart_zapret = False

        try:
            if restart_zapret:
                self.context.processes.stop_component("zapret")
            release = self.context.updates.fetch_latest_application_release()
        finally:
            if restart_zapret:
                try:
                    self.context.processes.start_component("zapret")
                except Exception:
                    pass
        self._ui_signals.update_check_done.emit(release, manual)

    def _on_update_check_done(self, release: object, manual: bool) -> None:
        self._update_check_in_progress = False
        if not isinstance(release, dict):
            if manual:
                self._show_error(self._t("Обновления", "Updates"), self._t("Не удалось проверить обновления.", "Failed to check for updates."))
            return

        status = str(release.get("status", "error"))
        latest_version = str(release.get("latest_version", ""))
        if status == "available":
            if manual or self._last_prompted_update_version != latest_version:
                self._last_prompted_update_version = latest_version
                self._show_update_prompt(release)
            return
        if manual:
            if status == "up-to-date":
                self._show_info(
                    self._t("Обновления", "Updates"),
                    self._t(
                        f"У вас уже установлена последняя версия: {release.get('current_version', '')}.",
                        f"You already have the latest version: {release.get('current_version', '')}.",
                    ),
                )
            else:
                self._show_error(
                    self._t("Обновления", "Updates"),
                    str(release.get("error", self._t("Не удалось проверить обновления.", "Failed to check for updates."))),
                )

    def _show_update_prompt(self, release: dict[str, str]) -> None:
        dialog = AppDialog(self, self.context, self._t("Доступно обновление", "Update available"))
        message = QLabel(
            self._t(
                f"Вышла новая версия Zapret Hub.\n\nТекущая версия: {release.get('current_version', '')}\nНовая версия: {release.get('latest_version', '')}",
                f"A new Zapret Hub version is available.\n\nCurrent version: {release.get('current_version', '')}\nNew version: {release.get('latest_version', '')}",
            )
        )
        message.setWordWrap(True)
        dialog.body_layout.addWidget(message)

        body = str(release.get("body", "")).strip()
        if body:
            notes = QLabel(body[:800])
            notes.setWordWrap(True)
            notes.setProperty("class", "muted")
            dialog.body_layout.addWidget(notes)

        row = QHBoxLayout()
        row.addStretch(1)
        close_btn = QPushButton(self._t("Закрыть", "Close"))
        link_btn = QPushButton(self._t("Открыть ссылку", "Open link"))
        update_btn = QPushButton(self._t("Обновить сейчас", "Update now"))
        update_btn.setProperty("class", "primary")
        close_btn.clicked.connect(dialog.reject)
        link_btn.clicked.connect(lambda: self._open_update_link(str(release.get("html_url", ""))))
        update_btn.clicked.connect(lambda: self._start_update_apply(dialog, release))
        row.addWidget(close_btn)
        row.addWidget(link_btn)
        row.addWidget(update_btn)
        dialog.body_layout.addLayout(row)
        dialog.prepare_and_center()
        dialog.exec()

    def _open_update_link(self, url: str) -> None:
        if not url:
            return
        try:
            if sys.platform.startswith("win"):
                import os

                os.startfile(url)  # type: ignore[attr-defined]
            else:
                webbrowser.open(url)
        except Exception:
            webbrowser.open(url)

    def _start_update_apply(self, parent_dialog: AppDialog, release: dict[str, str]) -> None:
        parent_dialog.accept()
        if self._update_prepare_dialog is not None:
            return
        dialog = AppDialog(self, self.context, self._t("Подготовка обновления", "Preparing update"))
        label = QLabel(self._t("Скачиваем и подготавливаем новую версию. Приложение перезапустится автоматически.", "Downloading and preparing the new version. The app will restart automatically."))
        label.setWordWrap(True)
        dialog.body_layout.addWidget(label)
        bar = QProgressBar()
        bar.setRange(0, 0)
        dialog.body_layout.addWidget(bar)
        dialog.prepare_and_center()
        dialog.show()
        self._update_prepare_dialog = dialog
        thread = threading.Thread(target=self._run_update_prepare_worker, args=(release,), daemon=True)
        thread.start()

    def _run_update_prepare_worker(self, release: dict[str, str]) -> None:
        try:
            prepared = self.context.updates.prepare_update(release)
            self._ui_signals.update_prepare_done.emit({"ok": True, "prepared": prepared})
        except Exception as error:
            self._ui_signals.update_prepare_done.emit({"ok": False, "error": str(error)})

    def _on_update_prepare_done(self, payload: object) -> None:
        if self._update_prepare_dialog is not None:
            self._update_prepare_dialog.accept()
            self._update_prepare_dialog = None
        if not isinstance(payload, dict) or not payload.get("ok"):
            self._show_error(
                self._t("Обновления", "Updates"),
                str((payload or {}).get("error", self._t("Не удалось подготовить обновление.", "Failed to prepare the update."))) if isinstance(payload, dict) else self._t("Не удалось подготовить обновление.", "Failed to prepare the update."),
            )
            return
        prepared = payload.get("prepared")
        if not isinstance(prepared, dict):
            self._show_error(self._t("Обновления", "Updates"), self._t("Некорректный пакет обновления.", "Invalid update package."))
            return
        try:
            self.context.updates.launch_update(prepared)
        except Exception as error:
            self._show_error(self._t("Обновления", "Updates"), str(error))
            return
        self._force_exit = True
        self.close()

    def _run_diagnostics_popup(self) -> None:
        results = self.context.diagnostics.run_all()
        text = "\n".join(f"{item.name}: {item.status}" for item in results)
        self._show_info(self._t("Диагностика", "Diagnostics"), text or self._t("Нет данных диагностики.", "No diagnostics data."))

    def _load_selected_file(self, *_args: object) -> None:
        full_path = self._selected_file_path()
        if not full_path:
            return
        item = self.files_list.currentItem()
        label_text = item.text().split("\n")[0] if item else full_path
        self.file_path_label.setText(label_text)
        self.file_editor.setPlainText(self.context.files.read_text(full_path))

    def _save_current_file(self) -> None:
        full_path = self._selected_file_path()
        if not full_path:
            self._show_info(self._t("Файлы", "Files"), self._t("Выберите файл перед сохранением.", "Select a file before saving."))
            return
        self.context.files.write_text(full_path, self.file_editor.toPlainText())
        self.context.logging.log("info", "File saved", path=full_path)
        self.refresh_logs()

    def _rename_current_file(self) -> None:
        full_path = self._selected_file_path()
        if not full_path:
            self._show_info(self._t("Файлы", "Files"), self._t("Выберите файл перед переименованием.", "Select a file before renaming."))
            return
        path = Path(full_path)
        new_name, ok = QInputDialog.getText(self, "Rename file", "New file name:", text=path.name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == path.name:
            return
        target = path.with_name(new_name)
        if target.exists():
            self._show_warning(self._t("Файлы", "Files"), self._t("Файл с таким именем уже существует.", "A file with this name already exists."))
            return
        try:
            path.rename(target)
            self.context.logging.log("info", "File renamed", source=str(path), target=str(target))
            self.refresh_files()
            self.refresh_logs()
        except Exception as error:
            self._show_error(self._t("Файлы", "Files"), f"{self._t('Не удалось переименовать файл', 'Failed to rename file')}:\n{error}")

    def schedule_refresh_all(self) -> None:
        self._refresh_dirty_sections.update({"dashboard", "components", "mods", "files", "logs", "tray"})
        self._schedule_dirty_refresh()

    def _mark_dirty(self, *sections: str) -> None:
        self._refresh_dirty_sections.update(sections)
        self._schedule_dirty_refresh()

    def _schedule_dirty_refresh(self) -> None:
        if self._refresh_scheduled:
            return
        self._refresh_scheduled = True
        QTimer.singleShot(0, self._flush_dirty_refresh)

    def _flush_dirty_refresh(self) -> None:
        self._refresh_scheduled = False
        dirty = set(self._refresh_dirty_sections)
        self._refresh_dirty_sections.clear()

        if "dashboard" in dirty:
            try:
                self.refresh_dashboard()
            except Exception:
                pass
        if "tray" in dirty:
            try:
                self._rebuild_tray_menu()
            except Exception:
                pass
        if "components" in dirty:
            try:
                self.refresh_components()
            except Exception:
                pass
        if "mods" in dirty:
            try:
                self.refresh_mods()
            except Exception:
                pass
        if "files" in dirty:
            try:
                self._request_page_refresh("files")
            except Exception:
                pass
        if "logs" in dirty:
            try:
                self._request_page_refresh("logs")
            except Exception:
                pass

        if self._initial_refresh_pending:
            self._initial_refresh_pending = False
            self._hide_loading_overlay()

    def refresh_all(self) -> None:
        self.schedule_refresh_all()

    def _request_page_refresh(self, section: str) -> None:
        cached = self._page_payload_cache.get(section)
        if cached is not None:
            if section == "components":
                self.refresh_components(cached)
            elif section == "mods":
                self.refresh_mods(cached)
            elif section == "files":
                self.refresh_files(cached)
            elif section == "logs":
                self.refresh_logs(cached)
        if section in self._page_refresh_in_progress:
            return
        self._page_refresh_in_progress.add(section)
        thread = threading.Thread(target=self._collect_page_payload_worker, args=(section,), daemon=True)
        thread.start()

    def _collect_page_payload_worker(self, section: str) -> None:
        try:
            payload: object
            if section == "components":
                payload = {
                    "components": self.context.processes.list_components(),
                    "states": {item.component_id: item for item in self.context.processes.list_states()},
                }
            elif section == "mods":
                payload = {
                    "index": self.context.mods.fetch_index(),
                    "installed": {item.id: item for item in self.context.mods.list_installed()},
                }
            elif section == "files":
                payload = {
                    "records": self.context.files.list_files(),
                    "collection_values": self.context.files.read_collection(self._current_file_collection),
                    "collection_id": self._current_file_collection,
                }
            elif section == "logs":
                entries = self.context.logging.read_entries()
                payload = [f"[{e.timestamp}] {e.level}: {e.message} {e.context}" for e in entries[-250:]]
            else:
                payload = None
            self._ui_signals.page_payload_ready.emit(section, payload)
        except Exception:
            self._ui_signals.page_payload_ready.emit(section, None)

    def _on_page_payload_ready(self, section: str, payload: object) -> None:
        self._page_refresh_in_progress.discard(section)
        if payload is not None:
            self._page_payload_cache[section] = payload
            if section == "components":
                self._update_runtime_snapshot_from_payload(payload)
        visible_page = self.pages.currentIndex() if hasattr(self, "pages") else 0
        if section == "components" and visible_page == 1:
            self.refresh_components(payload)
        elif section == "mods" and visible_page == 2:
            self.refresh_mods(payload)
        elif section == "files" and visible_page == 3:
            self.refresh_files(payload)
        elif section == "logs" and visible_page == 4:
            self.refresh_logs(payload)
        if self._loading_overlay_context == f"page:{section}":
            self._hide_loading_overlay()

    def refresh_dashboard(self) -> None:
        settings = self.context.settings.get()
        self._refresh_general_combo(settings.selected_zapret_general)
        states = self._component_states()
        components = self._component_defs()
        active_ids = self._master_active_components()
        zapret_state = states.get("zapret", None)
        tg_state = states.get("tg-ws-proxy", None)
        running_ids = {cid for cid in active_ids if states.get(cid) and states[cid].status == "running"}
        any_running = len(running_ids) > 0
        fully_running = bool(active_ids) and set(active_ids) == running_ids

        self.power_button.setProperty("state", "on" if fully_running else "off")
        self._update_power_icon()
        if isinstance(self.power_button, AnimatedPowerButton):
            self.power_button.set_active_state(fully_running, animate=True)
        if self.power_aura is not None:
            self.power_aura.set_idle_pulse_enabled(fully_running and not self._toggle_in_progress)
        if self.power_caption_dots is not None:
            self.power_caption_dots.setText("")
            self.power_caption_dots.hide()
        self._power_caption_base_text = ""
        if not active_ids:
            if self.power_caption_text is not None:
                self.power_caption_text.setText(self._t("НЕТ КОМПОНЕНТОВ", "NO COMPONENTS"))
                self._power_caption_base_text = self._t("НЕТ КОМПОНЕНТОВ", "NO COMPONENTS")
        else:
            target_caption = self._t("ВКЛ", "ON") if fully_running else (self._t("ЧАСТИЧНО", "PARTIAL") if any_running else self._t("ВЫКЛ", "OFF"))
            if self.power_caption_text is not None:
                self.power_caption_text.setText(target_caption)
                self._power_caption_base_text = target_caption

        enabled_mods = list(settings.enabled_mod_ids or [])

        self._set_badge("app", self._t("Работает", "Running") if fully_running else (self._t("Частично", "Partial") if any_running else self._t("Ожидание", "Idle")), "status_ok.svg" if fully_running else ("status_warn.svg" if any_running else "status_off.svg"))
        zapret_text, zapret_icon = self._component_badge_state(components.get("zapret"), zapret_state, any_running)
        tg_text, tg_icon = self._component_badge_state(components.get("tg-ws-proxy"), tg_state, any_running)
        self._set_badge("zapret", zapret_text, zapret_icon)
        self._set_badge("tg", tg_text, tg_icon)
        self._set_badge("mods", f"{len(enabled_mods)} {self._t('Активно', 'Active')}", "status_mod.svg")
        self._set_badge("theme", settings.theme.title(), self._theme_status_icon_name())

        try:
            merge_state = self.context.merge.get_state()
        except Exception:
            merge_state = None
        if merge_state is None and enabled_mods:
            QTimer.singleShot(0, self._ensure_merge_runtime_ready)

    def _ensure_merge_runtime_ready(self) -> None:
        if self._merge_ensure_in_progress:
            return
        self._merge_ensure_in_progress = True

        def _worker() -> None:
            try:
                self.context.merge.rebuild()
            except Exception:
                return
            finally:
                self._merge_ensure_in_progress = False
            self._ui_signals.component_action_done.emit("__merge__")

        threading.Thread(target=_worker, daemon=True).start()

    def _component_badge_state(self, component: object, state: object, any_running: bool) -> tuple[str, str]:
        status = str(getattr(state, "status", "unknown") or "unknown").lower()
        last_error = str(getattr(state, "last_error", "") or "").strip()
        enabled = bool(getattr(component, "enabled", False))
        if status == "running":
            return self._t("Работает", "Running"), "status_ok.svg"
        if last_error or (enabled and any_running):
            return self._t("Ошибка", "Error") if last_error else self._t("Не Запущен", "Not Running"), "status_warn.svg"
        if status == "stopped":
            return self._t("Остановлен", "Stopped"), "status_off.svg"
        return self._t("Неизвестно", "Unknown"), "status_off.svg"

    def _refresh_general_combo(self, selected_id: str) -> None:
        options = self._sorted_general_options()
        self._updating_general_combo = True
        try:
            self.general_combo.clear()
            for option in options:
                label = self._format_general_option_label(option)
                self.general_combo.addItem(label, option["id"])
            if self.general_combo.count() == 0:
                return
            target_id = selected_id
            if not target_id:
                target_id = self.general_combo.itemData(0)
            for i in range(self.general_combo.count()):
                if self.general_combo.itemData(i) == target_id:
                    self.general_combo.setCurrentIndex(i)
                    break
        finally:
            self._updating_general_combo = False

    def _on_general_selected(self, _index: int) -> None:
        if self._updating_general_combo:
            return
        selected = self.general_combo.currentData()
        if not selected:
            return
        current = self.context.settings.get().selected_zapret_general
        if selected == current:
            return
        self.context.settings.get().selected_zapret_general = selected
        states = self._component_states()
        zapret_running = states.get("zapret") and states["zapret"].status == "running"
        if zapret_running:
            self._loading_action = "connect"
            self._toggle_in_progress = True
            self.power_button.setEnabled(False)
            self._loading_frame = 0
            self._loading_timer.start()
            self._advance_loading_caption()
            self._submit_backend_task("select_general", {"selected": selected}, action_id="__general__")
        else:
            self._submit_backend_task("select_general", {"selected": selected}, action_id="__general__")
            self._mark_dirty("dashboard", "components", "tray")

    def _on_general_selected_from_components(self, selected: str, combo: QComboBox, status_label: QLabel) -> None:
        if not selected:
            return
        current = self.context.settings.get().selected_zapret_general
        if selected == current:
            return
        if self._general_loading_combo is not None:
            return
        self._general_loading_combo = combo
        self._general_loading_label = status_label
        combo.setEnabled(False)
        status_label.show()
        self._component_loading_frame = 0
        if not self._component_loading_timer.isActive():
            self._component_loading_timer.start()
        self._advance_component_loading()
        self._submit_backend_task("select_general", {"selected": selected}, action_id="__general__")

    def _apply_general_selection_worker(self, selected: str) -> None:
        self.context.settings.get().selected_zapret_general = selected
        self.context.settings.save()
        states = self._component_states()
        zapret_running = states.get("zapret") and states["zapret"].status == "running"
        if zapret_running:
            self.context.processes.stop_component("zapret")
            self.context.processes.start_component("zapret")
        self._ui_signals.component_action_done.emit("__general__")

    def _sync_general_favorite_button(self, general_id: str, button: QToolButton) -> None:
        favorite = self._is_general_favorite(general_id)
        button.setIcon(self._icon("star_filled.svg" if favorite else "star_outline.svg"))
        button.setIconSize(QSize(16, 16))
        button.setToolTip(
            self._t("Убрать из избранного", "Remove from favorites")
            if favorite
            else self._t("Добавить в избранное", "Add to favorites")
        )

    def _toggle_general_favorite_from_button(self, general_id: str, button: QToolButton) -> None:
        if not general_id:
            return
        favorite = not self._is_general_favorite(general_id)
        self._sync_general_favorite_button(general_id, button)
        current = self.context.settings.get()
        favorites = [item for item in self._favorite_general_ids() if item]
        if favorite and general_id not in favorites:
            favorites.append(general_id)
        if not favorite:
            favorites = [item for item in favorites if item != general_id]
        current.favorite_zapret_generals = favorites
        self._refresh_general_combo(current.selected_zapret_general)
        self._mark_dirty("components", "tray")
        self._submit_backend_task("set_favorite_generals", {"favorites": favorites}, action_id="__favorite__")

    def _master_active_components(self) -> list[str]:
        return [c.id for c in self._component_defs().values() if c.id in ("zapret", "tg-ws-proxy") and c.enabled]

    def _maybe_run_first_general_autotest(self) -> None:
        settings = self.context.settings.get()
        if settings.general_autotest_done:
            return
        options = self._sorted_general_options()
        if not options:
            return
        if self._first_general_prompt is not None:
            try:
                self._first_general_prompt.raise_()
                self._first_general_prompt.activateWindow()
            except Exception:
                pass
            return

        dialog = AppDialog(self, self.context, self._t("Первичная настройка", "First setup"))
        dialog.setModal(False)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        label = QLabel(
            self._t(
                "Конфигурация пока не выбрана.\n\nЗапустить автоподбор сейчас?",
                "No configuration is selected yet.\n\nRun auto-check now to find a working one?",
            )
        )
        label.setWordWrap(True)
        dialog.body_layout.addWidget(label)
        row = QHBoxLayout()
        row.addStretch(1)
        no_btn = QPushButton(self._t("Нет", "No"))
        yes_btn = QPushButton(self._t("Да", "Yes"))
        yes_btn.setProperty("class", "primary")
        row.addWidget(no_btn)
        row.addWidget(yes_btn)
        dialog.body_layout.addLayout(row)

        def _cleanup_prompt() -> None:
            if self._first_general_prompt is dialog:
                self._first_general_prompt = None
            dialog.deleteLater()

        def _decline() -> None:
            settings.general_autotest_done = True
            self._submit_backend_task("set_general_autotest_done", {"done": True}, action_id="__autotest_declined__")
            dialog.close()

        def _accept() -> None:
            dialog.close()
            QTimer.singleShot(0, lambda: self._run_general_tests_popup(auto_apply=True))

        no_btn.clicked.connect(_decline)
        yes_btn.clicked.connect(_accept)
        dialog.finished.connect(lambda _result: _cleanup_prompt())
        dialog.prepare_and_center()
        self._first_general_prompt = dialog
        dialog.show()

    def _restart_zapret_worker(self) -> None:
        self.context.settings.save()
        self.context.processes.stop_component("zapret")
        self.context.processes.start_component("zapret")
        self._ui_signals.toggle_done.emit()

    def _on_component_action_done(self, action_id: str) -> None:
        if action_id == "__settings__":
            self._hide_loading_overlay()
            self._mark_dirty("dashboard", "components", "tray")
            return

        if action_id == "__favorite__":
            return

        if action_id == "__autotest_declined__":
            return

        if action_id == "__merge__":
            self._mark_dirty("dashboard")
            return

        if action_id == "__merge_rebuild__":
            self._mark_dirty("dashboard", "mods", "files", "logs", "tray")
            return

        if action_id == "__general__":
            if self._general_loading_combo is not None:
                try:
                    self._general_loading_combo.setEnabled(True)
                except RuntimeError:
                    pass
            if self._general_loading_label is not None:
                try:
                    self._general_loading_label.hide()
                    self._general_loading_label.setText("")
                except RuntimeError:
                    pass
            self._general_loading_combo = None
            self._general_loading_label = None
            if not self._component_loading_buttons:
                self._component_loading_timer.stop()
            self._mark_dirty("dashboard", "components", "tray")
            return

        self._stop_component_loading(action_id)
        self._mark_dirty("dashboard", "components", "tray")

    def _run_general_tests_popup(self, auto_apply: bool = False) -> None:
        if self._general_test_running:
            return
        options = self._sorted_general_options()
        if not options:
            self._show_info(self._t("Проверка конфигураций", "Run general tests"), self._t("Список конфигураций пока пуст.", "The configuration list is empty."))
            return

        self._general_test_running = True
        self._general_test_cancelled = False
        self._general_test_show_results = True
        self._general_test_auto_apply = auto_apply
        self._general_test_started_at = time.time()
        self._general_test_current_index = 0
        self._general_test_total = 0
        self._general_test_last_progress_at = self._general_test_started_at
        dialog = AppDialog(self, self.context, self._t("Проверка конфигураций", "Run general tests"))
        title = QLabel(
            self._t(
                "Сейчас приложение по очереди проверит все доступные конфигурации и посмотрит, какие из них действительно дают подключение ко всем тестовым серверам. Этот процесс может занять много времени.",
                "The app will now test each available configuration and show which ones can actually reach all test servers. This process may take a while.",
            )
        )
        title.setWordWrap(True)
        dialog.body_layout.addWidget(title)
        status = QLabel(self._t("Подготовка...", "Preparing..."))
        status.setProperty("class", "muted")
        dialog.body_layout.addWidget(status)
        eta = QLabel(self._t("Расчёт времени...", "Estimating time..."))
        eta.setProperty("class", "muted")
        dialog.body_layout.addWidget(eta)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        dialog.body_layout.addWidget(bar)
        dialog.prepare_and_center()
        dialog.show()
        self._general_test_dialog = dialog
        self._general_test_status_label = status
        self._general_test_eta_label = eta
        self._general_test_progress_bar = bar
        dialog.rejected.connect(self._cancel_general_tests)
        self._update_general_test_eta()
        self._general_test_eta_timer.start()
        self._general_test_task_id = self._submit_backend_task("run_general_diagnostics", action_id="__general_test__")

    def _run_general_tests_worker(self) -> None:
        results = self.context.processes.run_general_diagnostics(
            progress_callback=lambda current, total, name: self._ui_signals.general_test_progress.emit(current, total, name),
            stop_callback=lambda: self._general_test_cancelled,
        )
        self._ui_signals.general_test_done.emit(results)

    def _cancel_general_tests(self) -> None:
        self._general_test_cancelled = True
        self._general_test_show_results = False
        self._general_test_eta_timer.stop()
        if self.context.backend is not None and self._general_test_task_id:
            self.context.backend.cancel(self._general_test_task_id)

    def _on_general_test_progress(self, current: int, total: int, name: str) -> None:
        self._general_test_current_index = current
        self._general_test_total = total
        self._general_test_last_progress_at = time.time()
        if self._general_test_progress_bar is not None:
            self._general_test_progress_bar.setMaximum(total)
            self._general_test_progress_bar.setValue(max(0, min(current, total)))
        if self._general_test_status_label is not None:
            self._general_test_status_label.setText(
                self._t(
                    f"Проверяется: {name}",
                    f"Checking: {name}",
                )
            )
        self._update_general_test_eta()

    def _update_general_test_eta(self) -> None:
        if self._general_test_eta_label is None or self._general_test_total <= 0:
            return
        if self._general_test_current_index <= 2 or self._general_test_started_at <= 0:
            self._general_test_eta_label.setText(self._t("Расчёт времени...", "Estimating time..."))
            return
        now = time.time()
        elapsed = max(0.0, now - self._general_test_started_at)
        completed = max(1, self._general_test_current_index)
        avg_per_step = max(0.1, elapsed / completed)
        in_step_elapsed = max(0.0, now - self._general_test_last_progress_at)
        in_step_fraction = min(0.95, in_step_elapsed / avg_per_step)
        effective_completed = min(float(self._general_test_total), completed + in_step_fraction)
        remaining_after_current = max(0.0, float(self._general_test_total) - effective_completed)
        estimate_seconds = max(0, round(avg_per_step * remaining_after_current))
        self._general_test_eta_label.setText(
            self._t(
                f"Осталось примерно: {estimate_seconds} сек.",
                f"About {estimate_seconds}s remaining.",
            )
        )

    def _on_general_test_done(self, results: object) -> None:
        self._general_test_running = False
        self._general_test_task_id = None
        self._general_test_eta_timer.stop()
        if self._general_test_dialog is not None:
            self._general_test_dialog.accept()
        self._general_test_dialog = None
        self._general_test_status_label = None
        self._general_test_eta_label = None
        self._general_test_progress_bar = None

        checked = results if isinstance(results, list) else []
        working: list[str] = []
        failed: list[str] = []
        best_label = ""
        best_score = -1
        best_total = 0
        best_id = ""
        best_working_id = ""
        for raw in checked:
            if not isinstance(raw, dict):
                continue
            label = self._format_general_option_label(
                {
                    "id": str(raw.get("id", "")),
                    "bundle": str(raw.get("bundle", "")),
                    "name": str(raw.get("name", "")),
                }
            )
            passed = int(str(raw.get("passed_targets", 0)) or 0)
            total = int(str(raw.get("total_targets", 0)) or 0)
            if passed > best_score:
                best_score = passed
                best_total = total
                best_label = label
                best_id = str(raw.get("id", ""))
            if raw.get("status") == "ok":
                working.append(label)
                if not best_working_id:
                    best_working_id = str(raw.get("id", ""))
            else:
                error_text = str(raw.get("error", "")).strip() or self._t("не удалось запустить", "failed to start")
                failed.append(f"{label} - {error_text}")

        chosen_id = best_working_id or best_id
        auto_applied = False
        if self._general_test_auto_apply and chosen_id:
            self.context.settings.update(
                selected_zapret_general=chosen_id,
                general_autotest_done=True,
            )
            self._set_general_favorite(chosen_id, True)
            self.refresh_all()
            auto_applied = True
        self._general_test_auto_apply = False

        if not self._general_test_show_results:
            self._mark_dirty("dashboard", "components", "tray")
            return

        dialog = AppDialog(self, self.context, self._t("Результаты проверки", "Test results"))
        title = QLabel(self._t("Проверка завершена.", "Testing is complete."))
        title.setProperty("class", "title")
        dialog.body_layout.addWidget(title)
        summary = QTextEdit()
        summary.setReadOnly(True)
        summary.setMinimumHeight(260)
        summary.setPlainText(
            f"{self._t('Работают:', 'Working:')}\n"
            + ("\n".join(working) if working else self._t("Нет полностью работающих конфигураций.", "No fully working configurations."))
            + "\n\n"
            + (
                f"{self._t('Лучший результат:', 'Best result:')}\n{best_label} ({best_score}/{best_total})\n\n"
                if not working and best_label
                else ""
            )
            + (
                f"{self._t('Применено автоматически:', 'Applied automatically:')}\n"
                f"{self._format_general_option_label(next((item for item in self._sorted_general_options() if item['id'] == chosen_id), {'id': chosen_id, 'bundle': '', 'name': chosen_id}))}\n\n"
                if auto_applied and chosen_id
                else ""
            )
            + f"{self._t('Не работают или дают ошибку:', 'Not working or failed:')}\n"
            + ("\n".join(failed) if failed else self._t("Ошибок не обнаружено.", "No failed configurations."))
        )
        dialog.body_layout.addWidget(summary)
        row = QHBoxLayout()
        row.addStretch(1)
        ok_btn = QPushButton(self._t("Ок", "OK"))
        ok_btn.setProperty("class", "primary")
        ok_btn.clicked.connect(dialog.accept)
        row.addWidget(ok_btn)
        dialog.body_layout.addLayout(row)
        dialog.prepare_and_center()
        dialog.exec()

    def _set_badge(self, key: str, text: str, icon_name: str) -> None:
        badge = self._status_badges.get(key)
        if not badge:
            return
        badge.value_label.setText(text)
        badge.icon_label.setPixmap(self._icon(icon_name).pixmap(18, 18))

    def _show_info(self, title: str, text: str) -> None:
        dialog = AppDialog(self, self.context, title)
        label = QLabel(text)
        label.setWordWrap(True)
        dialog.body_layout.addWidget(label)
        row = QHBoxLayout()
        row.addStretch(1)
        ok_btn = QPushButton(self._t("Ок", "OK"))
        ok_btn.setProperty("class", "primary")
        ok_btn.clicked.connect(dialog.accept)
        row.addWidget(ok_btn)
        dialog.body_layout.addLayout(row)
        dialog.prepare_and_center()
        dialog.exec()

    def _show_warning(self, title: str, text: str) -> None:
        self._show_info(title, text)

    def _show_error(self, title: str, text: str) -> None:
        self._show_info(title, text)

    def _ask_text_value(self, title: str, text: str, placeholder: str = "") -> str:
        dialog = AppDialog(self, self.context, title)
        label = QLabel(text)
        label.setWordWrap(True)
        dialog.body_layout.addWidget(label)
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        dialog.body_layout.addWidget(field)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel_btn = QPushButton(self._t("Отмена", "Cancel"))
        ok_btn = QPushButton(self._t("Загрузить", "Load"))
        ok_btn.setProperty("class", "primary")
        cancel_btn.clicked.connect(dialog.reject)
        ok_btn.clicked.connect(dialog.accept)
        row.addWidget(cancel_btn)
        row.addWidget(ok_btn)
        dialog.body_layout.addLayout(row)
        dialog.prepare_and_center()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return ""
        return field.text().strip()

    def _ask_yes_no(self, title: str, text: str) -> bool:
        dialog = AppDialog(self, self.context, title)
        label = QLabel(text)
        label.setWordWrap(True)
        dialog.body_layout.addWidget(label)
        row = QHBoxLayout()
        row.addStretch(1)
        no_btn = QPushButton(self._t("Нет", "No"))
        yes_btn = QPushButton(self._t("Да", "Yes"))
        yes_btn.setProperty("class", "primary")
        no_btn.clicked.connect(dialog.reject)
        yes_btn.clicked.connect(dialog.accept)
        row.addWidget(no_btn)
        row.addWidget(yes_btn)
        dialog.body_layout.addLayout(row)
        dialog.prepare_and_center()
        return dialog.exec() == QDialog.DialogCode.Accepted

    def refresh_components(self, payload: object | None = None) -> None:
        components: list[ComponentDefinition] = []
        states: dict[str, ComponentState] = {}
        if isinstance(payload, dict):
            raw_components = payload.get("components", [])
            raw_states = payload.get("states", {})
            if isinstance(raw_components, list):
                for item in raw_components:
                    if isinstance(item, ComponentDefinition):
                        components.append(item)
                    elif isinstance(item, dict):
                        try:
                            components.append(ComponentDefinition(**item))
                        except Exception:
                            continue
            if isinstance(raw_states, dict):
                for key, item in raw_states.items():
                    if isinstance(item, ComponentState):
                        states[str(key)] = item
                    elif isinstance(item, dict):
                        try:
                            states[str(key)] = ComponentState(**item)
                        except Exception:
                            continue
            elif isinstance(raw_states, list):
                for item in raw_states:
                    if isinstance(item, ComponentState):
                        states[item.component_id] = item
                    elif isinstance(item, dict) and item.get("component_id"):
                        try:
                            parsed = ComponentState(**item)
                            states[parsed.component_id] = parsed
                        except Exception:
                            continue
        if not components:
            components = list(self._component_defs().values())
        if not states:
            states = self._component_states()
        self.components_list.clear()
        for component in components:
            state = states.get(component.id)
            status_text = state.status if state else "stopped"
            subtitle = f"{self._t('Версия', 'Version')}: {component.version} | {self._t('Включен', 'Enabled')}: {self._t('да', 'yes') if component.enabled else self._t('нет', 'no')} | {self._t('Автозапуск', 'Autostart')}: {self._t('да', 'yes') if component.autostart else self._t('нет', 'no')} | {self._t('Статус', 'Status')}: {status_text}"
            source = f"{self._t('Источник', 'Source')}: {component.source}"
            display_name = {"zapret": "Zapret", "tg-ws-proxy": "Tg-Ws-Proxy"}.get(component.id, component.name)
            item = QListWidgetItem(f"{display_name}\n{subtitle}\n{source}")
            item.setData(Qt.ItemDataRole.UserRole, component.id)
            item.setSizeHint(QSize(200, 70))
            self.components_list.addItem(item)
        if self._components_cards_layout is None:
            return

        while self._components_cards_layout.count():
            layout_item = self._components_cards_layout.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                widget.deleteLater()

        if not components:
            empty, empty_layout = self._card()
            empty_title = QLabel(self._t("Компоненты пока недоступны", "Components are currently unavailable"))
            empty_title.setProperty("class", "title")
            empty_text = QLabel(
                self._t(
                    "Данные ещё подгружаются. Попробуйте открыть вкладку ещё раз через секунду.",
                    "Data is still loading. Try opening this tab again in a second.",
                )
            )
            empty_text.setProperty("class", "muted")
            empty_text.setWordWrap(True)
            empty_layout.addWidget(empty_title)
            empty_layout.addWidget(empty_text)
            self._components_cards_layout.addWidget(empty)
            self._components_cards_layout.addStretch(1)
            return

        descriptions = {
            "zapret": self._t(
                "Классический способ обхода блокировок через DPI.",
                "A classic DPI-based bypass method for blocked services.",
            ),
            "tg-ws-proxy": self._t(
                "Локальный Telegram Proxy. Позволяет подключаться к Telegram в обход блокировок, маскируясь под обычный https-трафик.",
                "Local Telegram Proxy. Lets Telegram connect through restrictions by blending in with regular HTTPS traffic.",
            ),
        }
        icons = {"zapret": "component_zapret.svg", "tg-ws-proxy": "component_tg.svg"}

        for component in components:
            state = states.get(component.id)
            status_text, _status_icon = self._component_badge_state(component, state, any_running=False)
            display_name = {"zapret": "Zapret", "tg-ws-proxy": "Tg-Ws-Proxy"}.get(component.id, component.name)
            card, card_layout = self._card()
            card.setMinimumHeight(300)
            icon = QLabel()
            icon_size = 38 if component.id == "tg-ws-proxy" else 36
            icon.setPixmap(self._icon(icons.get(component.id, "components.svg")).pixmap(icon_size, icon_size))
            icon_row = QHBoxLayout()
            icon_row.setContentsMargins(0, 12, 0, 0)
            icon_row.setSpacing(0)
            icon_row.addWidget(icon, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            icon_row.addStretch(1)
            card_layout.addLayout(icon_row)

            title = QLabel(display_name)
            title.setProperty("class", "title")
            title.setWordWrap(True)
            card_layout.addWidget(title)

            desc = QLabel(descriptions.get(component.id, component.description))
            desc.setProperty("class", "muted")
            desc.setWordWrap(True)
            card_layout.addWidget(desc)

            details = QLabel(
                f"Author: Flowseal\n"
                f"{self._t('Status', 'Status')}: {status_text}\n"
                f"{self._t('Version', 'Version')}: {component.version}"
            )
            details.setProperty("class", "muted")
            details.setWordWrap(True)
            card_layout.addWidget(details)
            card_layout.addStretch(1)

            enabled_text = self._t("включен", "enabled") if component.enabled else self._t("выключен", "disabled")
            participation = QLabel(f"{self._t('Участие в ON/OFF', 'ON/OFF participation')}: {enabled_text}")
            participation.setWordWrap(True)
            card_layout.addWidget(participation)
            if component.id == "zapret":
                config_label = QLabel(self._t("Конфигурация Zapret", "Zapret Configuration"))
                config_label.setProperty("class", "muted")
                card_layout.addWidget(config_label)
                config_combo = ClickSelectComboBox()
                config_status = QLabel("")
                config_status.setProperty("class", "muted")
                config_status.hide()
                options = self._sorted_general_options()
                selected = self.context.settings.get().selected_zapret_general
                for option in options:
                    config_combo.addItem(self._format_general_option_label(option), option["id"])
                if config_combo.count() > 0:
                    picked_index = 0
                    for i in range(config_combo.count()):
                        if config_combo.itemData(i) == selected:
                            picked_index = i
                            break
                    config_combo.setCurrentIndex(picked_index)
                config_row = QHBoxLayout()
                config_row.setContentsMargins(0, 0, 0, 0)
                config_row.setSpacing(8)
                config_combo.currentIndexChanged.connect(
                    lambda _=0, combo=config_combo, status_label=config_status: self._on_general_selected_from_components(
                        str(combo.currentData() or ""),
                        combo,
                        status_label,
                    )
                )
                favorite_btn = QToolButton()
                favorite_btn.setProperty("class", "action")
                current_general = str(config_combo.currentData() or "")
                self._sync_general_favorite_button(current_general, favorite_btn)
                favorite_btn.clicked.connect(
                    lambda _=False, combo=config_combo, btn=favorite_btn: self._toggle_general_favorite_from_button(
                        str(combo.currentData() or ""),
                        btn,
                    )
                )
                config_combo.currentIndexChanged.connect(
                    lambda _=0, combo=config_combo, btn=favorite_btn: self._sync_general_favorite_button(
                        str(combo.currentData() or ""),
                        btn,
                    )
                )
                config_row.addWidget(config_combo, 1)
                config_row.addWidget(favorite_btn, 0)
                card_layout.addLayout(config_row)
                card_layout.addWidget(config_status)

            if component.id == "tg-ws-proxy":
                connect_btn = QPushButton(self._t("Подключить к Telegram", "Connect to Telegram"))
                connect_btn.clicked.connect(self._prompt_tg_proxy_connect)
                card_layout.addWidget(connect_btn)

            toggle_btn = QPushButton(
                self._t("Выключить компонент", "Disable component")
                if component.enabled
                else self._t("Включить компонент", "Enable component")
            )
            toggle_btn.setProperty("class", "danger" if component.enabled else "primary")
            toggle_btn.clicked.connect(lambda _=False, cid=component.id, btn=toggle_btn: self._toggle_component_card(cid, btn))
            card_layout.addWidget(toggle_btn)
            self._components_cards_layout.addWidget(card, 1)

    def _prompt_tg_proxy_connect(self) -> None:
        try:
            self.context.processes.prompt_telegram_proxy_link()
        except Exception as error:
            self._show_error(
                self._t("TG Proxy", "TG Proxy"),
                f"{self._t('Не удалось открыть запрос на подключение в Telegram.', 'Failed to open Telegram connection prompt.')}\n{error}",
            )

    def _refresh_mods_legacy(self) -> None:
        index = self.context.mods.fetch_index()
        installed = {item.id: item for item in self.context.mods.list_installed()}
        combined: list[tuple[str, str, str, str, str, str]] = []
        seen: set[str] = set()
        for item in index:
            seen.add(item.id)
            state = "not installed"
            if item.id in installed:
                state = "enabled" if installed[item.id].enabled else "installed"
            combined.append(
                (
                    item.id,
                    item.name,
                    item.description,
                    f"{self._t('Автор', 'Author')}: {item.author} | {self._t('Версия', 'Version')}: {item.version} | {self._t('Статус', 'Status')}: {state}",
                    f"{self._t('Категория', 'Category')}: {item.category}",
                    state,
                )
            )

        for mod_id, item in installed.items():
            if mod_id in seen:
                continue
            state = "enabled" if item.enabled else "installed"
            source_type = "zapret bundle" if item.source_type == "zapret_bundle" else item.source_type
            combined.append(
                (
                    mod_id,
                    mod_id,
                    self._t("Локальная модификация без пользовательского описания.", "Local modification without user description."),
                    f"{self._t('Локальный импорт', 'Local import')} | {self._t('Версия', 'Version')}: {item.version} | {self._t('Статус', 'Status')}: {state}",
                    f"{self._t('Тип', 'Type')}: {source_type}",
                    state,
                )
            )

        selected = self._selected_mod_id()
        self.mods_list.clear()
        for mod_id, name, description, subtitle, tags, _state in combined:
            row_item = QListWidgetItem(f"{name}\n{description}\n{subtitle}\n{tags}")
            row_item.setData(Qt.ItemDataRole.UserRole, mod_id)
            row_item.setSizeHint(QSize(200, 88))
            self.mods_list.addItem(row_item)
        if selected:
            for i in range(self.mods_list.count()):
                it = self.mods_list.item(i)
                if it.data(Qt.ItemDataRole.UserRole) == selected:
                    self.mods_list.setCurrentItem(it)
                    break

    def _toggle_mod_by_id(self, mod_id: str) -> None:
        self._submit_backend_task("toggle_mod", {"mod_id": mod_id}, action_id=f"mod:{mod_id}")

    def refresh_mods(self, payload: object | None = None) -> None:
        def _field(obj: object, name: str, default: object = "") -> object:
            if isinstance(obj, dict):
                return obj.get(name, default)
            return getattr(obj, name, default)

        index: list[object] = []
        installed: dict[str, object] = {}
        if isinstance(payload, dict):
            raw_index = payload.get("index", [])
            raw_installed = payload.get("installed", {})
            if isinstance(raw_index, list):
                index = list(raw_index)
            if isinstance(raw_installed, dict):
                installed = {str(key): value for key, value in raw_installed.items()}
            elif isinstance(raw_installed, list):
                for item in raw_installed:
                    item_id = str(_field(item, "id", "") or "")
                    if item_id:
                        installed[item_id] = item
        if not index:
            index = self.context.mods.fetch_index()
        if not installed:
            installed = {item.id: item for item in self.context.mods.list_installed()}
        combined: list[dict[str, str | bool]] = []
        seen: set[str] = set()
        for item in index:
            item_id = str(_field(item, "id", "") or "")
            if not item_id:
                continue
            seen.add(item_id)
            installed_item = installed.get(item_id)
            enabled = bool(_field(installed_item, "enabled", False)) if installed_item is not None else False
            state = "enabled" if enabled else ("installed" if item_id in installed else "not installed")
            combined.append(
                {
                    "id": item_id,
                    "name": str(_field(item, "name", item_id)),
                    "description": str(_field(item, "description", "") or self._t("Описание не указано.", "No description.")),
                    "subtitle": f"{self._t('Автор', 'Author')}: {str(_field(item, 'author', 'goshkow'))} | {self._t('Версия', 'Version')}: {str(_field(item, 'version', ''))}",
                    "state": state,
                    "enabled": enabled,
                    "changelog": str(_field(item, "changelog", "") or ""),
                }
            )

        for mod_id, item in installed.items():
            if mod_id in seen:
                continue
            combined.append(
                {
                    "id": mod_id,
                    "name": str(_field(item, "name", mod_id) or mod_id),
                    "description": str(_field(item, "description", "") or self._t("Локальная модификация без описания.", "Local mod without description.")),
                    "subtitle": f"{self._t('Автор', 'Author')}: {str(_field(item, 'author', 'goshkow') or 'goshkow')} | {self._t('Версия', 'Version')}: {str(_field(item, 'version', ''))}",
                    "state": "enabled" if bool(_field(item, "enabled", False)) else "installed",
                    "enabled": bool(_field(item, "enabled", False)),
                    "changelog": "",
                }
            )

        if not hasattr(self, "mods_cards_layout"):
            return

        enabled_count = sum(1 for mod in combined if bool(mod["enabled"]))
        if hasattr(self, "mods_summary_chip"):
            self.mods_summary_chip.setText(
                self._t(
                    f"Всего пакетов: {len(combined)}",
                    f"Total packs: {len(combined)}",
                )
            )
        if hasattr(self, "mods_enabled_chip"):
            self.mods_enabled_chip.setText(
                self._t(
                    f"Активно сейчас: {enabled_count}",
                    f"Active now: {enabled_count}",
                )
            )

        while self.mods_cards_layout.count():
            child = self.mods_cards_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()

        if not combined:
            empty, empty_layout = self._card()
            empty.setProperty("class", "modCard")
            title = QLabel(self._t("Пока пусто", "Nothing here yet"))
            title.setProperty("class", "title")
            text = QLabel(
                self._t(
                    "Добавьте архив, конфиг или папку с файлами, чтобы здесь появились модификации.",
                    "Add an archive, config, or folder with files and your modifications will appear here.",
                )
            )
            text.setProperty("class", "muted")
            text.setWordWrap(True)
            empty_layout.addWidget(title)
            empty_layout.addWidget(text)
            self.mods_cards_layout.addWidget(empty)
            self.mods_cards_layout.addStretch(1)
            return

        for mod in combined:
            mod_id = str(mod["id"])
            enabled = bool(mod["enabled"])
            state = str(mod["state"])
            if mod_id == "unified-by-goshkow":
                mod["description"] = self._t(
                    "Позволяет обойти блокировки самых популярных сервисов, включая игровые сервисы, социальные сети и другие платформы.",
                    "Helps bypass restrictions for the most popular services, including gaming platforms, social networks, and other services.",
                )

            card = QFrame()
            card.setProperty("class", "modCard")
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(16, 16, 16, 16)
            card_layout.setSpacing(16)

            icon_wrap = QFrame()
            icon_wrap.setProperty("class", "modIconWrap")
            icon_wrap.setFixedSize(54, 54)
            icon_row = QVBoxLayout(icon_wrap)
            icon_row.setContentsMargins(0, 0, 0, 0)
            icon_row.setSpacing(0)
            icon_label = QLabel()
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_label.setPixmap(self._icon("mods.svg").pixmap(24, 24))
            icon_row.addWidget(icon_label)
            card_layout.addWidget(icon_wrap, 0, Qt.AlignmentFlag.AlignTop)

            body = QVBoxLayout()
            body.setContentsMargins(0, 0, 0, 0)
            body.setSpacing(10)

            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(10)

            text_col = QVBoxLayout()
            text_col.setContentsMargins(0, 0, 0, 0)
            text_col.setSpacing(5)
            title = QLabel(str(mod["name"]))
            title.setProperty("class", "title")
            text_col.addWidget(title)

            state_map = {
                "enabled": self._t("Включена", "Enabled"),
                "installed": self._t("Выключена", "Disabled"),
                "not installed": self._t("Еще не подключена", "Not added yet"),
            }
            badge = QLabel(state_map.get(state, state))
            badge.setProperty("class", "modState")
            badge.setProperty("state", state)
            badge.setObjectName("ModStateBadge")
            text_col.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)
            head.addLayout(text_col, 1)

            actions = QHBoxLayout()
            actions.setContentsMargins(0, 0, 0, 0)
            actions.setSpacing(8)

            info_btn = QPushButton(self._t("Подробнее", "Details"))
            info_btn.setIcon(self._icon("files.svg"))
            info_btn.setIconSize(QSize(14, 14))
            info_btn.clicked.connect(lambda _=False, m=mod: self._show_info(str(m["name"]), f"{m['description']}\n\n{m['changelog']}"))
            self._attach_button_animations(info_btn)
            actions.addWidget(info_btn)

            toggle_btn = QPushButton(
                self._t("Выключить", "Disable")
                if enabled
                else self._t("Включить", "Enable")
            )
            toggle_btn.setProperty("class", "primary")
            toggle_btn.setIcon(self._icon("power.svg"))
            toggle_btn.setIconSize(QSize(14, 14))
            toggle_btn.clicked.connect(lambda _=False, mid=mod_id: self._toggle_mod_by_id(mid))
            self._attach_button_animations(toggle_btn)
            actions.addWidget(toggle_btn)

            remove_btn = QPushButton(self._t("Удалить", "Remove"))
            remove_btn.setProperty("class", "danger")
            remove_btn.setIcon(self._icon("window_close.svg"))
            remove_btn.setIconSize(QSize(14, 14))
            remove_btn.clicked.connect(lambda _=False, mid=mod_id: self.context.mods.remove(mid) or self.refresh_all())
            self._attach_button_animations(remove_btn)
            if mod_id != "unified-by-goshkow":
                actions.addWidget(remove_btn)
            head.addLayout(actions)
            body.addLayout(head)

            desc = QLabel(str(mod["description"]))
            desc.setWordWrap(True)
            desc.setProperty("class", "modBody")
            body.addWidget(desc)

            meta_row = QHBoxLayout()
            meta_row.setContentsMargins(0, 0, 0, 0)
            meta_row.setSpacing(8)
            for meta_text in str(mod["subtitle"]).split(" | "):
                meta = QLabel(meta_text)
                meta.setProperty("class", "modMeta")
                meta.setObjectName("ModMetaChip")
                meta_row.addWidget(meta)
            meta_row.addStretch(1)
            body.addLayout(meta_row)
            card_layout.addLayout(body, 1)
            self.mods_cards_layout.addWidget(card)

        self.mods_cards_layout.addStretch(1)

    def refresh_files(self, payload: object | None = None) -> None:
        if isinstance(payload, dict):
            if payload.get("collection_id") == self._current_file_collection:
                self._refresh_file_collection_view_with_values(list(payload.get("collection_values", [])))
            else:
                self._refresh_file_collection_view()
            records = payload.get("records", [])
        else:
            self._refresh_file_collection_view()
            records = self.context.files.list_files()
        selected = self._selected_file_path()
        self.files_list.clear()
        for record in records:
            row_item = QListWidgetItem(f"{record.relative_path}\n{self._t('Размер', 'Size')}: {record.size} {self._t('байт', 'bytes')}")
            row_item.setData(Qt.ItemDataRole.UserRole, record.path)
            row_item.setSizeHint(QSize(200, 54))
            self.files_list.addItem(row_item)
        if selected:
            for i in range(self.files_list.count()):
                it = self.files_list.item(i)
                if it.data(Qt.ItemDataRole.UserRole) == selected:
                    self.files_list.setCurrentItem(it)
                    break

    def refresh_logs(self, payload: object | None = None) -> None:
        if isinstance(payload, list):
            lines = payload
        else:
            entries = self.context.logging.read_entries()
            lines = [f"[{e.timestamp}] {e.level}: {e.message} {e.context}" for e in entries[-250:]]
        self.logs_text.setPlainText("\n".join(lines))



