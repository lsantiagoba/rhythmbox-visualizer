#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-time spectrum visualizer plugin for Rhythmbox 3.x."""

import math
import random
import time

import gi

# Gtk's draw signal passes a cairo.Context.  In a libpeas host the foreign
# converter is not necessarily registered merely by importing pycairo.
gi.require_foreign("cairo")
import cairo

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Peas", "1.0")
gi.require_version("RB", "3.0")

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gst, GstApp, Gtk, Peas


BANDS = 64
MIN_DB = -80.0
MODES = (
    "Bars",
    "Mirror Spectrum",
    "Wave",
    "Circular",
    "Pulse Rings",
    "Neon Tunnel",
    "Particles",
    "Frequency Mountains",
    "Digital Rain",
    "Classic GOOM",
    "Classic GOOM 2K1",
)
CURSOR_HIDE_DELAY_SECONDS = 5


class VisualizerWindow(Gtk.ApplicationWindow):
    """A lightweight Cairo renderer driven by GStreamer's spectrum messages."""

    def __init__(self, application, mode_changed_callback=None):
        super().__init__(application=application, title="Rhythmbox Visualizer")
        self.set_default_size(900, 520)
        self.set_icon_name("rhythmbox")
        self.values = [0.0] * BANDS
        self.peaks = [0.0] * BANDS
        self.target = [0.0] * BANDS
        self.mode = MODES[0]
        self.sensitivity = 1.0
        self.phase = 0.0
        self.particles = []
        self.rain = [random.random() for _ in range(40)]
        self.classic_frames = {}
        self.mode_changed_callback = mode_changed_callback
        self.inhibit_cookie = 0
        self.cursor_timeout_id = 0
        self.blank_cursor = None

        header = Gtk.HeaderBar(title="Visualizer", show_close_button=True)
        modes = Gtk.ComboBoxText()
        for name in MODES:
            modes.append_text(name)
        modes.set_active(0)
        modes.connect("changed", self._mode_changed)
        header.pack_start(modes)

        adjustment = Gtk.Adjustment(1.0, 0.4, 2.5, 0.1, 0.2, 0)
        sensitivity = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment)
        sensitivity.set_size_request(130, -1)
        sensitivity.set_tooltip_text("Sensitivity")
        sensitivity.set_draw_value(False)
        sensitivity.connect("value-changed", self._sensitivity_changed)
        header.pack_end(sensitivity)

        fullscreen = Gtk.Button.new_from_icon_name("view-fullscreen-symbolic", Gtk.IconSize.BUTTON)
        fullscreen.set_tooltip_text("Fullscreen (F11)")
        fullscreen.connect("clicked", lambda _button: self._toggle_fullscreen())
        header.pack_end(fullscreen)
        self.set_titlebar(header)

        self.canvas = Gtk.DrawingArea()
        self.canvas.connect("draw", self._draw)
        self.add(self.canvas)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("key-press-event", self._key_pressed)
        self.connect("map-event", self._window_shown)
        self.connect("unmap-event", self._window_hidden)
        self.connect("motion-notify-event", self._mouse_moved)
        GLib.timeout_add(16, self._animate)

    def _window_shown(self, _widget, _event):
        if not self.inhibit_cookie:
            application = self.get_application()
            self.inhibit_cookie = application.inhibit(
                self,
                Gtk.ApplicationInhibitFlags.IDLE,
                "Audio visualizer is open",
            )
        self._show_cursor()
        self._restart_cursor_timeout()
        return False

    def _window_hidden(self, _widget, _event):
        if self.inhibit_cookie:
            self.get_application().uninhibit(self.inhibit_cookie)
            self.inhibit_cookie = 0
        self._cancel_cursor_timeout()
        self._show_cursor()
        return False

    def _mouse_moved(self, _widget, _event):
        self._show_cursor()
        self._restart_cursor_timeout()
        return False

    def _restart_cursor_timeout(self):
        self._cancel_cursor_timeout()
        self.cursor_timeout_id = GLib.timeout_add_seconds(
            CURSOR_HIDE_DELAY_SECONDS, self._hide_cursor
        )

    def _cancel_cursor_timeout(self):
        if self.cursor_timeout_id:
            GLib.source_remove(self.cursor_timeout_id)
            self.cursor_timeout_id = 0

    def _hide_cursor(self):
        self.cursor_timeout_id = 0
        window = self.get_window()
        if window is not None and self.get_visible():
            if self.blank_cursor is None:
                self.blank_cursor = Gdk.Cursor.new_for_display(
                    window.get_display(), Gdk.CursorType.BLANK_CURSOR
                )
            window.set_cursor(self.blank_cursor)
        return False

    def _show_cursor(self):
        window = self.get_window()
        if window is not None:
            window.set_cursor(None)

    def set_spectrum(self, magnitudes):
        data = list(magnitudes or ())
        if not data:
            return
        # GStreamer may return a different band count; resample by index.
        for index in range(BANDS):
            source = min(len(data) - 1, int(index * len(data) / BANDS))
            normalized = (float(data[source]) - MIN_DB) / -MIN_DB
            self.target[index] = max(0.0, min(1.0, normalized * self.sensitivity))

    def _animate(self):
        if not self.get_visible():
            return True
        for index, target in enumerate(self.target):
            speed = 0.38 if target > self.values[index] else 0.10
            self.values[index] += (target - self.values[index]) * speed
            self.peaks[index] = max(self.values[index], self.peaks[index] - 0.008)
        self.phase += 0.018
        self._animate_particles()
        energy = sum(self.values) / BANDS
        for index in range(len(self.rain)):
            self.rain[index] = (self.rain[index] + 0.004 + energy * 0.025) % 1.25
        self.canvas.queue_draw()
        return True

    def _draw(self, widget, cr):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        cr.set_source_rgb(0.025, 0.035, 0.075)
        cr.paint()
        if self.mode in ("Classic GOOM", "Classic GOOM 2K1"):
            frame = self.classic_frames.get(self.mode)
            if frame is not None:
                self._draw_classic_frame(cr, width, height, frame)
            return False
        renderers = {
            "Bars": self._draw_bars,
            "Mirror Spectrum": self._draw_mirror,
            "Wave": self._draw_wave,
            "Circular": self._draw_circle,
            "Pulse Rings": self._draw_rings,
            "Neon Tunnel": self._draw_tunnel,
            "Particles": self._draw_particles,
            "Frequency Mountains": self._draw_mountains,
            "Digital Rain": self._draw_rain,
        }
        renderers.get(self.mode, self._draw_bars)(cr, width, height)
        return False

    def set_classic_frame(self, mode, frame):
        self.classic_frames[mode] = frame
        if self.mode == mode:
            self.canvas.queue_draw()

    def _draw_classic_frame(self, cr, width, height, frame):
        frame_width = frame.get_width()
        frame_height = frame.get_height()
        scale = min(width / frame_width, height / frame_height)
        draw_width = frame_width * scale
        draw_height = frame_height * scale
        cr.save()
        cr.translate((width - draw_width) / 2, (height - draw_height) / 2)
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, frame, 0, 0)
        cr.paint()
        cr.restore()

    def _colour(self, cr, value, alpha=1.0):
        cr.set_source_rgba(0.12 + value * 0.75, 0.85 - value * 0.30, 1.0, alpha)

    def _draw_bars(self, cr, width, height):
        gap = 3.0
        bar_width = max(1.0, width / BANDS - gap)
        for index, value in enumerate(self.values):
            x = index * width / BANDS + gap / 2
            bar_height = max(2.0, value * (height - 24))
            self._colour(cr, value)
            cr.rectangle(x, height - bar_height, bar_width, bar_height)
            cr.fill()
            self._colour(cr, self.peaks[index], 0.8)
            cr.rectangle(x, height - self.peaks[index] * (height - 24) - 3, bar_width, 2)
            cr.fill()

    def _draw_wave(self, cr, width, height):
        cr.set_line_width(4.0)
        for mirror in (-1, 1):
            for index, value in enumerate(self.values):
                x = index * width / (BANDS - 1)
                y = height / 2 + mirror * value * height * 0.42
                if index == 0:
                    cr.move_to(x, y)
                else:
                    cr.line_to(x, y)
            self._colour(cr, sum(self.values) / BANDS, 0.82)
            cr.stroke()

    def _draw_mirror(self, cr, width, height):
        gap = 2.0
        bar_width = max(1.0, width / BANDS - gap)
        for index, value in enumerate(self.values):
            x = index * width / BANDS + gap / 2
            extent = max(1.0, value * height * 0.46)
            self._colour(cr, value, 0.92)
            cr.rectangle(x, height / 2 - extent, bar_width, extent * 2)
            cr.fill()
        cr.set_source_rgba(0.7, 0.95, 1.0, 0.5)
        cr.rectangle(0, height / 2 - 1, width, 2)
        cr.fill()

    def _draw_circle(self, cr, width, height):
        cx, cy = width / 2, height / 2
        radius = min(width, height) * 0.19
        cr.set_line_width(max(2.0, radius / 55))
        for index, value in enumerate(self.values):
            angle = index * math.tau / BANDS - math.pi / 2 + self.phase * 0.15
            inner = radius
            outer = radius + value * min(width, height) * 0.31
            cr.move_to(cx + math.cos(angle) * inner, cy + math.sin(angle) * inner)
            cr.line_to(cx + math.cos(angle) * outer, cy + math.sin(angle) * outer)
            self._colour(cr, value, 0.9)
            cr.stroke()

    def _draw_rings(self, cr, width, height):
        cx, cy = width / 2, height / 2
        size = min(width, height)
        bass = sum(self.values[:10]) / 10
        for ring in range(10, 0, -1):
            band = min(BANDS - 1, ring * 5)
            pulse = self.values[band] * size * 0.045 + bass * size * 0.035
            radius = size * (0.045 + ring * 0.039) + pulse
            self._colour(cr, self.values[band], 0.18 + ring * 0.065)
            cr.set_line_width(1.5 + self.values[band] * 7)
            cr.arc(cx, cy, radius, 0, math.tau)
            cr.stroke()

    def _draw_tunnel(self, cr, width, height):
        cx = width / 2 + math.sin(self.phase * 1.7) * width * 0.06
        cy = height / 2 + math.cos(self.phase * 1.3) * height * 0.06
        size = min(width, height)
        energy = sum(self.values) / BANDS
        for layer in range(14, 0, -1):
            progress = ((layer / 14) + self.phase * (0.45 + energy)) % 1.0
            radius = 12 + progress * size * 0.72
            alpha = (1.0 - progress) * 0.75
            self._colour(cr, energy, alpha)
            cr.set_line_width(1 + (1.0 - progress) * 5)
            for corner in range(7):
                angle = corner * math.tau / 6 + self.phase * 0.12
                x = cx + math.cos(angle) * radius
                y = cy + math.sin(angle) * radius
                if corner == 0:
                    cr.move_to(x, y)
                else:
                    cr.line_to(x, y)
            cr.stroke()

    def _animate_particles(self):
        bass = sum(self.values[:12]) / 12
        if self.mode == "Particles" and bass > 0.08:
            for _ in range(min(5, 1 + int(bass * 6))):
                angle = random.random() * math.tau
                speed = 0.003 + random.random() * 0.009 + bass * 0.012
                self.particles.append([0.5, 0.5, math.cos(angle) * speed,
                                       math.sin(angle) * speed, 1.0, bass])
        for particle in self.particles:
            particle[0] += particle[2]
            particle[1] += particle[3]
            particle[4] -= 0.012
        self.particles = [p for p in self.particles if p[4] > 0 and
                          -0.1 < p[0] < 1.1 and -0.1 < p[1] < 1.1][-350:]

    def _draw_particles(self, cr, width, height):
        for x, y, _vx, _vy, life, energy in self.particles:
            radius = 2 + energy * 10 * life
            self._colour(cr, energy, life * 0.85)
            cr.arc(x * width, y * height, radius, 0, math.tau)
            cr.fill()

    def _draw_mountains(self, cr, width, height):
        layers = ((0.72, 0.24), (0.84, 0.42), (0.96, 0.72))
        for baseline, alpha in layers:
            cr.move_to(0, height)
            for index, value in enumerate(self.values):
                x = index * width / (BANDS - 1)
                shifted = self.values[(index + int(baseline * 17)) % BANDS]
                y = height * baseline - shifted * height * 0.42
                cr.line_to(x, y)
            cr.line_to(width, height)
            cr.close_path()
            self._colour(cr, sum(self.values) / BANDS, alpha)
            cr.fill()

    def _draw_rain(self, cr, width, height):
        columns = len(self.rain)
        column_width = width / columns
        for index, head in enumerate(self.rain):
            band = int(index * BANDS / columns)
            strength = self.values[band]
            if strength < 0.03:
                continue
            length = height * (0.08 + strength * 0.45)
            y = head * height
            cr.set_source_rgba(0.1, 1.0, 0.65, 0.16 + strength * 0.75)
            cr.rectangle(index * column_width + 1, y - length,
                         max(1, column_width - 3), length)
            cr.fill()
            cr.set_source_rgba(0.75, 1.0, 0.95, 0.9)
            cr.rectangle(index * column_width + 1, y - 2,
                         max(1, column_width - 3), 3)
            cr.fill()

    def _mode_changed(self, combo):
        self.mode = combo.get_active_text() or MODES[0]
        if self.mode_changed_callback is not None:
            self.mode_changed_callback(self.mode)

    def _sensitivity_changed(self, scale):
        self.sensitivity = scale.get_value()

    def _toggle_fullscreen(self):
        state = self.get_window().get_state() if self.get_window() else 0
        if state & Gdk.WindowState.FULLSCREEN:
            self.unfullscreen()
        else:
            self.fullscreen()

    def _key_pressed(self, _widget, event):
        if event.keyval == Gdk.KEY_F11:
            self._toggle_fullscreen()
            return True
        if event.keyval == Gdk.KEY_Escape:
            self.unfullscreen()
            return True
        return False


class VisualizerPlugin(GObject.Object, Peas.Activatable):
    __gtype_name__ = "RhythmboxVisualizerPlugin"

    object = GObject.Property(type=GObject.Object)

    def __init__(self):
        super().__init__()
        self.window = None
        self.player = None
        self.spectra = []
        self.classic_sinks = []
        self.classic_valves = {}
        self.classic_frame_pending = {
            "Classic GOOM": False,
            "Classic GOOM 2K1": False,
        }
        self.filter = None
        self.stream_filter_id = None
        self.bus_connections = []
        self.action = None
        self.last_analysis = 0.0

    def do_activate(self):
        shell = self.object
        app = shell.props.application
        self.window = VisualizerWindow(app, self._classic_mode_changed)
        self.window.connect("delete-event", self._hide_window)

        self.action = Gio.SimpleAction.new("show-visualizer", None)
        self.action.connect("activate", self._show_window)
        app.add_action(self.action)
        app.add_plugin_menu_item(
            "view",
            "show-visualizer",
            Gio.MenuItem.new(label="Visualizer", detailed_action="app.show-visualizer"),
        )

        Gst.init(None)
        self.player = shell.props.shell_player.props.player
        if GObject.signal_lookup("get-stream-filters", self.player):
            self.stream_filter_id = self.player.connect(
                "get-stream-filters", self._create_stream_filters
            )
        else:
            self.filter = self._new_spectrum()
            self.player.add_filter(self.filter)

    def do_deactivate(self):
        app = self.object.props.application
        app.remove_plugin_menu_item("view", "show-visualizer")
        app.remove_action("show-visualizer")
        if self.stream_filter_id is not None and self.player is not None:
            self.player.disconnect(self.stream_filter_id)
        if self.filter is not None and self.player is not None:
            self.player.remove_filter(self.filter)
        if self.window is not None:
            self.window.destroy()
        self.bus_connections.clear()
        self.spectra.clear()
        self.classic_sinks.clear()
        self.classic_valves.clear()
        self.window = self.player = self.filter = self.action = None

    def _new_spectrum(self):
        analyzer_bin = Gst.Bin.new(None)
        convert = Gst.ElementFactory.make("audioconvert", None)
        capsfilter = Gst.ElementFactory.make("capsfilter", None)
        tee = Gst.ElementFactory.make("tee", None)
        passthrough_queue = Gst.ElementFactory.make("queue", None)
        spectrum = Gst.ElementFactory.make("spectrum", None)
        if not all((analyzer_bin, convert, capsfilter, tee, passthrough_queue, spectrum)):
            raise RuntimeError("Could not create the GStreamer audio analyzer")

        # A known sample format lets the pad probe read samples directly.  We
        # deliberately preserve the channel count and let downstream convert
        # it again if needed.
        capsfilter.set_property("caps", Gst.Caps.from_string("audio/x-raw,format=F32LE"))
        spectrum.set_property("bands", BANDS)
        spectrum.set_property("threshold", MIN_DB)
        spectrum.set_property("interval", 40 * Gst.MSECOND)
        spectrum.set_property("post-messages", False)
        spectrum.set_property("message-magnitude", True)
        analyzer_bin.add(convert)
        analyzer_bin.add(capsfilter)
        analyzer_bin.add(tee)
        analyzer_bin.add(passthrough_queue)
        analyzer_bin.add(spectrum)
        if (not convert.link(capsfilter) or not capsfilter.link(tee) or
                not tee.link(passthrough_queue) or not passthrough_queue.link(spectrum)):
            raise RuntimeError("Could not link the audio analyzer")

        for element_name, mode in (("goom", "Classic GOOM"),
                                   ("goom2k1", "Classic GOOM 2K1")):
            self._add_classic_branch(analyzer_bin, tee, element_name, mode)

        analyzer_bin.add_pad(Gst.GhostPad.new("sink", convert.get_static_pad("sink")))
        analyzer_bin.add_pad(Gst.GhostPad.new("src", spectrum.get_static_pad("src")))
        capsfilter.get_static_pad("src").add_probe(
            Gst.PadProbeType.BUFFER, self._audio_buffer
        )
        self.spectra.append(spectrum)
        return analyzer_bin

    def _add_classic_branch(self, analyzer_bin, tee, element_name, mode):
        valve = Gst.ElementFactory.make("valve", None)
        queue = Gst.ElementFactory.make("queue", None)
        convert = Gst.ElementFactory.make("audioconvert", None)
        audio_caps = Gst.ElementFactory.make("capsfilter", None)
        effect = Gst.ElementFactory.make(element_name, None)
        video_convert = Gst.ElementFactory.make("videoconvert", None)
        video_scale = Gst.ElementFactory.make("videoscale", None)
        video_caps = Gst.ElementFactory.make("capsfilter", None)
        sink = Gst.ElementFactory.make("appsink", None)
        elements = (valve, queue, convert, audio_caps, effect, video_convert,
                    video_scale, video_caps, sink)
        if not all(elements):
            raise RuntimeError("Could not create the classic %s visualizer" % element_name)
        queue.set_property("leaky", 2)
        queue.set_property("max-size-buffers", 3)
        queue.set_property("max-size-bytes", 0)
        queue.set_property("max-size-time", 0)
        valve.set_property("drop", self.window is None or self.window.mode != mode)
        audio_caps.set_property(
            "caps", Gst.Caps.from_string("audio/x-raw,format=S16LE,channels=2")
        )
        video_caps.set_property(
            "caps", Gst.Caps.from_string(
                "video/x-raw,format=RGB,width=1280,height=720,framerate=30/1"
            ),
        )
        sink.set_property("emit-signals", True)
        sink.set_property("async", False)
        # Audio playback already paces this branch; avoiding a second clock wait
        # keeps both classic engines from blocking each other's tee queues.
        sink.set_property("sync", False)
        sink.set_property("drop", True)
        sink.set_property("max-buffers", 1)
        sink.connect("new-sample", self._classic_sample, mode)
        for element in elements:
            analyzer_bin.add(element)
        if not tee.link(valve):
            raise RuntimeError("Could not connect the classic visualizer branch")
        for left, right in zip(elements, elements[1:]):
            if not left.link(right):
                raise RuntimeError("Could not link the classic %s visualizer" % element_name)
        self.classic_sinks.append(sink)
        self.classic_valves[mode] = valve

    def _classic_mode_changed(self, active_mode):
        for mode, valve in self.classic_valves.items():
            valve.set_property("drop", mode != active_mode)

    def _classic_sample(self, sink, mode):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        # Both legacy engines live in the pipeline, but only the selected one
        # should cross into GTK.  Keeping at most one pending frame prevents
        # the GLib main loop from being flooded by decoded video frames.
        if (self.window is None or self.window.mode != mode or
                self.classic_frame_pending[mode]):
            return Gst.FlowReturn.OK
        caps = sample.get_caps()
        structure = caps.get_structure(0)
        width = structure.get_value("width")
        height = structure.get_value("height")
        buffer = sample.get_buffer()
        success, mapping = buffer.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.OK
        try:
            pixels = GLib.Bytes.new(bytes(mapping.data))
        finally:
            buffer.unmap(mapping)
        frame = GdkPixbuf.Pixbuf.new_from_bytes(
            pixels, GdkPixbuf.Colorspace.RGB, False, 8,
            width, height, width * 3,
        )
        self.classic_frame_pending[mode] = True
        GLib.idle_add(self._deliver_classic_frame, mode, frame)
        return Gst.FlowReturn.OK

    def _deliver_classic_frame(self, mode, frame):
        self.classic_frame_pending[mode] = False
        if self.window is not None:
            self.window.set_classic_frame(mode, frame)
        return False

    def _create_stream_filters(self, _player, _uri):
        return [self._new_spectrum()]

    def _audio_buffer(self, pad, probe_info):
        """Calculate selected DFT bins without relying on pipeline bus messages."""
        now = time.monotonic()
        if now - self.last_analysis < 0.04:
            return Gst.PadProbeReturn.OK
        self.last_analysis = now
        buffer = probe_info.get_buffer()
        if buffer is None:
            return Gst.PadProbeReturn.OK
        success, mapping = buffer.map(Gst.MapFlags.READ)
        if not success:
            return Gst.PadProbeReturn.OK
        try:
            raw = memoryview(mapping.data)
            usable = len(raw) - (len(raw) % 4)
            floats = raw[:usable].cast("f")
            caps = pad.get_current_caps()
            structure = caps.get_structure(0) if caps and caps.get_size() else None
            channels = structure.get_value("channels") if structure else 1
            channels = max(1, int(channels or 1))
            frame_count = min(1024, len(floats) // channels)
            if frame_count < 64:
                return Gst.PadProbeReturn.OK
            start = len(floats) - frame_count * channels
            samples = [
                sum(float(floats[start + frame * channels + ch]) for ch in range(channels))
                / channels
                for frame in range(frame_count)
            ]
            magnitudes = self._frequency_bands(samples)
        finally:
            buffer.unmap(mapping)
        GLib.idle_add(self._deliver_spectrum, magnitudes)
        return Gst.PadProbeReturn.OK

    def _frequency_bands(self, samples):
        """Return logarithmically spaced magnitudes in GStreamer-like dB."""
        count = len(samples)
        result = []
        max_bin = max(2, count // 2 - 1)
        for band in range(BANDS):
            ratio = band / max(1, BANDS - 1)
            frequency_bin = max(1, min(max_bin, round(max_bin ** ratio)))
            coefficient = 2.0 * math.cos(math.tau * frequency_bin / count)
            previous = previous2 = 0.0
            for sample in samples:
                current = sample + coefficient * previous - previous2
                previous2, previous = previous, current
            power = previous2 * previous2 + previous * previous - coefficient * previous * previous2
            amplitude = max(1e-8, math.sqrt(max(0.0, power)) / count)
            result.append(max(MIN_DB, min(0.0, 20.0 * math.log10(amplitude))))
        return result

    def _deliver_spectrum(self, magnitudes):
        if self.window is not None:
            self.window.set_spectrum(magnitudes)
        return False

    def _show_window(self, _action, _parameter):
        self.window.show_all()
        self.window.present()

    def _hide_window(self, window, _event):
        window.hide()
        return True
