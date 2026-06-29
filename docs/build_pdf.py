"""Build the Compa standalone User Manual PDF.

Reads screenshots + logos from this `docs/` directory; writes the PDF
to wherever --output points (defaults to docs/Compa_User_Manual.pdf).

Usage:
    python docs/build_pdf.py
    python docs/build_pdf.py --version 0.2.0 --output Compa_User_Manual.pdf

CI: see .github/workflows/build-os-image.yml — the build-pdf step
runs this and the softprops/action-gh-release step attaches the
resulting file to the GitHub Release.
"""

import argparse
import os
import subprocess
from pathlib import Path
from datetime import date

from PIL import Image as PILImage
from reportlab.lib.colors import HexColor, black, white, Color
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, PageBreak,
    Image as RLImage, Table, TableStyle, KeepTogether, NextPageTemplate,
    HRFlowable, ListFlowable, ListItem, FrameBreak, Preformatted,
)
from reportlab.platypus.tableofcontents import TableOfContents


# --- paths ---------------------------------------------------------------

DOCS_DIR = Path(__file__).resolve().parent
REPO_ROOT = DOCS_DIR.parent
SHOTS = DOCS_DIR / "screenshots"
LOGO = DOCS_DIR / "logo"


def _resolve_version() -> str:
    """Priority: --version arg > COMPA_VERSION env > latest git tag > 'dev'."""
    env_v = os.environ.get("COMPA_VERSION", "").strip()
    if env_v:
        return env_v.lstrip("v")
    try:
        v = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "describe", "--tags", "--abbrev=0"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        if v:
            return v.lstrip("v")
    except Exception:
        pass
    return "dev"


def _parse_args():
    p = argparse.ArgumentParser(description="Build the Compa User Manual PDF.")
    p.add_argument("--version", default=None,
                   help="Version string for the cover (e.g. 0.1.1). "
                        "Defaults to the latest git tag, or 'dev'.")
    p.add_argument("--output", default=None,
                   help="Output PDF path. Defaults to docs/Compa_User_Manual.pdf.")
    return p.parse_args()


_ARGS = _parse_args()
VERSION = (_ARGS.version or _resolve_version()).lstrip("v")
OUT = Path(_ARGS.output) if _ARGS.output else (DOCS_DIR / "Compa_User_Manual.pdf")
OUT.parent.mkdir(parents=True, exist_ok=True)


# --- brand ---------------------------------------------------------------

NEON_RED = HexColor("#FF003E")
INK = HexColor("#111111")
SOFT = HexColor("#444444")
RULE = HexColor("#DDDDDD")
PAGE_BG = HexColor("#0A0A0A")  # cover bg
ACCENT_P6 = HexColor("#F1B500")
ACCENT_SP = HexColor("#1FBFA8")
ACCENT_FORCE = HexColor("#E14B3F")


# --- page geometry -------------------------------------------------------

PAGE_W, PAGE_H = LETTER
MARGIN = 0.7 * inch
CONTENT_W = PAGE_W - 2 * MARGIN
CONTENT_H = PAGE_H - 2 * MARGIN


# --- styles --------------------------------------------------------------

def make_styles():
    s = getSampleStyleSheet()

    base = "Helvetica"
    bold = "Helvetica-Bold"
    mono = "Courier"

    styles = {
        "Cover_Title": ParagraphStyle(
            "Cover_Title", parent=s["Title"], fontName=bold, fontSize=64,
            leading=70, alignment=TA_CENTER, textColor=white,
            spaceBefore=0, spaceAfter=0,
        ),
        "Cover_Sub": ParagraphStyle(
            "Cover_Sub", parent=s["Normal"], fontName=base, fontSize=18,
            leading=24, alignment=TA_CENTER, textColor=white,
        ),
        "Cover_Tag": ParagraphStyle(
            "Cover_Tag", parent=s["Normal"], fontName=bold, fontSize=11,
            leading=16, alignment=TA_CENTER, textColor=NEON_RED,
        ),
        "Cover_Foot": ParagraphStyle(
            "Cover_Foot", parent=s["Normal"], fontName=base, fontSize=9,
            leading=12, alignment=TA_CENTER, textColor=HexColor("#888888"),
        ),
        "H1": ParagraphStyle(
            "H1", parent=s["Heading1"], fontName=bold, fontSize=24, leading=30,
            textColor=NEON_RED, spaceBefore=0, spaceAfter=10,
            keepWithNext=1,
        ),
        "H2": ParagraphStyle(
            "H2", parent=s["Heading2"], fontName=bold, fontSize=15, leading=20,
            textColor=INK, spaceBefore=14, spaceAfter=6, keepWithNext=1,
        ),
        "H3": ParagraphStyle(
            "H3", parent=s["Heading3"], fontName=bold, fontSize=11, leading=15,
            textColor=NEON_RED, spaceBefore=10, spaceAfter=4, keepWithNext=1,
        ),
        "Body": ParagraphStyle(
            "Body", parent=s["Normal"], fontName=base, fontSize=10, leading=14,
            textColor=INK, spaceBefore=0, spaceAfter=6, alignment=TA_LEFT,
        ),
        "BodyJ": ParagraphStyle(
            "BodyJ", parent=s["Normal"], fontName=base, fontSize=10, leading=14,
            textColor=INK, spaceBefore=0, spaceAfter=6, alignment=TA_JUSTIFY,
        ),
        "Bullet": ParagraphStyle(
            "Bullet", parent=s["Normal"], fontName=base, fontSize=10, leading=14,
            textColor=INK, spaceBefore=0, spaceAfter=2, leftIndent=14,
            bulletIndent=2,
        ),
        "Caption": ParagraphStyle(
            "Caption", parent=s["Normal"], fontName=base, fontSize=8.5,
            leading=11, textColor=SOFT, alignment=TA_CENTER,
            spaceBefore=4, spaceAfter=10,
        ),
        "Code": ParagraphStyle(
            "Code", parent=s["Code"], fontName=mono, fontSize=9, leading=12,
            textColor=INK, backColor=HexColor("#F4F4F6"),
            borderPadding=6, leftIndent=4, rightIndent=4,
            spaceBefore=4, spaceAfter=8,
        ),
        "Lead": ParagraphStyle(
            "Lead", parent=s["Normal"], fontName=base, fontSize=12, leading=18,
            textColor=INK, alignment=TA_LEFT, spaceAfter=10,
        ),
        "Note": ParagraphStyle(
            "Note", parent=s["Normal"], fontName=base, fontSize=9.5, leading=13,
            textColor=SOFT, leftIndent=10, rightIndent=10,
            backColor=HexColor("#FFF6F8"), borderPadding=8,
            spaceBefore=6, spaceAfter=10,
        ),
        "TocH1": ParagraphStyle(
            "TocH1", parent=s["Normal"], fontName=bold, fontSize=12, leading=18,
            textColor=INK, leftIndent=0, spaceBefore=4,
        ),
        "TocH2": ParagraphStyle(
            "TocH2", parent=s["Normal"], fontName=base, fontSize=10, leading=14,
            textColor=SOFT, leftIndent=18,
        ),
    }
    return styles


STYLES = make_styles()


# --- doc / page templates ------------------------------------------------

class CompaDocTemplate(BaseDocTemplate):
    def __init__(self, filename, **kw):
        super().__init__(filename, **kw)
        self._toc_entries = []

    def afterFlowable(self, flowable):
        if hasattr(flowable, "_toc_level"):
            level = flowable._toc_level
            text = flowable.getPlainText()
            self.notify("TOCEntry", (level, text, self.page))


def cover_page(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(PAGE_BG)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    # neon stripe
    canvas.setFillColor(NEON_RED)
    canvas.rect(0, PAGE_H - 0.3 * inch, PAGE_W, 0.05 * inch, fill=1, stroke=0)
    canvas.rect(0, 0.3 * inch, PAGE_W, 0.05 * inch, fill=1, stroke=0)
    canvas.restoreState()


def body_page(canvas, doc):
    canvas.saveState()
    # header rule
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, PAGE_H - 0.5 * inch, PAGE_W - MARGIN, PAGE_H - 0.5 * inch)
    # header text — left: section, right: COMPA
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(SOFT)
    canvas.drawString(MARGIN, PAGE_H - 0.4 * inch, "COMPA  ·  USER MANUAL")
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 0.4 * inch,
                           f"v{VERSION}  ·  by RARE DATA")
    # footer rule
    canvas.line(MARGIN, 0.55 * inch, PAGE_W - MARGIN, 0.55 * inch)
    # footer text
    canvas.drawString(MARGIN, 0.4 * inch,
                      "© 2026 Rare Data LLC. All rights reserved.")
    canvas.drawRightString(PAGE_W - MARGIN, 0.4 * inch, f"{doc.page}")
    canvas.restoreState()


# --- helpers -------------------------------------------------------------

def heading(text, style_name, level=None, anchor=None):
    style = STYLES[style_name]
    p = Paragraph(text, style)
    if level is not None:
        p._toc_level = level
    if anchor:
        p._bookmarkName = anchor
    return p


def H1(text, anchor=None):
    return heading(text, "H1", level=0, anchor=anchor)


def H2(text):
    return heading(text, "H2", level=1)


def H3(text):
    return heading(text, "H3")


def P(text):
    return Paragraph(text, STYLES["Body"])


def Lead(text):
    return Paragraph(text, STYLES["Lead"])


def Code(text):
    # Preformatted preserves whitespace and line breaks (no HTML parsing).
    # Wrap in a 1-cell table to render the soft background reliably.
    pre_style = ParagraphStyle(
        "CodePre", parent=STYLES["Code"],
        fontName="Courier", fontSize=8.8, leading=11,
        spaceBefore=0, spaceAfter=0, leftIndent=0, rightIndent=0,
        backColor=None,
    )
    pre = Preformatted(text, pre_style)
    t = Table([[pre]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#F4F4F6")),
        ("BOX", (0, 0), (-1, -1), 0.4, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return KeepTogether([Spacer(1, 4), t, Spacer(1, 8)])


def Note(text):
    return Paragraph(text, STYLES["Note"])


def Bullets(items):
    flows = []
    for it in items:
        flows.append(Paragraph(f"• {it}", STYLES["Bullet"]))
    return flows


def Shot(name, caption=None, width_in=6.5):
    """Insert a screenshot, scaled to width_in inches preserving aspect ratio."""
    path = SHOTS / name
    if not path.exists():
        return Paragraph(f"<i>[missing: {name}]</i>", STYLES["Body"])
    with PILImage.open(path) as im:
        w, h = im.size
    target_w = width_in * inch
    target_h = target_w * h / w
    img = RLImage(str(path), width=target_w, height=target_h)
    img.hAlign = "CENTER"
    flows = [img]
    if caption:
        flows.append(Paragraph(caption, STYLES["Caption"]))
    else:
        flows.append(Spacer(1, 8))
    return KeepTogether(flows)


def TwoUpShots(left_name, right_name, left_cap, right_cap):
    cell_w = (CONTENT_W - 0.2 * inch) / 2
    def cell(name, cap):
        path = SHOTS / name
        if not path.exists():
            return [Paragraph(f"[missing: {name}]", STYLES["Body"])]
        with PILImage.open(path) as im:
            w, h = im.size
        tw = cell_w
        th = tw * h / w
        img = RLImage(str(path), width=tw, height=th)
        return [img, Spacer(1, 4),
                Paragraph(cap, STYLES["Caption"])]
    data = [[cell(left_name, left_cap), cell(right_name, right_cap)]]
    t = Table(data, colWidths=[cell_w, cell_w], hAlign="CENTER")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return KeepTogether(t)


def DataTable(rows, widths=None, header=True):
    if widths is None:
        cols = len(rows[0])
        widths = [CONTENT_W / cols] * cols
    # wrap cells in Paragraph for wrapping
    body_st = ParagraphStyle("tcell", parent=STYLES["Body"], fontSize=9, leading=12,
                             spaceAfter=0)
    head_st = ParagraphStyle("thead", parent=STYLES["Body"], fontSize=9, leading=12,
                             fontName="Helvetica-Bold", textColor=white, spaceAfter=0)
    pdata = []
    for i, row in enumerate(rows):
        st = head_st if (header and i == 0) else body_st
        pdata.append([Paragraph(str(c), st) for c in row])
    t = Table(pdata, colWidths=widths, hAlign="LEFT")
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), INK),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ]
    t.setStyle(TableStyle(style))
    return t


def HR():
    return HRFlowable(width="100%", thickness=0.5, color=RULE,
                      spaceBefore=8, spaceAfter=8)


# --- cover ---------------------------------------------------------------

def build_cover():
    flows = []
    # Spacer
    flows.append(Spacer(1, 0.9 * inch))

    # Hero logo
    logo_path = LOGO / "compa_logo_hero.png"
    with PILImage.open(logo_path) as im:
        w, h = im.size
    target_w = 6.0 * inch
    target_h = target_w * h / w
    img = RLImage(str(logo_path), width=target_w, height=target_h)
    img.hAlign = "CENTER"
    flows.append(img)

    flows.append(Spacer(1, 0.4 * inch))

    flows.append(Paragraph("USER MANUAL", STYLES["Cover_Title"]))
    flows.append(Spacer(1, 0.15 * inch))
    flows.append(Paragraph(
        "Touchscreen companion for the Roland SP-404 MKII and P-6.<br/>"
        "Multi-device hub. Live sampler, sequencer, kit builder.<br/>"
        "Push 2 control deck.",
        STYLES["Cover_Sub"]))
    flows.append(Spacer(1, 0.5 * inch))
    flows.append(Paragraph("by RARE DATA", STYLES["Cover_Tag"]))

    # Rare Data logo (lives at docs/raredata_logo.png, not docs/logo/).
    rd_logo = DOCS_DIR / "raredata_logo.png"
    if rd_logo.exists():
        flows.append(Spacer(1, 0.22 * inch))
        with PILImage.open(rd_logo) as im:
            rw, rh = im.size
        rd_w = 1.5 * inch
        rd_h = rd_w * rh / rw
        rd_img = RLImage(str(rd_logo), width=rd_w, height=rd_h)
        rd_img.hAlign = "CENTER"
        flows.append(rd_img)
        flows.append(Spacer(1, 1.0 * inch))
    else:
        flows.append(Spacer(1, 1.6 * inch))

    flows.append(Paragraph(
        f"Version {VERSION} &nbsp;·&nbsp; {date.today().isoformat()}",
        STYLES["Cover_Foot"]))
    flows.append(Spacer(1, 4))
    flows.append(Paragraph(
        "© 2026 Rare Data LLC. All rights reserved.",
        STYLES["Cover_Foot"]))

    return flows


# --- TOC -----------------------------------------------------------------

def build_toc():
    toc = TableOfContents()
    toc.levelStyles = [STYLES["TocH1"], STYLES["TocH2"]]
    return [
        H1("Contents"),
        Spacer(1, 8),
        toc,
        PageBreak(),
    ]


# --- content sections ----------------------------------------------------

def section_intro():
    flows = [
        H1("Welcome", anchor="welcome"),
        Lead(
            "Compa is a touchscreen companion for the Roland SP-404 MKII and "
            "the Roland AIRA Compact P-6. It runs on a Raspberry Pi and turns "
            "the two grooveboxes into a tighter, more playable setup &mdash; "
            "direct control over both devices from one screen, a chromatic "
            "keyboard that plays either one melodically, live recording with "
            "a 60-second recall buffer, and a full kit-building pipeline that "
            "exports to Akai MPC (.xpm), Akai Force, and Ableton Live (.adg)."
        ),
        P(
            "The workflow is unified: <b>record anything &rarr; slice it &rarr; "
            "build a kit &rarr; push it to your MPC/Force or Ableton.</b> The "
            "SP-404 and P-6 stay at the center of the setup. Compa adds the "
            "parts they're missing &mdash; a real screen, cross-device routing, "
            "sample transfer, and deep external MIDI controller support."
        ),
        P(
            "No desktop environment. No web browser. Just a direct touchscreen "
            "UI built for live use with your fingers."
        ),
        Spacer(1, 6),
        H2("What's in the box (the screen, anyway)"),
    ]
    flows += Bullets([
        "<b>Multi-device hub</b> — connect up to 3 USB devices simultaneously with hot-plug, audio routing, and MIDI clock relay.",
        "<b>Recording &amp; capture</b> — record from any device, 60-second recall buffer, threshold and auto-record modes.",
        "<b>Sample editing</b> — visual waveform slicer, snap-to-zero-crossing trim, normalize, auto-slice.",
        "<b>Format conversion</b> — convert between P-6, SP-404 MK2, MPC/Force, and Ableton Drum Rack with a single tap.",
        "<b>Kit builder</b> — 4&times;4 pad grid &times; 8 banks (128 pads), smart drum import, export XPM and ADG.",
        "<b>Internet radio</b> — 137 stations, live waveform visualizer, 60-second capture buffer.",
        "<b>Pattern sequencing</b> — pattern grid, chain/song mode, Pi-side step sequencer with chromatic / ghost / EXT SOURCE rows.",
        "<b>LFO automation</b> — sine, triangle, saw, square, random, S&amp;H on any MIDI CC.",
        "<b>Push 2 control deck</b> — drives the 960&times;160 RGB display and 8&times;8 pad grid directly from the Pi. No Live, no host.",
        "<b>Ableton Link</b> — joins any Link session on your network and stays tempo-locked.",
        "<b>Searchable manuals</b> — Compa, P-6, and SP-404 MK2 references on-device.",
    ])
    flows += [
        Spacer(1, 8),
        H2("How to read this manual"),
        P(
            "Each numbered screen in Compa has its own chapter. Hardware "
            "controllers (Push 2, Midi Fighter Twister, ATOM SQ, generic "
            "MIDI keyboards) get their own chapters too. Workflows toward "
            "the back of the book chain screens together for the common "
            "tasks &mdash; record &rarr; slice &rarr; build a kit &rarr; "
            "push to MPC/Force or Ableton."
        ),
        P(
            "If you'd rather read it on-device, every word in this PDF is "
            "also searchable from the Help screen on Compa itself."
        ),
        PageBreak(),
    ]
    return flows


def section_at_a_glance():
    flows = [
        H1("Compa at a glance", anchor="glance"),
        Shot("compa_session.png",
             "The Session screen — Compa's main dashboard. One playing card "
             "per connected device with live oscilloscope, BPM, transport, "
             "and recall-buffer status."),
        H2("The big idea"),
        P(
            "The SP-404 MKII and P-6 are great. They're also small, "
            "menu-heavy, and don't talk to each other on their own. Compa "
            "is the missing piece that ties them together &mdash; a single "
            "touchscreen that sees both devices, routes audio between them, "
            "shares a master clock, and gives you a real waveform editor "
            "and kit builder for the samples you record."
        ),
        H2("Who Compa is for"),
    ]
    flows += Bullets([
        "SP-404 and P-6 owners who want one screen for both.",
        "Producers using an Akai MPC or Force as their main beat machine but recording into the SP/P-6 first.",
        "Anyone running a USB MIDI keyboard or controller and tired of the SP-404's chromatic-mode menu dance.",
        "Push 2 owners who don't want to drag a laptop on stage.",
    ])
    flows += [
        Spacer(1, 8),
        H2("The cross-device flow"),
        P(
            "Compa is built around one continuous flow that works across "
            "any combination of connected devices:"
        ),
        Code(
            "RECORD          SAMPLE           KIT BUILDER        XFER\n"
            "------          ------           -----------        ----\n"
            "Capture audio   Load WAV         Drop slices        Push kit to\n"
            "from any     -> Slice it up   -> onto 128-pad    -> MPC/Force via\n"
            "device or       Trim, normalize  grid. Auto-detect   USB, or\n"
            "radio stream    Export slices     drum types.         export ADG."
        ),
        P(
            "Record from your SP-404. Slice the recording. Build a kit "
            "from the slices (auto-detecting kick, snare, hat). Push the "
            "finished kit to your MPC Force as an XPM drum program, or "
            "export an Ableton Drum Rack. Without leaving Compa."
        ),
        PageBreak(),
    ]
    return flows


def section_hardware():
    flows = [
        H1("Supported hardware", anchor="hardware"),
        H2("Devices"),
        DataTable([
            ["Device", "Audio", "MIDI control", "Patterns", "Sample transfer"],
            ["Roland P-6",
             "2-in / 2-out, 44.1 kHz",
             "Granular, filter, envelope, mixer, FX (40+ CCs)",
             "64", "Slicer + format converter"],
            ["Roland SP-404 MK2",
             "2-in / 4-out, 48 kHz",
             "5-bus FX, DJ mode, looper (25+ CCs)",
             "16", "Slicer + format converter, IMPORT folder"],
            ["Akai Force / MPC",
             "—",
             "—", "—",
             "USB Computer Mode, XPM drum program export"],
            ["Midi Fighter Twister",
             "—",
             "Deep: 16 knobs &rarr; SP-404 FX + P-6 granular, RGB feedback",
             "—", "—"],
            ["Midi Fighter Spectra",
             "—",
             "Pad mapping for SP-404 with HOLD",
             "—", "—"],
            ["PreSonus ATOM SQ",
             "—",
             "Pad trigger, transport, touch-strip CC, PAD/PATTERN/CONTROL layers",
             "—", "—"],
            ["Ableton Push 2",
             "—",
             "Full surface: 8&times;8 pads, 960&times;160 LCD, 11 encoders, transport",
             "—", "—"],
            ["Any USB MIDI keyboard",
             "—",
             "Chromatic play via the KEYS tab",
             "—", "—"],
            ["Any USB audio device",
             "Record / playback at native rate",
             "—", "—", "—"],
        ], widths=[1.1*inch, 1.1*inch, 2.5*inch, 0.7*inch, 1.6*inch]),
        Spacer(1, 8),
        H2("SP-404 MK2 effects coverage"),
        P(
            "Compa includes the complete SP-404 MK2 effects list with named "
            "presets per bus:"
        ),
        *Bullets([
            "<b>Bus 1 and 2</b> — 42 effects including Scatter, Ha-Dou, Ko-Da-Ma, Tape Echo, JUNO Chorus, Cloud Delay.",
            "<b>Bus 3 and 4</b> — 40 effects with a different ordering, no Direct FX.",
            "<b>Input FX</b> — 18 effects focused on vocal and amp processing.",
        ]),
        P("All effect selection is via CC with human-readable names "
          "displayed on screen."),
        Spacer(1, 8),
        H2("System requirements"),
        DataTable([
            ["Component", "Spec"],
            ["Raspberry Pi", "Pi 3B+, 4, or 5 (Pi 4/5 recommended)"],
            ["Display", "7" + chr(0x201D) + " HDMI or DSI touchscreen at 800&times;480 or higher; any HDMI display works with mouse"],
            ["USB cables", "Data-capable cables (charge-only cables will not work)"],
            ["Power", "Official Pi power supply, 2.5 A minimum (3 A for Pi 4)"],
            ["SD card", "16 GB+, Class 10 or faster"],
            ["Network", "Ethernet or Wi-Fi for radio, updates, and Samba share"],
        ], widths=[1.4*inch, 5.6*inch]),
        Spacer(1, 8),
        H2("Display compatibility"),
        DataTable([
            ["Screen", "Resolution", "Connection", "Touch", "Notes"],
            ["7\" HDMI", "800&times;480+", "HDMI + USB", "Capacitive", "Best — designed for this"],
            ["7\" DSI (official Pi)", "800&times;480", "DSI ribbon", "Capacitive", "Excellent — no extra USB needed"],
            ["5\" HDMI", "800&times;480", "HDMI + USB", "Capacitive", "Good"],
            ["3.5\" SPI", "480&times;320", "GPIO SPI", "Resistive", "Functional — mouse recommended"],
            ["Any HDMI monitor", "Varies", "HDMI", "Mouse", "Works fine"],
        ], widths=[1.3*inch, 1.0*inch, 1.0*inch, 0.9*inch, 2.8*inch]),
        PageBreak(),
    ]
    return flows


def section_install():
    flows = [
        H1("Installation", anchor="install"),
        H2("Two paths — pick one"),
        H3("Option A — Compa OS image (just flash and go)"),
        P("For producers who want zero terminal time. Pre-baked SD card "
          "image with Compa already installed and configured to boot "
          "straight into the touchscreen UI:"),
        *Bullets([
            "Get the image at <b>raredata.net/compa</b> — drop your email, get a 24-hour signed link in your inbox.",
            "Open <b>Raspberry Pi Imager</b> &rarr; Choose OS &rarr; Use custom &rarr; pick the .img.xz file.",
            "Choose your SD card &rarr; Write.",
            "Insert the SD card, plug in your sampler, power on. Compa boots straight up.",
        ]),
        H3("Option B — One-command install (you already have a Pi)"),
        P("If you have a Raspberry Pi running fresh "
          "<b>Raspberry Pi OS Lite (64-bit)</b>, the entire install is one line:"),
        Code(
            "ssh pi@compa.local\n"
            "curl -sSL https://raw.githubusercontent.com/macdigi/compa/main/setup/install.sh | sudo bash\n"
            "sudo reboot"
        ),
        P("The installer pulls all dependencies, clones the repo to "
          "<code>/home/pi/compa</code>, sets up the Python venv, installs "
          "fonts, udev rules, and the systemd autostart service. It's "
          "idempotent &mdash; re-run anytime to update."),
        H3("First-boot wizard"),
        P("On first boot the wizard walks you through:"),
        *Bullets([
            "<b>Input mode</b> — move the mouse to pick MOUSE, tap the screen to pick TOUCHSCREEN.",
            "<b>Touchscreen calibration</b> — 4-corner + center taps compute an affine transform; persists at <code>~/.config/compa/touch_calibration.json</code>.",
            "<b>Wi-Fi setup</b> — scans nearby networks, lets you pick + enter a password via the on-screen keyboard, connects with <code>nmcli</code>. Skip is always available for Ethernet users.",
        ]),
        H2("Auto-updates"),
        P(
            "Compa polls the repo every 30 minutes in the background. When "
            "a new build lands, the Settings menu's <b>UPDATES</b> button "
            "lights up in the accent color with a (N) badge. Tap to open "
            "the Updates screen with the changelog in plain English. <b>Update "
            "now</b> pulls and restarts."
        ),
        Note(
            "<b>Tip:</b> Future features land without re-flashing the SD card. "
            "Just leave Compa on the network and it stays current."
        ),
        H2("Samba share (optional)"),
        P("To access recordings from your Mac or PC over the network, add "
          "this to <code>/etc/samba/smb.conf</code>:"),
        Code(
            "[compa]\n"
            "   path = /home/pi/compa\n"
            "   browseable = yes\n"
            "   read only = no\n"
            "   guest ok = yes"
        ),
        P("Restart Samba:"),
        Code("sudo systemctl restart smbd"),
        P("On macOS: Finder &rarr; Go &rarr; Connect to Server &rarr; "
          "<code>smb://compa.local/compa</code>."),
        PageBreak(),
    ]
    return flows


def section_multidevice():
    flows = [
        H1("The multi-device hub", anchor="hub"),
        Lead(
            "The Raspberry Pi has four USB ports. Compa can manage multiple "
            "devices connected at the same time. Each device receives its "
            "own independent MIDI connection and audio stream."
        ),
        Code(
            "   +-----------+     USB     +-----------+     USB     +-----------+\n"
            "   | Roland P-6 |<---------->|           |<---------->| SP-404 MK2 |\n"
            "   +-----------+    Audio    |   COMPA   |    Audio    +-----------+\n"
            "                    + MIDI   |           |    + MIDI\n"
            "   +-----------+     USB     |  Pi + 7\"  |\n"
            "   | Akai Force |<---------->| Touchscr. |\n"
            "   +-----------+   Storage   +-----------+"
        ),
        H2("Switching focus"),
        P(
            "The navigation bar at the top of every screen shows the names of "
            "all connected devices. Tap a device name to switch focus. The "
            "focused device determines which parameters appear on the Control "
            "screen, which pattern grid layout is used, and which format is "
            "selected for sample export."
        ),
        H2("Audio routing between devices"),
        P(
            "Open Settings &rarr; Audio Routing &rarr; START to route audio "
            "from one device into another. Compa handles sample-rate "
            "conversion automatically &mdash; you can route the output of an "
            "SP-404 MK2 (48 kHz) into a P-6 recording (44.1 kHz) without any "
            "manual configuration."
        ),
        H2("MIDI clock relay"),
        P(
            "When enabled in Settings, Compa relays MIDI clock from a "
            "designated master device to all other connected devices. Every "
            "unit stays locked to the same tempo without external sync "
            "hardware. Combine with Ableton Link (Settings &rarr; ABLETON LINK) "
            "to share that tempo with iPad apps and Live over Wi-Fi."
        ),
        H2("Hot-plug behavior"),
        P(
            "Compa scans for USB device changes every 2&ndash;5 seconds. Plug "
            "in or unplug devices at any time without restarting the "
            "application."
        ),
        *Bullets([
            "<b>Connection</b> — Compa identifies the device by USB descriptor, configures audio + MIDI, adds it to the nav bar.",
            "<b>Disconnection</b> — Compa removes it from the nav bar and releases its resources. Focus auto-switches to another connected device if any.",
            "<b>No restart required</b> — never reboot the Pi to add or remove devices.",
        ]),
        H2("Device cards"),
        P(
            "The Session screen shows a playing card per device with "
            "oscilloscope, level meters, BPM, pattern info, and Play/Rec/Stop "
            "transport. Device color themes adapt automatically: yellow for "
            "P-6, teal for SP-404, red for Force."
        ),
        Shot("compa_device_workspace.png",
             "Tap a device card to open its full-screen workspace — live "
             "oscilloscope across the top, per-device control tabs below."),
        PageBreak(),
    ]
    return flows


def section_screen_session():
    return [
        H1("Screen 1: Session", anchor="screen-session"),
        Lead(
            "The Session screen is the main dashboard. It appears when "
            "Compa starts and gives you the overview of everything happening "
            "in your setup at a glance."
        ),
        Shot("compa_session.png",
             "Session — every connected device side-by-side with live "
             "oscilloscopes, BPM, pattern info, and recall buffer status."),
        H2("Transport bar"),
        P(
            "Shows BPM, play/stop state, and active pattern number for "
            "every connected device. Tap BPM to edit. Tap play/stop to "
            "toggle transport for the focused device."
        ),
        H2("Resample calculator"),
        P(
            "Enter a source BPM and a target BPM. Compa calculates the "
            "exact playback speed ratio and the resulting sample length. "
            "Useful when resampling loops between different tempos."
        ),
        H2("Session notes"),
        P(
            "Free-text area for jotting down ideas, patch names, or "
            "anything you want to remember about the current session. "
            "Notes auto-save with the session file."
        ),
        H2("Backup and restore"),
        P(
            "For supported devices (P-6, SP-404 MK2), back up all pattern "
            "data and device settings to the Pi SD card. Restore loads a "
            "previous backup onto the device. Backups are timestamped and "
            "stored in <code>~/compa/backups/</code>."
        ),
        H2("LINK indicator"),
        P(
            "When Ableton Link is enabled, a green LINK dot pulses on every "
            "tempo or peers update. Tempo source and peer count are visible "
            "in Settings &rarr; ABLETON LINK."
        ),
        H2("Monitor output (experimental)"),
        P(
            "The OUT button on session cards sets a device as your "
            "headphone output. When you tap a different card, audio routes "
            "through the Pi to your headphone device so you can hear it."
        ),
        Note(
            "<b>Pi 3B note:</b> Audio pass-through works best on Pi 4 or 5 "
            "where USB devices are on separate buses. On the Pi 3B, the "
            "shared USB bus causes glitches when routing audio between two "
            "USB devices simultaneously. The workaround: use analog cables "
            "to monitor multiple devices &mdash; run one device's headphone "
            "out into another device's EXT SOURCE input."
        ),
        PageBreak(),
    ]


def section_screen_control():
    return [
        H1("Screen 2: Control", anchor="screen-control"),
        Lead(
            "The Control screen presents a grid of parameter knobs that "
            "adapt to whichever device you have focused. Turning a knob "
            "sends the corresponding MIDI CC message in real time."
        ),
        Shot("compa_control.png",
             "Control screen — adapts per device. Roland P-6 shown."),
        H2("Roland P-6 parameters"),
        *Bullets([
            "<b>Granular</b> — grain size, grain density, grain position, scatter, freeze.",
            "<b>Filter</b> — cutoff, resonance, filter type, envelope amount.",
            "<b>Envelope</b> — attack, decay, sustain, release.",
            "<b>Mixer</b> — track volume, pan, mute, solo.",
            "<b>FX</b> — reverb send, delay send, delay time, delay feedback.",
        ]),
        P(
            "All 14 granular parameters can be saved as a preset and "
            "recalled from the session file. Sweep back and forth between "
            "presets with the Midi Fighter Twister or any external "
            "controller."
        ),
        H2("Roland SP-404 MK2 parameters"),
        *Bullets([
            "<b>FX Bus 1–5</b> — effect type, depth, rate, mix level. Each bus carries a different effects list.",
            "<b>Looper</b> — loop length, overdub level, playback speed.",
            "<b>DJ mode</b> — crossfader position, EQ low/mid/high, filter sweep.",
        ]),
        H2("Generic USB devices"),
        P(
            "No parameter knobs appear for unrecognized devices &mdash; the "
            "Control screen displays a message that MIDI CC mapping is not "
            "supported for the connected device. You can still record audio, "
            "use the file browser, and run the kit builder; you just can't "
            "drive its parameters from this screen."
        ),
        PageBreak(),
    ]


def section_screen_pattern():
    return [
        H1("Screen 3: Pattern", anchor="screen-pattern"),
        Lead(
            "The Pattern screen shows a grid of pattern slots that adapts "
            "to the focused device — and a Pi-side step sequencer overlay "
            "that lets you build patterns without writing to the device's "
            "own pattern memory."
        ),
        Shot("compa_pattern.png",
             "Pattern grid + step sequencer overlay."),
        H2("Pattern grid"),
        DataTable([
            ["Device", "Layout", "Slots"],
            ["Roland P-6", "8 columns × 8 rows", "64"],
            ["Roland SP-404 MK2", "4 columns × 4 rows", "16"],
        ], widths=[2.0*inch, 2.5*inch, 2.5*inch]),
        P(
            "Tap a slot to select it as the active pattern. Long-press to "
            "copy, paste, or clear a pattern."
        ),
        H2("Chain / song mode"),
        P(
            "Tap the <b>Chain</b> button to enter chain mode. Select multiple "
            "patterns in order to build a chain. Compa sends pattern-change "
            "messages at the correct bar boundaries so transitions are seamless. "
            "Chain steps can carry FX snapshots — drop a different SP-404 effect "
            "configuration on each step of the song."
        ),
        H2("Step sequencer"),
        P(
            "Tap <b>SEQ</b> to open the step-sequencer overlay. The sequencer "
            "displays 16 steps per bar (expandable to 64). Tap a step to "
            "toggle it on or off. Hold a step and turn a knob on the Control "
            "screen to set per-step parameter locks."
        ),
        H3("Special row types (SP-404 MK2)"),
        *Bullets([
            "<b>Chromatic</b> — the row plays a chromatic note instead of a pad trigger. Build melodic basslines from a single sample.",
            "<b>Ghost Kick</b> — silent trigger row used as a sidechain source for ducking effects in Compa or in your DAW.",
            "<b>EXT SOURCE</b> — gates the SP-404's external audio input. Slice live input into the pattern.",
        ]),
        H2("Step probability"),
        P(
            "Each active step can carry a probability value (0–100%). On every "
            "loop pass Compa rolls the dice — perfect for generative drum "
            "patterns that drift without ever sounding random."
        ),
        H2("LFO automation"),
        P(
            "Compa includes a built-in LFO engine driven from the same step "
            "sequencer screen. Modulate any MIDI CC parameter with sine, "
            "triangle, saw (up/down), square, random, or sample-and-hold "
            "waveforms at rates from 0.01 Hz up to 30 Hz. Multiple "
            "simultaneous LFO targets are supported. The 30 Hz update rate "
            "is smooth enough for filter sweeps and light on the Pi's CPU."
        ),
        PageBreak(),
    ]


def section_screen_record():
    return [
        H1("Screen 4: Record", anchor="screen-record"),
        Lead(
            "The Record screen captures audio from any connected device. "
            "It runs a continuous 60-second recall buffer in the background "
            "so you never lose a good take just because you forgot to press "
            "record."
        ),
        Shot("compa_record.png",
             "Recorder — input source selector, level meters, take list, "
             "recall and threshold buttons."),
        H2("IN source selector"),
        P(
            "Tap <b>IN</b> to choose which device to record from. The list "
            "shows every connected USB audio device plus the radio stream."
        ),
        H2("Recall buffer"),
        P(
            "Compa continuously captures the last 60 seconds of audio from "
            "the selected source into a circular buffer. If you missed "
            "pressing record, tap <b>Recall</b> to save the buffer contents "
            "as a new take."
        ),
        H2("Threshold recording"),
        P(
            "Enable threshold recording with the <b>T</b> button. Set the "
            "threshold level in Settings (typical starting point: -30 dBFS). "
            "Recording starts the moment the input signal exceeds the "
            "threshold and stops after a configurable silence duration."
        ),
        H2("Auto-record"),
        P(
            "Press <b>A</b> or tap the <b>Auto</b> button to enable "
            "auto-record mode. Compa starts recording whenever transport "
            "is started on the focused device and stops when transport "
            "stops. Walk away, jam, come back to a folder of takes."
        ),
        H2("Take management"),
        P(
            "Each recording is saved as a numbered take. The take list "
            "lives on the right side of the screen. Tap a take to preview, "
            "swipe left to delete, tap the export icon to send it to the "
            "Sample screen for editing. Star, rename, and BPM/pattern "
            "metadata are stored alongside the WAV."
        ),
        H2("Where takes go"),
        Code("~/compa/recordings/"),
        P(
            "If you set up the optional Samba share, the recordings folder "
            "is mounted on your Mac/PC at <code>smb://compa.local/compa/recordings</code>."
        ),
        PageBreak(),
    ]


def section_screen_sample():
    return [
        H1("Screen 5: Sample", anchor="screen-sample"),
        Lead(
            "The Sample screen is Compa's waveform editor and format "
            "converter. Load any WAV, place slice markers visually, "
            "trim with snap-to-zero-crossing so you don't get clicks, "
            "and export to whichever device you want to play it on."
        ),
        Shot("compa_sample.png",
             "Sample browser + visual waveform slicer with start/end "
             "markers and zoom."),
        H2("File browser"),
        P(
            "Navigate the Pi filesystem to find recordings, imported "
            "samples, or any WAV file. Defaults to "
            "<code>~/compa/recordings/</code> but you can navigate "
            "anywhere on the SD card or the Samba share."
        ),
        H2("Visual waveform slicer"),
        P(
            "Load a file to see its waveform. Drag the start and end "
            "markers to define a region. Pinch to zoom on the touchscreen. "
            "The selected region is highlighted in the device color."
        ),
        H3("Auto-slice"),
        P(
            "Tap <b>Auto-Slice</b> to detect transients and place slice "
            "markers at each hit. Adjust the sensitivity slider to capture "
            "more or fewer slices. Each slice becomes an individual sample. "
            "You can also auto-slice into 2, 4, 8, or 16 equal parts."
        ),
        H3("Normalize / mono / downsample"),
        *Bullets([
            "<b>Normalize</b> — bring the peak level of the selected region to 0 dBFS. Consistent volume across all your samples.",
            "<b>Mono</b> — fold a stereo recording to mono. Useful for kicks, snares, and SP-404 chromatic mode.",
            "<b>Downsample</b> — drop the sample rate for lo-fi character or to fit a tight memory budget on the device.",
        ]),
        H3("Trim"),
        P(
            "Tap <b>Trim</b> to remove everything outside the selected "
            "region. Snap-to-zero-crossing on both edges keeps cuts clean. "
            "The trimmed file replaces the original unless you choose "
            "<b>Save As</b>."
        ),
        H2("Export formats"),
        P(
            "Tap <b>Export</b> and choose a target format. Compa converts "
            "the file and (when applicable) transfers it directly over USB."
        ),
        DataTable([
            ["Target", "Format", "Sample rate", "Bit depth", "Channels"],
            ["Roland P-6", "WAV", "44 100 Hz", "16-bit", "Mono"],
            ["Roland SP-404 MK2", "WAV", "48 000 Hz", "16-bit", "Stereo"],
            ["Akai MPC / Force", ".Drum.xpm program", "—", "—", "—"],
            ["Ableton Live", ".adg Drum Rack (gzipped XML)", "—", "—", "—"],
        ], widths=[1.6*inch, 2.0*inch, 1.0*inch, 0.9*inch, 0.9*inch]),
        Note(
            "When exporting to a connected device, Compa transfers the file "
            "directly. SP-404 samples land in the IMPORT folder on the SP's "
            "SD card. Ableton exports go to the Samba share for pickup from "
            "your Mac or PC."
        ),
        PageBreak(),
    ]


def section_screen_radio():
    return [
        H1("Screen 6: Radio", anchor="screen-radio"),
        Lead(
            "Compa ships with 137 internet radio stations across 25+ "
            "genres &mdash; jazz, soul, funk, lo-fi, hip-hop, metal, "
            "classical, electronic, vintage, paranormal, and more. Use it "
            "for inspiration, source material, or background while you "
            "work."
        ),
        Shot("compa_radio.png",
             "Radio — station library, ICY metadata for current track, "
             "full-width real-time waveform visualizer."),
        H2("Station library"),
        P(
            "Stations are organized across genre tabs at the top. Scroll "
            "through and tap a station to start playback. The station "
            "name and current track info (when the station broadcasts ICY "
            "metadata) appear at the top of the screen."
        ),
        H2("Audio path"),
        P(
            "Radio plays through the HDMI audio output on the Pi. USB "
            "audio recording is handled on a separate path, so you can "
            "monitor radio on speakers while simultaneously recording "
            "from a USB device with no crosstalk or routing conflict."
        ),
        H2("Capture buffer"),
        P(
            "Just like the Record screen, the Radio screen maintains a "
            "60-second circular capture buffer. Tap <b>Capture</b> to save "
            "the last minute of radio audio as a WAV file."
        ),
        H2("Threshold recording from radio"),
        P(
            "Enable threshold recording on the Radio screen to "
            "automatically capture segments that exceed the configured "
            "threshold level. Useful for grabbing individual songs or "
            "segments from a continuous stream."
        ),
        H2("Adding stations"),
        P(
            "Stations live in <code>docs/radio_stations.json</code> in the "
            "Compa repo. Submit a PR with name, genre, bitrate, and a "
            "working URL to add a station to the public list, or edit the "
            "JSON locally to keep it private."
        ),
        PageBreak(),
    ]


def section_screen_kit():
    return [
        H1("Screen 7: Kit Builder", anchor="screen-kit"),
        Lead(
            "Compa's Kit Builder turns a folder of samples into an Akai "
            "MPC / Force drum program (.xpm) or an Ableton Live Drum "
            "Rack (.adg) in a single tap. 4&times;4 pad grid, 8 banks, "
            "128 pads total."
        ),
        Shot("compa_kit.png",
             "Kit Builder — 4×4 pad grid with 8 banks (A–H), waveform "
             "preview per pad, smart drum import."),
        H2("Pad grid"),
        P(
            "A 4&times;4 grid of pads with 8 banks (A through H) gives you "
            "128 pad slots. Tap a pad to select it. The selected pad is "
            "highlighted in the device color."
        ),
        H2("Assigning samples"),
        P(
            "With a pad selected, tap <b>Load</b> to open the file browser "
            "and choose a sample. The waveform appears above the grid. "
            "Trim start and end points directly in the Kit Builder &mdash; "
            "no need to bounce back to the Sample screen for small edits."
        ),
        H2("Smart import"),
        P(
            "Drop a folder onto the Kit Builder. Compa scans the folder "
            "and file names for common drum-type keywords (kick, snare, "
            "hat, clap, tom, perc, ride, crash, etc.) using pattern "
            "matching against typical sample-library conventions, then "
            "auto-assigns each sample to a sensible pad."
        ),
        H2("Banks"),
        P(
            "Tap the bank selector (A through H) to switch between banks. "
            "Each bank holds 16 pads. Use multiple banks to organize a kit "
            "by category &mdash; kicks on Bank A, snares on Bank B, hats "
            "on Bank C, percussion on Bank D, and so on."
        ),
        H2("Export — Akai MPC / Force (.xpm)"),
        P(
            "Tap <b>Export &rarr; XPM</b> to generate an Akai-compatible "
            "drum program. Compa creates the .xpm and formats every "
            "assigned sample to the correct specifications. Transfer to "
            "your MPC or Force over the network or via USB Computer Mode."
        ),
        H2("Export — Ableton Live (.adg)"),
        P(
            "Tap <b>Export &rarr; ADG</b> to generate an Ableton Live "
            "Drum Rack preset. The .adg and its referenced samples land "
            "on the Samba share. Drag the .adg into a Live session to "
            "load the kit."
        ),
        PageBreak(),
    ]


def section_screen_xfer():
    return [
        H1("Screen 8: XFER (Files & Transfer)", anchor="screen-xfer"),
        Lead(
            "Push and pull files between Compa, your MPC/Force, and other "
            "Compas on the same LAN. Dual-pane file manager with "
            "peer-to-peer transfer."
        ),
        Shot("compa_files.png",
             "File browser — dual-pane peer-to-peer transfer between "
             "Compas on the same LAN."),
        H2("Dual-pane file manager"),
        P(
            "Left pane is local (the Pi's filesystem). Right pane can be "
            "another Compa on the network, an MPC/Force in USB Computer "
            "Mode, or any other reachable storage. Drag-and-drop on the "
            "touchscreen to copy."
        ),
        H2("MPC / Force transfer"),
        P(
            "Plug an Akai Force or MPC into a Pi USB port and put it in "
            "Computer Mode. Compa sees the SD card and any internal SSD "
            "as separate volumes. Push your kit's .xpm and its samples "
            "directly into the right project folder on the device."
        ),
        Shot("compa_transfer.png",
             "XFER — push files to MPC/Force via USB Computer Mode, "
             "with SD card and SSD drive selectors."),
        H2("Compa-to-Compa over LAN"),
        P(
            "Two Compas on the same network see each other automatically "
            "via mDNS. Drag a kit, a recording, or a session from one to "
            "the other &mdash; useful for studio + stage setups, or for "
            "moving sounds between a development Compa and a performance "
            "Compa."
        ),
        H2("Samba share"),
        P(
            "If you've enabled the optional Samba share (see Installation), "
            "Compa's <code>~/compa</code> folder is browsable from your "
            "Mac/PC at <code>smb://compa.local/compa</code>."
        ),
        PageBreak(),
    ]


def section_screen_settings():
    return [
        H1("Screen 9: Settings", anchor="screen-settings"),
        Lead(
            "Global configuration. Audio, MIDI, themes, calibration, "
            "screen capture, updates."
        ),
        Shot("compa_settings.png",
             "Settings — global configuration."),
        H2("Mouse mode"),
        P(
            "Toggle mouse mode on or off. When enabled, a visible cursor "
            "appears on screen and touch input behaves like a mouse with "
            "click events. Useful for debugging or when using a mouse "
            "instead of touch."
        ),
        H2("Auto-record"),
        P(
            "Enable or disable auto-record globally. When on, recording "
            "begins and ends with the focused device's transport."
        ),
        H2("Threshold level"),
        P(
            "Set the audio level (in dBFS) at which threshold recording "
            "is triggered. Lower values mean quieter sounds will trigger "
            "recording. Typical starting point: -30 dBFS."
        ),
        H2("Silence duration"),
        P(
            "Set how many seconds of silence must elapse before threshold "
            "recording automatically stops. Default: 3 seconds."
        ),
        H2("Connected devices"),
        P(
            "A list of all currently connected USB devices with audio "
            "configuration (sample rate, channels) and MIDI status."
        ),
        H2("Audio routing"),
        P(
            "Configure routing between devices. Select a source device "
            "and a destination device, then tap <b>START</b>. Compa inserts "
            "sample-rate conversion automatically when source and "
            "destination operate at different rates."
        ),
        H2("MIDI clock relay"),
        P(
            "Select which device is the clock master. All other devices "
            "receive clock from the master through Compa. Disable relay "
            "to let each device run independently."
        ),
        H2("Ableton Link"),
        *Bullets([
            "<b>Enable Link</b> — joins any Link session on the local network.",
            "<b>Send MIDI Clock</b> — broadcasts 0xF8 at 24 PPQN to every connected device's MIDI out so SP-404, P-6, and other class-compliant grooveboxes follow Link tempo without a USB cable to the iPad. Set the device's sync source to <b>External / Auto</b>.",
            "<b>Live status</b> — tempo source (\"from this Compa\" / \"from a Link peer\"), peer count, recipient device list.",
            "<b>Link Audio (Live 12.4+)</b> — broadcast Compa's recorder input as a network audio channel that Live 12.4 sees as a track input. See the <i>Ableton Link Audio</i> chapter for setup, latency, and the Pi 3B / Pi 5 caveat.",
        ]),
        H2("Touch calibration"),
        P(
            "Run the touch calibration wizard if the touchscreen is not "
            "registering taps accurately. Follow the on-screen prompts "
            "to tap the four corners and the center. The transform "
            "persists at <code>~/.config/compa/touch_calibration.json</code>."
        ),
        H2("Video recording"),
        P(
            "Tap <b>RECORD</b> to start a screen recording of the Compa "
            "touchscreen (saved to <code>~/compa/videos/</code>). Tap "
            "again to <b>STOP</b>. The <b>Auto-demo</b> walkthrough "
            "button cycles through every screen automatically with a "
            "recording running &mdash; about a 43-second tour video "
            "without manual navigation."
        ),
        H2("Screenshots"),
        P("Three capture buttons in the SCREENSHOTS section, grouped "
          "with Video Recording:"),
        *Bullets([
            "<b>Push 2 screen</b> — instant capture of the current Push 2 display frame. Saves to <code>~/compa/screenshots/</code> as "
            "<code>push2_&lt;device&gt;_&lt;mode&gt;_&lt;layout&gt;_&lt;page&gt;_&lt;timestamp&gt;.png</code>. The Push 2 display is independent "
            "of the touchscreen so no timer is needed.",
            "<b>Compa screen (5s timer)</b> — schedules a Compa touchscreen capture in 5 seconds. A small countdown badge appears in the "
            "top-right while the timer runs so you can navigate to whichever screen you want captured. Saves as "
            "<code>compa_&lt;screen&gt;_&lt;timestamp&gt;.png</code>.",
            "<b>Both (5s timer)</b> — schedules Compa + Push 2 captures to fire on the same tick. Useful for documenting matching states "
            "across both surfaces.",
        ]),
        Note(
            "The countdown badge is drawn after the screenshot save, so "
            "the saved Compa PNG is the clean fully-composed UI without "
            "the countdown visible. The badge does appear in any "
            "in-progress video recording (so playback shows when the "
            "timer fired)."
        ),
        H2("I/O & connectivity"),
        Shot("compa_io.png",
             "I/O & connectivity — Wi-Fi/Bluetooth config, on-screen "
             "keyboard, per-device color picker."),
        H2("Updates"),
        P(
            "<b>UPDATES</b> lights up in the accent color with a (N) "
            "badge when a new build lands. Tap to open the Updates "
            "screen with the changelog in plain English. <b>Update now</b> "
            "pulls and restarts. Drag anywhere on a button row to scroll "
            "without triggering the button."
        ),
        PageBreak(),
    ]


def section_screen_help():
    return [
        H1("Screen 10: Help", anchor="screen-help"),
        Lead(
            "Three searchable reference manuals on-device: Compa, "
            "P-6, SP-404 MK2. Tap a tab to switch manuals; type into "
            "the search bar to jump to any section."
        ),
        Shot("compa_help.png",
             "Help — split-pane reference manual with sidebar TOC and "
             "scrollable content."),
        H2("Manuals included"),
        *Bullets([
            "<b>COMPA</b> — every screen, feature, workflow, keyboard shortcut, troubleshooting note. The same content as this PDF.",
            "<b>P-6</b> — Roland P-6 reference: pads and banks, granular engine, sequencer, sampling, auto-chop, MIDI CCs, menu navigation, firmware notes.",
            "<b>SP-404 MK2</b> — Roland SP-404 MK2 reference: pads, sequencer, performance FX, looper, DJ mode, MIDI CCs, USB backup modes.",
        ]),
        H2("Search"),
        P(
            "The search bar in the top-right filters the sidebar in "
            "real time. Matching sections highlight, non-matching ones "
            "fade. Clear the search to restore the full TOC."
        ),
        H2("Why three manuals on-device"),
        P(
            "When you're mid-jam and forget a CC number on the SP-404 "
            "or which menu page holds the P-6's filter resonance, you "
            "shouldn't have to reach for your phone. The manuals are "
            "right there on Compa, searchable, and (importantly) the "
            "same content this PDF prints."
        ),
        PageBreak(),
    ]


def section_workspaces():
    return [
        H1("Device workspaces", anchor="workspaces"),
        Lead(
            "Tap a device card on the Session screen and Compa opens "
            "a full-screen workspace. Live oscilloscope across the top, "
            "per-device control tabs below."
        ),
        H2("Roland SP-404 MK2"),
        P(
            "The SP-404 workspace gives you bus FX knobs, a 16-slot "
            "Twister grid, the chromatic keyboard, the step sequencer, "
            "the looper, and DJ mode &mdash; all without touching the "
            "device's own menu pages."
        ),
        TwoUpShots(
            "compa_workspace_SP-404MKII_control.png",
            "compa_workspace_SP-404MKII_twister.png",
            "Control — bus FX with named effects.",
            "Twister — 16-slot Midi Fighter Twister grid.",
        ),
        TwoUpShots(
            "compa_workspace_SP-404MKII_keys.png",
            "compa_workspace_SP-404MKII_looper.png",
            "Keys — chromatic keyboard.",
            "Looper — record / overdub / stop / delete / undo / redo.",
        ),
        TwoUpShots(
            "compa_workspace_SP-404MKII_sequence.png",
            "compa_workspace_SP-404MKII_dj.png",
            "Sequence — pattern view with step sequencer overlay.",
            "DJ — crossfader, EQ, filter sweep.",
        ),
        Spacer(1, 8),
        H2("Roland P-6"),
        P(
            "The P-6 workspace is built around the granular engine. "
            "Knobs for every parameter the device exposes over MIDI, "
            "the chromatic keyboard, the pattern grid, the step sequencer."
        ),
        TwoUpShots(
            "compa_workspace_P-6_control.png",
            "compa_workspace_P-6_keys.png",
            "Control — granular engine + filter + envelope + mixer + FX.",
            "Keys — chromatic on Ch4 (P-6 granular).",
        ),
        Shot("compa_workspace_P-6_pattern.png",
             "P-6 — pattern grid + step sequencer."),
        PageBreak(),
    ]


def section_midi_controllers():
    return [
        H1("MIDI controllers", anchor="midi-controllers"),
        Lead(
            "Compa works with any USB MIDI controller. Plug it in and "
            "Compa detects it within 2 seconds. Specific controllers "
            "have deep integrations; everything else is treated as a "
            "generic MIDI keyboard and routed through the chromatic "
            "keyboard module."
        ),
        H2("Midi Fighter Twister (deep integration)"),
        P(
            "The DJ TechTools Midi Fighter Twister is the recommended "
            "physical controller for Compa. It auto-detects on plug-in "
            "and maps itself to whichever device you have focused &mdash; "
            "<b>SP-404 effects on one page, P-6 granular engine on "
            "another</b>. Flip between devices with Compa's focus toggle "
            "and the Twister retargets instantly."
        ),
        H3("On the SP-404 MK2 — 16 effect slots with live switching"),
        *Bullets([
            "<b>16 encoders = 16 SP-404 effect slots</b> — each knob pre-assigned to an effect (To-Gu-Ro, Scatter, Tape Echo, Stopper, Downer, Ha-Dou, etc.).",
            "<b>Press a knob</b> to activate the effect on the currently selected bus. Press again to turn it off.",
            "<b>Turn the knob</b> to sweep Ctrl 1 of that effect in real time.",
            "<b>RGB LEDs reflect effect color</b> — red/orange for distortion and dynamics, blue/cyan for modulation and reverb, green for filters, purple/pink for time-based effects.",
            "<b>Focus mode</b> — press a knob to focus on one effect; all other LEDs dim so you can see exactly what's active.",
            "<b>Bus-aware</b> — the knobs follow whichever bus (1–4 or Input FX) is selected, so you can build different FX chains per bus.",
        ]),
        H3("On the P-6 — full granular engine control"),
        *Bullets([
            "<b>14 knobs mapped to the granular parameters</b> — Position, Size, Density, Pitch, Spray, Reverse, Freeze, Filter Cutoff/Resonance, Attack/Decay, LFO Rate/Depth, Pan.",
            "<b>2 dynamic Ctrl knobs</b> — the remaining knobs adapt to whatever screen you're on (pattern control, pad mute, etc.).",
            "<b>LED colors match the P-6's yellow accent</b> so the hardware feels like part of the same family.",
            "<b>Live CC sends on Channel 15</b> (P-6 Auto channel), so every knob tweak is captured by the P-6 as automation data.",
            "<b>Preset recall</b> — save and recall the full 14-parameter granular state in Compa's session, then sweep back and forth with the Twister.",
        ]),
        H3("Shared across both devices"),
        *Bullets([
            "<b>Multi-page support</b> — scroll through extended banks of effects and parameters with knob 4's press.",
            "<b>Auto-map on startup</b> — no configuration needed.",
            "<b>Customizable slot assignments</b> — swap which effect is on which knob via Settings.",
            "<b>Clean handoff between devices</b> — switching focus on Compa retargets the Twister, rebuilds the LED colors, and the same 16 knobs now control the other device.",
        ]),
        H2("Chromatic MIDI keyboards"),
        P(
            "Plug in any USB MIDI keyboard &mdash; Alesis V49, Arturia "
            "KeyStep, AKAI MPK Mini, Novation Launchkey, etc. &mdash; and "
            "play any loaded sample melodically:"
        ),
        *Bullets([
            "<b>Two-octave range</b> from the KEYS tab of a device workspace.",
            "<b>Plays the focused device</b> on its designated chromatic channel: SP-404 MK2 on MIDI Ch 16, P-6 on MIDI Ch 4 (granular engine).",
            "<b>Visual piano display</b> shows active notes with velocity color.",
            "<b>Latch mode</b> — tap keys to hold chords.",
            "<b>Octave shift &plusmn;3</b> — extends the playable range across the MIDI spectrum.",
            "<b>Touch-to-play</b> on the screen — works without a hardware keyboard.",
            "<b>Pad selector</b> — tap any pad across all banks to audition the sound before committing.",
            "<b>All notes off</b> when switching tabs or leaving the workspace (no stuck notes).",
        ]),
        Note(
            "<b>SP-404 chromatic note:</b> Ch16 chromatic is a hardware-only "
            "mode on the SP itself &mdash; tap the pad on the SP, press "
            "<b>SHIFT + PAD 4 (CHROMATIC)</b>, then Compa's keyboard plays "
            "that sample across the keys. For the P-6, chromatic routing is "
            "fully MIDI-controlled and works immediately."
        ),
        H2("ATOM SQ"),
        *Bullets([
            "32 pads map to SP-404 sample triggering.",
            "Transport buttons control Compa's master clock.",
            "Touch-strip routes to CC for expressive control.",
            "Layer system switches between PAD / PATTERN / CONTROL modes.",
        ]),
        H2("Midi Fighter Spectra"),
        P(
            "Basic pad mapping today with color-coded banks for SP-404 "
            "effects and HOLD functionality. A deeper integration is in "
            "progress."
        ),
        H2("Generic USB MIDI input"),
        *Bullets([
            "Auto-detected and routed through the chromatic keyboard module.",
            "Notes and CC forwarded to the focused device on its chromatic channel.",
            "Pitch bend supported on devices that accept it (SP-404 Ch16 / Ch11 vocoder).",
            "Excludes known devices (SP-404, P-6, ATOM SQ, Twister, Spectra, Force) so their own dedicated connections aren't duplicated.",
            "Disconnect and reconnect freely — Compa rescans every 2 seconds.",
        ]),
        PageBreak(),
    ]


def section_push2():
    return [
        H1("Push 2 control deck", anchor="push2"),
        Lead(
            "The Ableton Push 2 is the recommended performance surface "
            "for Compa. Plug it in via USB &mdash; <b>no Live, no host "
            "required</b>. Compa drives the 960&times;160 RGB display, "
            "the 8&times;8 RGB pad grid, the 11 encoders, and the full "
            "button layout directly from the Pi."
        ),
        H2("Connection"),
        P(
            "USB-A to USB-B cable from any Pi port to the Push 2's "
            "<b>Computer</b> USB port. The Push 2's main power "
            "connector also needs to be in. On boot Compa logs <code>"
            "Push 2 connected</code> and <code>Push 2 display active"
            "</code> when the surface is ready."
        ),
        H2("Mode auto-tracking"),
        P(
            "The Push 2 surface re-roles to match whichever "
            "device-workspace tab is focused on the Compa touchscreen:"
        ),
        DataTable([
            ["Compa tab", "Push 2 mode"],
            ["Control", "Bank-aware pad grid for the focused device"],
            ["Keys", "Chromatic / in-scale keyboard layout"],
            ["Pattern / Sequence", "Combined pattern launcher + step sequencer"],
            ["DJ (SP only)", "DJ-mode action grid + crossfader on the touch strip"],
            ["Looper (SP only)", "SP-404 looper action layout"],
        ], widths=[2.5*inch, 4.5*inch]),
        Spacer(1, 6),
        H2("Control mode — per-device pad layouts"),
        P(
            "The <b>Layout</b> button cycles between layouts. For the "
            "P-6: row-per-bank (default, all 8 banks visible &mdash; one "
            "bank per Push 2 row, 6 pads + 2 blank cells) or 4&times;4 "
            "quadrants (2 banks per page, each bank's 6 pads laid out "
            "2&times;3 inside its quadrant). For the SP-404 MK2: 2-row "
            "strips (default, 4 banks per page with Bank A on top, D on "
            "bottom) or 4&times;4 quadrants (TL=A, TR=B, BL=C, BR=D, "
            "SP-style top-left=pad-1 numbering). Octave Up/Down pages "
            "between bank windows (E&ndash;H, I&ndash;J for SP)."
        ),
        TwoUpShots(
            "push2_P-6_control_strips.png",
            "push2_P-6_control_quad.png",
            "Push 2 — P-6 Control (row-per-bank).",
            "Push 2 — P-6 Control (quadrants).",
        ),
        TwoUpShots(
            "push2_SP-404_control_strips.png",
            "push2_SP-404_control_quad.png",
            "Push 2 — SP-404 Control (2-row strips).",
            "Push 2 — SP-404 Control (quadrants).",
        ),
        H3("Pad flash on incoming notes"),
        P(
            "When the focused device plays a pad &mdash; from its own "
            "hardware press, sequencer, or any other source &mdash; Compa "
            "receives the MIDI echo and lights the matching Push 2 pad "
            "bright white for the duration the device holds it. Empty "
            "pads don't echo MIDI from the device, so they don't flash."
        ),
        H3("Bottom select buttons (SP-404 only)"),
        P(
            "In control mode, the bottom-row select buttons 1&ndash;5 act "
            "as the SP-404 bus selector: <b>B1, B2, B3, B4, IN</b>. The "
            "active bus lights in its bus color (red, blue, green, yellow, "
            "orange); the others stay dim. Slots 6&ndash;8 are unmapped."
        ),
        H3("Dynamic effect labels"),
        P(
            "When an effect is loaded on the SP-404, encoders 1&ndash;6 "
            "relabel to that effect's actual parameters &mdash; Length / "
            "Speed / Loop SW for DJFX Looper; Time / Feedback / Send / "
            "L Damp F / H Damp F / Mode for Ko-Da-Ma; etc. &mdash; with "
            "values formatted in the SP's own units (Hz / dB / sec / sync "
            "divs / ON/OFF):"
        ),
        Shot("push2_SP-404_control_active_fx.png",
             "Push 2 — SP-404 Control with an active effect. Encoder labels "
             "relabel to the effect's parameter names."),
        H2("Keys mode"),
        P(
            "Plays the focused device chromatically through the Push 2 "
            "pad grid. Eight scales: chromatic, major, minor, min/maj "
            "pentatonic, blues, dorian, mixolydian, harmonic minor."
        ),
        *Bullets([
            "<b>Pad layout</b> — chromatic: each row offsets +5 semitones from the row below (Ableton-Live note layout). Any other scale: every pad plays an in-scale note (no off-scale gaps); rows offset +3 scale degrees.",
            "<b>Header status</b> — root + scale name + the actual playable range (e.g. \"C2&ndash;F#5\") computed from the layout in use.",
            "<b>Held-note overlay</b> — when you hold a Push 2 pad (or chord), the held note name(s) replace the BPM in the display centerpiece so you can see exactly what's sounding.",
            "<b>Octave Up / Octave Down</b> — transpose the layout by 12 semitones at a time.",
            "<b>Nav-Up / Nav-Down</b> — cycle scale.",
            "<b>Nav-Left / Nav-Right</b> — step root pitch class -1 / +1.",
        ]),
        TwoUpShots(
            "push2_P-6_keys.png", "push2_SP-404_keys.png",
            "Push 2 — P-6 Keys.", "Push 2 — SP-404 Keys.",
        ),
        H2("Pattern mode (overlay sequencer)"),
        P(
            "Compa's pattern mode is an <b>overlay sequencer</b> &mdash; "
            "its own grid that fires samples on the focused device via "
            "MIDI but never writes to the device's own pattern memory. "
            "Set up your kits on the SP/P-6, then perform overlay "
            "sequences on top &mdash; switch banks and patterns on the "
            "fly during playback to use the same grid as a live remix "
            "engine. Driven by Compa's master clock so tempo is "
            "rock-solid."
        ),
        TwoUpShots(
            "push2_P-6_pattern.png", "push2_SP-404_pattern.png",
            "Push 2 — P-6 Pattern.", "Push 2 — SP-404 Pattern.",
        ),
        H3("Push 2 layout in pattern mode"),
        *Bullets([
            "<b>8&times;8 pad grid</b> — step cells. Tap to toggle.",
            "<b>Bottom select</b> — bank selector in bank-palette color.",
            "<b>Top select</b> — pattern launchers 1&ndash;8 within the active 8-pat window. Active pattern bright in device theme color, others dim.",
            "<b>Push 2 display</b> — sequencer overview: tracks &times; all-steps grid showing the pattern's whole shape with the active 8-step / 8-pad window outlined. Status line: <code>Bank A · Pat 1/64 · Step 1/2 · 1/16 · Pads 1/2 · Sw 25%</code>.",
        ]),
        H3("Editing controls"),
        *Bullets([
            "<b>Quantize</b> — normalize all active step velocities to 100.",
            "<b>New</b> — clears the pattern. Two-press safety (see below).",
            "<b>Double Loop</b> — extends pattern length 16 &rarr; 32 &rarr; 64 with empty cells.",
            "<b>Duplicate</b> — doubles length and copies the existing content (same beat plays twice).",
            "<b>Convert / Fixed Length</b> — zoom step resolution 1/4 &harr; 1/8 &harr; 1/16 &harr; 1/32.",
            "<b>Repeat / Accent</b> — rotate the pattern one step earlier / later.",
            "<b>Swing encoder</b> — sets odd-step shuffle 0&ndash;50%.",
            "<b>Encoders 1&ndash;2</b> — live remix: encoder 1 re-rolls density at the knob's level; encoder 2 randomizes velocities of active steps.",
        ]),
        H3("Two-press New (safety)"),
        P(
            "The <b>New</b> button uses a two-press confirm so an "
            "accidental tap can't wipe a take in progress &mdash; first "
            "press lights the button red and pops a centered prompt with "
            "a 3-second countdown:"
        ),
        Shot("push2_pattern_new_confirm.png",
             "Push 2 — Pattern Clear confirm overlay. Press New again "
             "within the 3-second window to actually clear."),
        H3("Step grids persist"),
        P(
            "Each (device, pattern) step grid saves to "
            "<code>sessions/compa_step_grids.json</code> on shutdown and "
            "reloads on the next launch &mdash; overlay-sequencer data "
            "survives a service restart."
        ),
        H2("DJ mode (SP-404 only)"),
        P(
            "The pad grid maps to the SP's DJ-mode action set "
            "(5 columns &times; 2 rows = Decks A and B). Header status "
            "shows the current crossfader position <code>Crossfade N/127"
            "</code> for live reference. <b>Touch strip</b> in DJ mode "
            "acts as the SP-404 crossfader (CC8 on Ch1)."
        ),
        Shot("push2_SP-404_dj.png",
             "Push 2 — SP-404 DJ mode."),
        H2("Looper mode (SP-404 only)"),
        P(
            "Visual layout for the SP-404 looper actions. The header "
            "shows the labeled slots: <b>REC, OVERDUB, STOP, DELETE, "
            "UNDO, REDO</b>. Pad triggers are wired up when Roland "
            "publishes a confirmed CC map for the looper."
        ),
        Shot("push2_SP-404_looper.png",
             "Push 2 — SP-404 Looper mode."),
        H2("Encoders + buttons reference"),
        DataTable([
            ["Control", "Function"],
            ["Tempo encoder (leftmost)", "Nudges Compa's master clock BPM by 0.1 BPM per detent (1.0 with Shift)"],
            ["Master encoder", "Master volume (placeholder; lands when Compa's master mixer ships)"],
            ["Swing encoder", "Pattern-mode swing 0&ndash;50%"],
            ["8 main encoders (top row)", "Drive the focused device's encoder page; top select buttons 1&ndash;N jump between pages"],
            ["Touch strip (outside DJ)", "CC 1 (mod wheel) on the focused device's main channel"],
            ["Transport (Play / Stop / Record)", "Drive Compa's transport for the focused device"],
            ["Note", "Jump to Keys tab"],
            ["Session", "Jump to Session screen"],
            ["Browse", "Jump to Files browser"],
            ["Device", "Jump to Control tab"],
            ["New", "Add chain step (chain tab) or clear pattern grid (pattern tab)"],
            ["Mute", "Toggle mute on the active SP bus (CC 7 0/100 with last non-zero stored for restore)"],
        ], widths=[2.0*inch, 5.0*inch]),
        H2("Push 2 keys + chord modes"),
        *Bullets([
            "<b>Keys mode LCD</b> — full-screen piano keyboard, rolling note roll, chord recognition (Cmaj7, F#m7b5, slash chords) on both the Push 2 LCD and the touchscreen.",
            "<b>Chord layout</b> — every pad plays a full chord. 8 columns are the diatonic chord positions (I&ndash;vii&deg;+I'); rows are variations (root, +7, 1st inv, 2nd inv, +1 octave). Tap LAYOUT to cycle chromatic &rarr; in-key &rarr; chord.",
            "<b>Arpeggiator</b> — across all keys layouts. Encoders control rate, octaves, stab, swing, density, inversion, humanize, accent. Top buttons are pattern shortcuts (UP / DOWN / UP-DN / DN-UP / RANDOM / OFF) plus RESTART and HOLD. Tempo follows the P-6's BPM live.",
            "<b>Top scale buttons</b> — Major, Minor, Pent, Blues, Dorian, Mixolydian.",
            "<b>Bottom root buttons</b> — C&ndash;B, with Shift+root for the sharp variant.",
            "<b>SP-404 chromatic mode</b> — Push 2 grid auto-aligns to whichever pad you're playing chromatically &mdash; bottom-left of the grid is now the SP's bend-window low end. Out-of-range pads stay dimly lit so the layout is always visually complete.",
        ]),
        PageBreak(),
    ]


def section_workflows():
    return [
        H1("Workflows", anchor="workflows"),
        Lead(
            "Common tasks, end-to-end. Each workflow stitches together "
            "screens you've already met."
        ),
        H2("Cross-device flow — record, slice, build, push"),
        P("The most common Compa session:"),
        *Bullets([
            "<b>1. Record</b> — capture audio from the SP-404 (or P-6, or radio) on the Record screen.",
            "<b>2. Sample</b> — slice the recording on the Sample screen. Trim, normalize, auto-slice.",
            "<b>3. Kit Builder</b> — drop slices onto the 128-pad grid. Smart import auto-detects kick / snare / hat / etc.",
            "<b>4. XFER</b> — push the finished kit to your MPC / Force as an XPM, or export an Ableton Drum Rack to the Samba share.",
        ]),
        H2("Basic manual recording"),
        *Bullets([
            "Go to the Record screen (F4).",
            "Tap <b>IN</b> to select the device you want to record from.",
            "Press <b>R</b> or tap the Record button to start.",
            "Press <b>R</b> again to stop. The take appears in the take list on the right.",
        ]),
        H2("Threshold recording"),
        *Bullets([
            "Go to Settings (F8) and set the threshold level (typical: -30 dBFS).",
            "Go to the Record screen (F4).",
            "Tap <b>T</b> to enable threshold mode.",
            "Recording starts the moment signal exceeds threshold and stops after the configured silence duration.",
        ]),
        H2("Auto-record with transport"),
        *Bullets([
            "Press <b>A</b> to enable auto-record.",
            "Start playback on your device. Compa begins recording automatically.",
            "Stop playback. Compa stops recording and saves the take.",
        ]),
        H2("Recall buffer capture"),
        *Bullets([
            "Realize you just heard something great but weren't recording.",
            "Go to the Record screen (F4).",
            "Tap <b>Recall</b> to save the last 60 seconds.",
            "The captured audio appears as a new take.",
        ]),
        H2("Radio capture"),
        *Bullets([
            "Go to the Radio screen (F6).",
            "Browse genres and select a station.",
            "When you hear something interesting, tap <b>Capture</b>.",
            "The last 60 seconds of radio audio are saved as a WAV file.",
        ]),
        H2("Trimming a recording"),
        *Bullets([
            "Go to the Sample screen (F5).",
            "Open the file browser and select a recording.",
            "Drag the start and end markers on the waveform.",
            "Tap <b>Trim</b> to remove everything outside the selection.",
        ]),
        H2("Auto-slicing a loop"),
        *Bullets([
            "Load a loop on the Sample screen.",
            "Tap <b>Auto-Slice</b>.",
            "Adjust sensitivity until slices land on the transients.",
            "Each slice is saved as a separate file.",
            "Export individual slices or the entire set.",
        ]),
        H2("Normalizing for consistent levels"),
        *Bullets([
            "Load a sample on the Sample screen.",
            "Select the region you want to normalize (or select all).",
            "Tap <b>Normalize</b>.",
            "The peak level is brought to 0 dBFS.",
        ]),
        H2("Exporting to a device"),
        *Bullets([
            "Edit and prepare your sample on the Sample screen.",
            "Tap <b>Export</b>.",
            "Choose the target: P-6, SP-404, MPC, or Ableton.",
            "Compa converts the format and transfers the file.",
        ]),
        H2("Building a drum kit"),
        *Bullets([
            "Open Kit Builder.",
            "Tap pad A1 to select it.",
            "Tap <b>Load</b> and browse to your kick drum.",
            "Repeat for each pad: snare on A2, hi-hat on A3, etc.",
            "Use banks B&ndash;H for additional sounds.",
        ]),
        H2("Exporting to MPC / Force"),
        *Bullets([
            "Build your kit.",
            "Tap <b>Export &rarr; XPM</b>.",
            "Compa generates the .xpm drum program.",
            "Transfer to your MPC or Force via network or USB Computer Mode.",
        ]),
        H2("Exporting to Ableton Live"),
        *Bullets([
            "Build your kit.",
            "Tap <b>Export &rarr; ADG</b>.",
            "The .adg and samples save to the Samba share.",
            "On your Mac/PC, open the network share and drag the .adg into a Live session.",
        ]),
        PageBreak(),
    ]


def section_audio_routing():
    return [
        H1("Audio routing in detail", anchor="audio-routing"),
        H2("Source selection"),
        P(
            "On the Record screen, tap the <b>IN</b> selector to choose "
            "which device to record from. The options include every "
            "connected USB audio device and the radio stream."
        ),
        H2("Device-to-device routing"),
        P(
            "In Settings &rarr; Audio Routing, route audio between any "
            "two connected devices. Useful for resampling: play a pattern "
            "on one device and record its output directly into another."
        ),
        H2("Sample rate conversion"),
        P(
            "Compa automatically converts between sample rates when "
            "routing audio between devices. The conversion is transparent:"
        ),
        *Bullets([
            "48 kHz &rarr; 44.1 kHz (e.g., SP-404 to P-6).",
            "44.1 kHz &rarr; 48 kHz (e.g., P-6 to SP-404).",
        ]),
        P("No manual configuration is needed."),
        H2("Radio audio path"),
        P(
            "Radio streams play through the HDMI audio output on the Pi. "
            "USB audio recording is handled on a separate path. You can "
            "monitor radio on speakers while simultaneously recording from "
            "a USB device with no crosstalk or routing conflict."
        ),
        H2("Format conversion reference"),
        DataTable([
            ["Target", "Format", "Sample rate", "Bit depth", "Channels", "Destination"],
            ["Roland P-6", "WAV", "44 100 Hz", "16-bit", "Mono", "Direct USB transfer"],
            ["Roland SP-404 MK2", "WAV", "48 000 Hz", "16-bit", "Stereo", "IMPORT folder on SP SD card"],
            ["Akai MPC / Force", "XPM drum program + WAVs", "—", "—", "—", "Network share or USB Computer Mode"],
            ["Ableton Live", "ADG Drum Rack + WAVs", "—", "—", "—", "Samba share on the Pi"],
        ], widths=[1.3*inch, 1.4*inch, 0.9*inch, 0.7*inch, 0.6*inch, 2.1*inch]),
        PageBreak(),
    ]


def section_link_audio():
    return [
        H1("Ableton Link Audio", anchor="link-audio"),
        Lead(
            "Compa speaks Ableton's <b>Link Audio</b> protocol &mdash; "
            "the audio sibling of Ableton Link. With Link Audio enabled, "
            "Compa appears in <b>Ableton Live 12.4 (or higher)</b> as a "
            "live network audio source: a stereo channel you can route "
            "to any Live track input. No USB cable to the laptop, no "
            "audio interface in the chain. Whatever Compa is recording "
            "&mdash; the SP-404, the P-6, a radio capture, an EXT SOURCE "
            "feed &mdash; streams over Wi-Fi or Ethernet straight into Live."
        ),
        Note(
            "<b>Requires Ableton Live 12.4 or newer.</b> Live 12.4 is the "
            "first public release with the Link Audio receiver. On older "
            "Live versions, only the tempo half of Link works (which "
            "Compa has shipped since v0.1.1)."
        ),
        H2("What Link Audio gets you"),
        *Bullets([
            "<b>Compa as a Live track input.</b> In Live 12.4, the audio-in dropdown lists Compa as a network channel. Arm a track, hit record on Live, and capture Compa's signal at full 48 kHz / 16-bit stereo &mdash; no cables.",
            "<b>Latency-friendly.</b> Compa sends audio in 2048-frame hops (~42.7 ms at 48 kHz), well inside Live's default 100 ms network-input tolerance. Live's plug-in delay compensation handles the round trip.",
            "<b>Tempo + audio together.</b> The same Link mesh that already keeps Compa, the SP/P-6, your iPad, and Live tempo-locked also carries the audio stream. One toggle, one network, one source of truth.",
            "<b>Multi-Compa sessions.</b> Two Compas on the same network each show up as their own Link Audio source &mdash; one from your studio rig, one from a guest's, both feeding Live tracks at once.",
            "<b>Decoupled from USB.</b> No more &ldquo;does this Mac see the Pi as a class-compliant audio interface?&rdquo; The audio is on the network, not the USB bus.",
        ]),
        H2("How it works under the hood"),
        P(
            "Compa's recorder hands captured audio to a Link Audio "
            "broadcaster running alongside the Link tempo client. The "
            "broadcaster decouples the bursty USB capture path from the "
            "steady cadence Live expects: a 250 ms ring buffer absorbs "
            "USB jitter, and a dedicated send worker drains the ring at "
            "exactly HOP / sample-rate intervals, anchored to an absolute "
            "start time so cumulative drift can't accumulate."
        ),
        P(
            "Whatever the recorder's <b>IN</b> selector is pointed at &mdash; "
            "SP-404, P-6, generic USB audio device, or the radio stream "
            "&mdash; is what Live receives. Switching the IN source on "
            "Compa instantly changes what's flowing into Live's track."
        ),
        H2("Setup"),
        *Bullets([
            "<b>1. Update Compa.</b> Settings &rarr; Updates &rarr; Update now (Link Audio shipped post-v0.1.1).",
            "<b>2. Update Live.</b> Make sure Live is 12.4 or higher.",
            "<b>3. Same network.</b> Pi and laptop must be on the same LAN. Wired Ethernet is recommended; Wi-Fi works on Pi 4 / 5 with a clean signal.",
            "<b>4. Enable Link.</b> On Compa: Settings &rarr; ABLETON LINK &rarr; Enable Link. The LINK indicator on the session screen pulses green when peers are connected.",
            "<b>5. Pick the input.</b> On Compa's Record screen, tap <b>IN</b> and choose the device or stream you want to send to Live.",
            "<b>6. In Live, add a track input.</b> Open Live's Preferences &rarr; Link / Tempo &rarr; verify Link is on. Create an audio track, set its input dropdown to <b>Compa</b> (it appears under network sources), arm the track, and monitor / record as normal.",
        ]),
        H2("Hardware recommendations"),
        Note(
            "<b>Pi 5 strongly recommended for Link Audio.</b> Pi 5's Ethernet "
            "is on a dedicated PCIe lane and its USB host controller is "
            "separate from the network stack &mdash; so a USB capture from "
            "the SP-404 doesn't compete with the network send. Real-time "
            "performance is clean."
        ),
        DataTable([
            ["Pi model", "Link Audio support", "Why"],
            ["Pi 5",
             "<b>Recommended.</b> Full real-time, Wi-Fi or Ethernet.",
             "PCIe Ethernet, separate USB host controller, plenty of CPU headroom."],
            ["Pi 4",
             "Works. Use Ethernet for a stable feed; Wi-Fi can drop frames if the signal is busy.",
             "Strong CPU; USB 3 + gigabit Ethernet on the same controller share bandwidth but rarely saturate."],
            ["Pi 3B / 3B+",
             "<b>Not recommended.</b> USB capture itself caps at ~75% of real-time.",
             "Shared USB / Ethernet bus on the same controller. Even with wired Ethernet, the USB capture path can't keep up with the real-time send rate, so Live receives a choppy stream regardless of network speed."],
        ], widths=[1.0*inch, 2.4*inch, 3.6*inch]),
        H2("Troubleshooting Link Audio"),
        H3("Compa doesn't show up in Live's input list"),
        *Bullets([
            "Verify Live is 12.4 or newer. Older versions don't have the Link Audio receiver.",
            "Verify Link is enabled in <b>both</b> Compa (Settings &rarr; ABLETON LINK) and Live (Preferences &rarr; Link / Tempo).",
            "Check that both devices are on the same subnet. Some routers isolate Wi-Fi clients from each other &mdash; disable AP isolation, or move both onto Ethernet.",
            "Some VPNs hijack the multicast traffic Link uses for discovery. Disable VPNs while testing.",
        ]),
        H3("Audio drops out, glitches, or stutters"),
        *Bullets([
            "First check: are you on a Pi 3B? If yes, see the hardware table above &mdash; the limit is the USB bus, not the network.",
            "On Pi 4, switch to wired Ethernet if you're on Wi-Fi. Wi-Fi packet loss shows up as audio drops.",
            "Check Live's preferences &rarr; Link / Tempo &rarr; latency. The default 100 ms is fine for Compa's 42.7 ms hop, but if you've raised buffer sizes elsewhere you may need more.",
            "On a noisy network, try moving Compa and the laptop into a dedicated subnet or use a small dedicated switch between them.",
        ]),
        H3("Tempo is locked but audio isn't flowing"),
        *Bullets([
            "Make sure the Compa recorder's <b>IN</b> source is set to a device that's actually producing signal. Link Audio sends what the recorder captures; if the recorder has nothing on its input, Live receives silence.",
            "Confirm the Live track is armed and its input meter is responding.",
        ]),
        PageBreak(),
    ]


def section_keyboard_shortcuts():
    return [
        H1("Keyboard shortcuts", anchor="shortcuts"),
        Lead(
            "Compa supports a USB or Bluetooth keyboard for quick "
            "navigation and control. Useful for development, debugging, "
            "or running Compa headlessly with a connected display."
        ),
        DataTable([
            ["Key", "Action"],
            ["F1", "Switch to Session screen"],
            ["F2", "Switch to Control screen"],
            ["F3", "Switch to Pattern screen"],
            ["F4", "Switch to Record screen"],
            ["F5", "Switch to Sample screen"],
            ["F6", "Switch to Radio screen"],
            ["F7", "Switch to Help screen"],
            ["F8", "Switch to Settings screen"],
            ["Space", "Toggle transport (play/stop) on focused device"],
            ["R", "Toggle recording on/off"],
            ["A", "Toggle auto-record mode"],
            ["M", "Toggle mouse mode"],
            ["F12", "Take a screenshot (saved to ~/compa/screenshots/)"],
            ["SIGUSR1", "Save the current Push 2 display frame (kill -USR1 &lt;compa-pid&gt;)"],
        ], widths=[1.0*inch, 6.0*inch]),
        PageBreak(),
    ]


def section_file_locations():
    return [
        H1("File locations", anchor="files"),
        Lead("Where Compa keeps its data on the Pi."),
        DataTable([
            ["Path", "What lives there"],
            ["~/compa/recordings/", "Audio recordings and takes"],
            ["~/compa/backups/", "Device backup archives"],
            ["~/compa/exports/", "Exported samples and kits (.xpm, .adg)"],
            ["~/compa/screenshots/", "Screenshots from F12 and the Settings capture buttons"],
            ["~/compa/sessions/", "Session files with notes and state"],
            ["~/compa/radio/", "Captured radio audio files"],
            ["~/compa/videos/", "Screen recordings"],
            ["~/compa/kits/", "Saved kit JSON files"],
            ["~/.config/compa/touch_calibration.json", "Touch calibration transform"],
            ["sessions/compa_step_grids.json", "Push 2 overlay-sequencer step grids"],
        ], widths=[2.7*inch, 4.3*inch]),
        H2("Setup files"),
        DataTable([
            ["File", "Purpose"],
            ["setup/config.env", "Audio buffer size, sample rate, base note, optional Mac/PC sample mount"],
            ["setup/01-base-setup.sh", "System packages, Python, venv"],
            ["setup/02-audio-setup.sh", "ALSA config, USB audio permissions"],
            ["setup/03-network-mounts.sh", "SSHFS mount, Samba share"],
            ["setup/04-autostart.sh", "systemd service, boot-to-Compa"],
        ], widths=[2.2*inch, 4.8*inch]),
        H2("Configuration"),
        P(
            "Edit <code>setup/config.env</code> to customize your setup:"
        ),
        Code(
            "# Network sample library (optional — mount from Mac/PC via SSHFS)\n"
            "MAC_MINI_IP=192.168.1.XXX\n"
            "MAC_MINI_USER=charlie\n"
            "REMOTE_SAMPLE_DIR=/Users/charlie/Music/Samples\n\n"
            "# Audio\n"
            "AUDIO_DEVICE=default\n"
            "BUFFER_SIZE=256          # 256 frames = ~5.8ms latency at 44.1kHz\n"
            "SAMPLE_RATE=44100\n\n"
            "# MIDI\n"
            "MIDI_BASE_NOTE=36        # Bottom pad = MIDI note 36 (C2)"
        ),
        PageBreak(),
    ]


def section_tips():
    return [
        H1("Tips & best practices", anchor="tips"),
        *Bullets([
            "<b>Always use data-capable USB cables.</b> Charge-only cables are the most common source of connection problems.",
            "<b>Keep the recall buffer in mind.</b> Even if you forget to press record, the last 60 seconds are always available.",
            "<b>Use threshold recording for hands-free capture.</b> Set it up before a jam session and let Compa handle the recording automatically.",
            "<b>Export directly to your device when possible.</b> Compa handles format conversion so you don't need to worry about sample rates or bit depths.",
            "<b>Back up your device data regularly</b> using the Session screen. Backups are small and fast, and they protect against accidental data loss.",
            "<b>Use the Kit Builder to prepare drum kits before a session.</b> Having kits ready to load on your MPC or in Ableton saves time during creative work.",
            "<b>The MIDI clock relay eliminates the need for a separate sync box.</b> Designate one device as master and let Compa distribute clock to the rest.",
            "<b>Take advantage of the multi-device hub.</b> With four USB ports, you can have a P-6, SP-404, and MPC all connected and switch between them with a single tap.",
            "<b>Plug in a Push 2 if you have one.</b> No Live, no host. Compa drives it directly from the Pi and the surface re-roles to whichever workspace tab you have focused.",
            "<b>Enable Ableton Link</b> if you work with iPad apps, Live, or other Compas — Compa joins the Link session and broadcasts MIDI clock to every connected groovebox.",
        ]),
        PageBreak(),
    ]


def section_troubleshooting():
    return [
        H1("Troubleshooting", anchor="troubleshooting"),
        H2("Device not detected"),
        *Bullets([
            "Verify the USB cable is data-capable (not charge-only).",
            "Try a different USB port on the Pi.",
            "Check that the power supply is rated at 2.5 A or higher (3 A for Pi 4).",
            "Wait at least 5 seconds for the scan cycle to detect the device.",
        ]),
        H2("Audio glitches or dropouts"),
        *Bullets([
            "Use the official Raspberry Pi power supply.",
            "Close unnecessary background processes on the Pi.",
            "If routing between devices, ensure both are stable.",
            "Consider a powered USB hub for multiple devices.",
            "On Pi 3B, audio routing between two USB devices on the shared USB bus can glitch — use Pi 4 / 5 or analog cables (one device's headphone out into another device's EXT SOURCE input).",
        ]),
        H2("Touchscreen not responding accurately"),
        *Bullets([
            "Go to Settings (F8) and run Touch Calibration.",
            "Follow the on-screen prompts to recalibrate (4 corners + center).",
            "Calibration persists at <code>~/.config/compa/touch_calibration.json</code>.",
        ]),
        H2("Radio not playing"),
        *Bullets([
            "Verify the Pi has an active internet connection.",
            "Check that the HDMI audio output is enabled in Pi settings.",
            "Try a different radio station — some stations go offline.",
        ]),
        H2("Samba share not visible"),
        *Bullets([
            "Verify Samba is installed and running on the Pi.",
            "Check that your Mac or PC is on the same network.",
            "Review <code>/etc/samba/smb.conf</code> for correct share configuration.",
        ]),
        H2("MIDI clock not syncing"),
        *Bullets([
            "Settings &rarr; MIDI Clock Relay — verify a master device is selected.",
            "Ensure all devices are set to receive external clock.",
            "If using Ableton Link, enable <b>Send MIDI Clock</b> and set each device to External / Auto.",
        ]),
        H2("Updates button doesn't work"),
        *Bullets([
            "Verify the Pi has internet access.",
            "If Compa was installed from a flashed image, the .git folder needs to survive the build — fixed in v0.1.1. If you're on an older image, re-flash with the latest Compa OS image.",
            "Manual update: SSH in and run <code>cd ~/compa &amp;&amp; git pull &amp;&amp; sudo systemctl restart compa</code>.",
        ]),
        PageBreak(),
    ]


def section_credits():
    return [
        H1("Credits & license", anchor="credits"),
        H2("Created by RARE DATA"),
        P(
            "Compa is designed and maintained by <b>Rare Data</b> &mdash; "
            "a Southern California music software studio founded by "
            "<b>Mac Digi</b>, focused on hardware-first tools for "
            "producers. <b>raredata.net</b>."
        ),
        H2("Open-source license"),
        P(
            "Compa source is released under the <b>MIT License</b>. The "
            "name &ldquo;Compa&rdquo;, the Compa logo, and the Rare Data "
            "branding are trademarks of Rare Data LLC."
        ),
        H2("Fonts"),
        *Bullets([
            "<b>Inter</b> by Rasmus Andersson.",
            "<b>JetBrains Mono</b> by JetBrains.",
        ]),
        H2("Trademarks"),
        P(
            "Roland P-6, SP-404 MK2, Akai Force, Akai MPC, Ableton Push 2, "
            "Ableton Live, Ableton Link, Midi Fighter Twister, and "
            "PreSonus ATOM SQ are trademarks of their respective owners. "
            "Compa is an independent project and is not affiliated with or "
            "endorsed by Roland, Akai, Ableton, DJ TechTools, or PreSonus."
        ),
        H2("Where to find us"),
        *Bullets([
            "Website: <b>raredata.net</b>",
            "Compa: <b>raredata.net/compa</b>",
            "GitHub: <b>github.com/macdigi/compa</b>",
            "Main YouTube: <b>@raredatanet</b>",
            "Radio YouTube: <b>@RAREDATARADIO</b>",
        ]),
        Spacer(1, 24),
        HR(),
        Paragraph(
            "<b>Compa &mdash; User Manual</b><br/>"
            f"Version {VERSION} &nbsp;·&nbsp; {date.today().isoformat()}<br/>"
            "<br/>"
            "© 2026 Rare Data LLC. All rights reserved.<br/>"
            "Document content reproduces and extends the on-device manual "
            f"shipped with Compa v{VERSION}.",
            ParagraphStyle("colofon", parent=STYLES["Body"],
                           alignment=TA_CENTER, textColor=SOFT,
                           fontSize=9, leading=13),
        ),
    ]


# --- main ----------------------------------------------------------------

def main():
    doc = CompaDocTemplate(
        str(OUT),
        pagesize=LETTER,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN + 0.05 * inch, bottomMargin=MARGIN + 0.1 * inch,
        title="Compa — User Manual",
        author="Rare Data LLC",
        subject="Compa Touchscreen Companion User Manual",
        creator="Rare Data LLC",
    )

    cover_frame = Frame(0, 0, PAGE_W, PAGE_H, leftPadding=0,
                        rightPadding=0, topPadding=0, bottomPadding=0,
                        showBoundary=0)
    body_frame = Frame(MARGIN, MARGIN + 0.05 * inch,
                       CONTENT_W, CONTENT_H - 0.4 * inch,
                       leftPadding=0, rightPadding=0,
                       topPadding=0, bottomPadding=0)

    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[cover_frame], onPage=cover_page),
        PageTemplate(id="body", frames=[body_frame], onPage=body_page),
    ])

    story = []

    # Cover (uses cover template)
    story += build_cover()
    story += [NextPageTemplate("body"), PageBreak()]

    # TOC
    story += build_toc()

    # Sections
    story += section_intro()
    story += section_at_a_glance()
    story += section_hardware()
    story += section_install()
    story += section_multidevice()
    story += section_screen_session()
    story += section_screen_control()
    story += section_screen_pattern()
    story += section_screen_record()
    story += section_screen_sample()
    story += section_screen_radio()
    story += section_screen_kit()
    story += section_screen_xfer()
    story += section_screen_settings()
    story += section_screen_help()
    story += section_workspaces()
    story += section_midi_controllers()
    story += section_push2()
    story += section_workflows()
    story += section_audio_routing()
    story += section_link_audio()
    story += section_keyboard_shortcuts()
    story += section_file_locations()
    story += section_tips()
    story += section_troubleshooting()
    story += section_credits()

    doc.multiBuild(story)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
