"""PDF inspection report built from the stored inspection record (never from demo constants)."""
import io
import os
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.pdfgen import canvas as rl_canvas  # noqa: E402
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, PageTemplate, Paragraph,  # noqa: E402
                                Spacer, Table, TableStyle)

from . import config, database, ocr_engine  # noqa: E402

INK = colors.HexColor("#172B45")
MUTED = colors.HexColor("#687B91")
LINE = colors.HexColor("#DFE7F0")
PANEL = colors.HexColor("#F4F7FB")
BLUE = colors.HexColor("#1769E8")
CYAN = colors.HexColor("#00B8D9")
PASS = colors.HexColor("#16A36A")
WARN = colors.HexColor("#E9A23B")
FAIL = colors.HexColor("#DC4545")
SIM = colors.HexColor("#B7791F")

_fonts_ready = False


def _fonts():
    global _fonts_ready
    if _fonts_ready:
        return
    pdfmetrics.registerFont(TTFont("ICV", os.path.join(config.FONT_DIR, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("ICV-Bold", os.path.join(config.FONT_DIR, "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("ICV-Mono", os.path.join(config.FONT_DIR, "DejaVuSansMono.ttf")))
    from reportlab.pdfbase.pdfmetrics import registerFontFamily
    registerFontFamily("ICV", normal="ICV", bold="ICV-Bold", italic="ICV", boldItalic="ICV-Bold")
    _fonts_ready = True


def styles():
    base = dict(fontName="ICV", textColor=INK, alignment=TA_LEFT)
    return {
        "title": ParagraphStyle("title", fontSize=17, leading=21, fontName="ICV-Bold", **{k: v for k, v in base.items() if k != "fontName"}),
        "sub": ParagraphStyle("sub", fontSize=9.5, leading=13, textColor=MUTED, fontName="ICV"),
        "h": ParagraphStyle("h", fontSize=11.5, leading=15, fontName="ICV-Bold", textColor=INK, spaceBefore=10, spaceAfter=5, keepWithNext=1),
        "result": ParagraphStyle("result", fontSize=12, leading=17, fontName="ICV-Bold", textColor=INK, spaceAfter=3),
        "body": ParagraphStyle("body", fontSize=8.6, leading=11.6, **base),
        "small": ParagraphStyle("small", fontSize=7.4, leading=9.6, fontName="ICV", textColor=MUTED),
        "cell": ParagraphStyle("cell", fontSize=7.8, leading=9.8, **base),
        "cellb": ParagraphStyle("cellb", fontSize=7.8, leading=9.8, fontName="ICV-Bold", textColor=INK),
        "mono": ParagraphStyle("mono", fontSize=7.6, leading=9.8, fontName="ICV-Mono", textColor=INK),
    }


STATUS_COLOR = {"PASS": PASS, "WARN": WARN, "FAIL": FAIL, "INVALID": FAIL, "pass": PASS, "warn": WARN, "fail": FAIL}
STATUS_TEXT = {"NOT_MEASURED": "Not measured", "NO_REFERENCE": "No reference", "NOT_APPLICABLE": "N/A",
               "not_assessed": "Not assessed", "INVALID": "Invalid", "pass": "Pass", "warn": "Warning", "fail": "Fail",
               "PASS": "Pass", "WARN": "Warning", "FAIL": "Fail"}
CLASS_COLOR = {"consistent": PASS, "investigate": WARN, "deviation": FAIL, "inconclusive": MUTED}


def esc(s):
    return (str(s) if s is not None else "—").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def P(text, st):
    return Paragraph(text, st)


def status_par(status, st):
    c = STATUS_COLOR.get(status, MUTED)
    return Paragraph(f'<font color="{c.hexval()}"><b>{esc(STATUS_TEXT.get(status, status))}</b></font>', st)


def table(rows, widths, header=True, zebra=False):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    ts = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
          ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
          ("TOPPADDING", (0, 0), (-1, -1), 3.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2)]
    if header:
        ts += [("BACKGROUND", (0, 0), (-1, 0), PANEL), ("LINEBELOW", (0, 0), (-1, 0), 0.8, LINE)]
    t.setStyle(TableStyle(ts))
    return t


def kv_table(pairs, s, widths=(42 * mm, 132 * mm)):
    rows = [[P(f"<b>{esc(k)}</b>", s["cell"]), P(esc(v), s["cell"])] for k, v in pairs]
    return table(rows, list(widths), header=False)


# ------------------------------------------------------------------ charts
def _chart_current(series, simulated):
    if not series:
        return None
    fig, ax = plt.subplots(figsize=(7.0, 2.2), dpi=200)
    for phase, col, lab in (("static", "#1769E8", "Supply monitoring"), ("functional", "#00B8D9", "Functional test")):
        pts = [p for p in series if p.get("phase") == phase and p.get("valid") and p.get("i") is not None]
        if pts:
            ax.plot([p["t"] for p in pts], [p["i"] for p in pts], color=col, lw=1.0, label=lab,
                    linestyle="--" if simulated else "-")
    ax.set_xlabel("Time (s)", fontsize=7, color="#687B91")
    ax.set_ylabel("Supply current (mA)", fontsize=7, color="#687B91")
    ax.tick_params(labelsize=6.5, colors="#687B91")
    for sp in ax.spines.values():
        sp.set_color("#DFE7F0")
    ax.grid(color="#EEF2F7", lw=0.6)
    ax.legend(fontsize=6.5, frameon=False, loc="upper right")
    if simulated:
        ax.text(0.01, 0.92, "SIMULATED", transform=ax.transAxes, fontsize=7, color="#B7791F", weight="bold")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf


def _chart_iv(iv, simulated):
    if not iv or not iv.get("points"):
        return None
    fig, ax = plt.subplots(figsize=(4.2, 2.4), dpi=200)
    ax.plot([p["v"] for p in iv["points"]], [p["i_ua"] for p in iv["points"]], color="#1769E8", lw=1.3, marker="o", ms=2,
            label="Measured" + (" (simulated)" if simulated else ""))
    ref = iv.get("reference")
    if ref and ref.get("points"):
        ax.plot([p["v"] for p in ref["points"]], [p["i_ua"] for p in ref["points"]], color="#687B91", lw=1.0, linestyle=":",
                label="Reference")
    ax.set_xlabel("Pin voltage (V)", fontsize=7, color="#687B91")
    ax.set_ylabel("Current (µA)", fontsize=7, color="#687B91")
    ax.tick_params(labelsize=6.5, colors="#687B91")
    for sp in ax.spines.values():
        sp.set_color("#DFE7F0")
    ax.grid(color="#EEF2F7", lw=0.6)
    ax.legend(fontsize=6.5, frameon=False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf


def _img(path, max_w, max_h):
    from PIL import Image as PILImage
    with PILImage.open(path) as im:
        w, h = im.size
    s = min(max_w / w, max_h / h)
    return Image(path, width=w * s, height=h * s)


# ------------------------------------------------------------------ page furniture
class NumberedCanvas(rl_canvas.Canvas):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._pages = []

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for st in self._pages:
            self.__dict__.update(st)
            self.setFont("ICV", 7)
            self.setFillColor(MUTED)
            self.drawRightString(A4[0] - 16 * mm, 10 * mm, f"Page {self._pageNumber} of {total}")
            super().showPage()
        super().save()


def _draw_logo(c, x, y):
    c.setFillColor(BLUE)
    c.roundRect(x, y, 9 * mm, 9 * mm, 1.6 * mm, fill=1, stroke=0)
    c.setStrokeColor(colors.white)
    c.setLineWidth(0.8)
    for k in range(3):
        yy = y + (2.4 + 2.1 * k) * mm
        c.line(x + 1.2 * mm, yy, x + 2.6 * mm, yy)
        c.line(x + 6.4 * mm, yy, x + 7.8 * mm, yy)
    c.setFillColor(colors.white)
    c.rect(x + 2.9 * mm, y + 1.8 * mm, 3.2 * mm, 5.4 * mm, fill=1, stroke=0)
    c.setFillColor(CYAN)
    c.circle(x + 3.6 * mm, y + 6.3 * mm, 0.45 * mm, fill=1, stroke=0)
    c.setFillColor(INK)
    c.setFont("ICV-Bold", 12)
    c.drawString(x + 11.5 * mm, y + 4.6 * mm, "IC VISION")
    c.setFont("ICV", 6.6)
    c.setFillColor(MUTED)
    c.drawString(x + 11.5 * mm, y + 1.2 * mm, "Every chip leaves an electrical signature")


def _page_decor(ins, report_id, generated):
    simulated = ins.get("data_source") == "simulated"

    def draw(c, doc):
        c.saveState()
        W, H = A4
        _draw_logo(c, 16 * mm, H - 21 * mm)
        c.setFont("ICV-Bold", 9)
        c.setFillColor(INK)
        c.drawRightString(W - 16 * mm, H - 14 * mm, "Semiconductor Inspection Report")
        c.setFont("ICV", 7.2)
        c.setFillColor(MUTED)
        c.drawRightString(W - 16 * mm, H - 18 * mm, f"{report_id}  |  {generated}")
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(16 * mm, H - 24 * mm, W - 16 * mm, H - 24 * mm)
        c.line(16 * mm, 14 * mm, W - 16 * mm, 14 * mm)
        c.setFont("ICV", 7)
        c.setFillColor(MUTED)
        c.drawString(16 * mm, 10 * mm, f"IC VISION {config.APP_VERSION} — screening result, not a certificate of authenticity")
        if simulated:
            c.setFillColor(SIM)
            c.setFillAlpha(0.10)
            c.setFont("ICV-Bold", 54)
            c.translate(W / 2, H / 2)
            c.rotate(35)
            c.drawCentredString(0, 0, "SIMULATED DATA")
        c.restoreState()

    return draw


# ------------------------------------------------------------------ builder
def build_report(iid):
    _fonts()
    ins = database.get_inspection(iid)
    if not ins:
        raise ValueError("Inspection not found")
    if ins.get("status") != "completed" or not ins.get("scores"):
        raise ValueError("Compute the screening result before generating a report")
    s = styles()
    simulated = ins.get("data_source") == "simulated"
    report_id = "RPT-" + iid.replace("ICV-", "")
    generated = _local_ts(datetime.now().astimezone())
    path = os.path.join(config.REPORT_DIR, f"{report_id}.pdf")
    doc = BaseDocTemplate(path, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=28 * mm, bottomMargin=19 * mm,
                          title=f"IC VISION Inspection Report {report_id}", author="IC VISION", subject=iid)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=_page_decor(ins, report_id, generated))])
    comp = ins.get("component") or {}
    scores = ins["scores"]
    dev = ins.get("device") or {}
    cfg = ins.get("config") or {}
    ref = ins.get("reference_snapshot") or database.get_reference(ins.get("reference_id"))
    meas = ins.get("measurements") or {}
    W = doc.width
    story = []

    if simulated:
        banner = Table([[P('<font color="#FFFFFF"><b>SIMULATED DATA</b> — every measurement, functional result and optical '
                           'assessment in this report was generated by the IC VISION simulator. It is not a physical measurement.</font>', s["cell"])]],
                       colWidths=[W])
        banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SIM), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                                    ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story += [banner, Spacer(1, 6)]

    story.append(P(esc(comp.get("name") or "Component inspection"), s["title"]))
    story.append(P(f"{esc(comp.get('manufacturer') or 'Manufacturer not identified')} · {esc(comp.get('part_number') or 'Part number not identified')}", s["sub"]))
    story.append(Spacer(1, 6))
    meta = [
        ("Report ID", report_id), ("Inspection ID", iid),
        ("Inspection date", _fmt_ts(ins.get("run", {}).get("started_at") or ins.get("created_at"))),
        ("Report generated", generated),
        ("Operator", ins.get("operator") or "—"), ("Device ID", dev.get("label") or dev.get("device_id") or "—"),
        ("Data source", "SIMULATED (generated data)" if simulated else "LIVE HARDWARE (physical measurement)"),
        ("Inspection mode", (cfg.get("mode") or "").title()),
    ]
    rows = [[P(f"<b>{esc(a)}</b>", s["cell"]), P(esc(b), s["cell"]), P(f"<b>{esc(c)}</b>", s["cell"]), P(esc(d), s["cell"])]
            for (a, b), (c, d) in zip(meta[0::2], meta[1::2])]
    story.append(table(rows, [28 * mm, 58 * mm, 30 * mm, W - 116 * mm], header=False))
    story.append(Spacer(1, 8))

    # summary box
    cls_col = CLASS_COLOR.get(scores.get("classification_key"), MUTED)
    ov = scores.get("overall")
    inconclusive = scores.get("classification_key") == "inconclusive"
    if ov is not None and not inconclusive:
        score_html = f'<font size="20"><b>{ov:.1f}</b></font><font color="#687B91" size="8"> / 100</font>'
    elif ov is not None:
        score_html = (f'<font size="16"><b>—</b></font><br/><font color="#687B91" size="7">partial {ov:.1f} over available '
                      f'evidence; not a screening score</font>')
    else:
        score_html = '<font size="16"><b>—</b></font><font color="#687B91" size="8"> no score</font>'
    summ = Table([[P(f'<font color="{cls_col.hexval()}" size="13"><b>{esc(scores["classification"])}</b></font><br/>'
                     f'<font color="#687B91" size="7.5">{esc(scores.get("label"))}</font>', s["body"]),
                   P(score_html, s["body"]),
                   P(f'<font color="#687B91" size="7.5">Evidence coverage</font><br/><b>{scores.get("coverage", 0) * 100:.0f} %</b>', s["body"])]],
                 colWidths=[W * 0.5, W * 0.25, W * 0.25])
    summ.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, LINE), ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                              ("LINEBEFORE", (0, 0), (0, -1), 3, cls_col), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 7),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story.append(summ)
    story.append(Spacer(1, 4))
    story.append(P(esc(" ".join(scores.get("reasons", [])[:2])), s["small"]))

    # 1 component
    story.append(P("1  Component information", s["h"]))
    fixture = ("Simulated fixture (no hardware)" if simulated else
               f"Breadboard {(database.get_profile(ins['profile_id']) or {}).get('package', '')} fixture, ESP32 + {dev.get('sensor') or 'INA2xx'}")
    story.append(kv_table([
        ("Component name", comp.get("name")), ("Manufacturer", comp.get("manufacturer") or "Not identified"),
        ("Part number", comp.get("part_number") or "Not identified"), ("Package", comp.get("package")),
        ("Batch / lot", comp.get("batch") or "Not recorded"), ("Date code", comp.get("date_code") or "Not recorded"),
        ("Test fixture", fixture), ("Identification method", comp.get("identification_method")),
        ("Datasheet reference", ((ref or {}).get("datasheet") or {}).get("title") or "Reference unavailable"),
    ], s))

    # 2 optical
    story.append(P("2  Optical inspection", s["h"]))
    op = ins.get("optical") or {}
    if not cfg.get("optical"):
        story.append(P("Not part of this inspection mode.", s["body"]))
    else:
        ocr = ins.get("ocr") or {}
        img = ins.get("image") or {}
        ident = ocr.get("identification") or {}
        line = (f"OCR engine: {esc(ocr.get('engine') or 'unavailable')}. Extracted text: "
                f"<font name='ICV-Mono'>{esc((ocr.get('edited_text') or ocr.get('text') or '—').replace(chr(10), ' / '))}</font>. "
                f"Mean word confidence (engine-reported): {ocr.get('mean_conf') if ocr.get('mean_conf') is not None else '—'}. "
                f"Identification: {esc(ident.get('message') or ident.get('status') or '—')}")
        if ocr.get("edited_text") and ocr.get("edited_text") != ocr.get("text"):
            line += " Text corrected by operator."
        story.append(P(line, s["body"]))
        story.append(Spacer(1, 4))
        rows = [[P("<b>Inspection parameter</b>", s["cell"]), P("<b>Observed</b>", s["cell"]), P("<b>Reference</b>", s["cell"]),
                 P("<b>Status</b>", s["cell"]), P("<b>Remarks</b>", s["cell"])]]
        for c in op.get("checks", []):
            rows.append([P(esc(c["label"]), s["cell"]), P(esc(c.get("observed") or "—"), s["cell"]),
                         P(esc(c.get("reference") or "—"), s["cell"]), status_par(c["status"], s["cell"]),
                         P(esc(c.get("note") or ""), s["cell"])])
        story.append(table(rows, [36 * mm, 38 * mm, 36 * mm, 18 * mm, W - 128 * mm]))
        osum = op.get("summary") or {}
        story.append(P(f"Optical score: {osum.get('score') if osum.get('score') is not None else 'inconclusive'}"
                       f" (coverage {(osum.get('coverage') or 0) * 100:.0f} %). Visual inspection identifies visible inconsistencies; "
                       "it cannot establish electrical genuineness.", s["small"]))
        imgs = []
        if img.get("image_id") and os.path.exists(ocr_engine.image_path(img["image_id"])):
            cap = "Inspection image" + (" — synthetic rendering (simulation)" if img.get("synthetic") else "")
            imgs.append([_img(ocr_engine.image_path(img["image_id"]), 80 * mm, 42 * mm), P(cap, s["small"])])
        cmp_ = op.get("image_compare") or {}
        if cmp_.get("reference_image") and os.path.exists(cmp_["reference_image"]):
            imgs.append([_img(cmp_["reference_image"], 80 * mm, 42 * mm),
                         P(f"Reference image ({esc(cmp_.get('provenance'))}) — SSIM {cmp_.get('ssim')}", s["small"])])
        if imgs:
            cells = [[i[0] for i in imgs], [i[1] for i in imgs]]
            t = Table(cells, colWidths=[W / 2] * len(imgs))
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
            story += [Spacer(1, 5), t]

    # 3 manufacturer
    story.append(P("3  Manufacturer verification", s["h"]))
    mf = ins.get("manufacturer") or {}
    if not cfg.get("manufacturer"):
        story.append(P("Not part of this inspection mode.", s["body"]))
    elif not ref:
        story.append(P("<b>Reference unavailable.</b> No matching manufacturer record exists in the local reference database, "
                       "so this dimension is inconclusive. The component is not classified as counterfeit on that basis.", s["body"]))
    else:
        ds = ref.get("datasheet") or {}
        story.append(kv_table([
            ("Manufacturer identification", ref.get("manufacturer")),
            ("Datasheet source", f"{ds.get('title')} — {ds.get('doc_id')}, {ds.get('revision')}"),
            ("Datasheet URL", ds.get("url")),
            ("Reference record", f"{ref.get('part_number')} ({ref.get('id')}), status: {(ref.get('verification') or {}).get('status', '').replace('_', ' ')}"),
            ("Dataset availability", f"Local reference dataset {ins.get('reference_dataset_version')}; "
                                     f"{len(ref.get('specs', []))} specification rows"),
        ], s))
        story.append(Spacer(1, 4))
        rows = [[P("<b>Verification factor</b>", s["cell"]), P("<b>Evidence</b>", s["cell"]), P("<b>Points</b>", s["cell"])]]
        for f in mf.get("factors", []):
            rows.append([P(esc(f["label"]), s["cell"]), P(esc(f["detail"]), s["cell"]), P(f"{f['points']:g} / {f['max']}", s["cell"])])
        rows.append([P("<b>Manufacturer score</b>", s["cell"]), P("", s["cell"]), P(f"<b>{mf.get('score')}</b>", s["cell"])])
        story.append(table(rows, [42 * mm, W - 66 * mm, 24 * mm]))
        story.append(P("This score reflects the availability and consistency of reference data. It is not a rating of the manufacturer.", s["small"]))

    # 4 electrical
    story.append(P("4  Electrical fingerprinting", s["h"]))
    el = ins.get("electrical") or {}
    if not cfg.get("electrical"):
        story.append(P("Not part of this inspection mode.", s["body"]))
    else:
        st = meas.get("static") or {}
        iv_, vv = st.get("i") or {}, st.get("v") or {}
        story.append(P(f"{meas.get('n_valid', 0)} valid of {meas.get('n_total', 0)} supply samples over {cfg.get('duration_s')} s "
                       f"({st.get('rate_hz') or '—'} samples/s). Supply {vv.get('mean', 0):.3f} V "
                       f"(min {vv.get('min', 0):.3f}, max {vv.get('max', 0):.3f}); current {iv_.get('mean', 0):.3f} mA "
                       f"(min {iv_.get('min', 0):.3f}, max {iv_.get('max', 0):.3f}, σ {iv_.get('std', 0):.3f})."
                       + (f" Invalid samples excluded: {meas.get('invalid_reasons')}." if meas.get("n_invalid") else ""), s["body"]))
        story.append(Spacer(1, 4))
        rows = [[P(f"<b>{h}</b>", s["cell"]) for h in ("Parameter", "Measured", "Reference", "Unit", "Status", "Remarks")]]
        for e in el.get("evaluations", []):
            r = e.get("reference") or {}
            rtxt = r.get("text") or "—"
            src = r.get("source_type")
            if src:
                rtxt += f" [{src}{', verified' if r.get('verified') else ', unverified' if src in ('oem', 'oem_derived') else ''}]"
            rows.append([P(esc(e["label"]), s["cell"]), P(esc(e.get("measured_text")), s["cell"]), P(esc(rtxt), s["cell"]),
                         P(esc(e.get("unit")), s["cell"]), status_par(e["status"], s["cell"]), P(esc(e.get("remarks")), s["cell"])])
        story.append(table(rows, [30 * mm, 26 * mm, 46 * mm, 11 * mm, 22 * mm, W - 135 * mm]))
        esum = el.get("summary") or {}
        story.append(P(f"Electrical score: {esum.get('score') if esum.get('score') is not None else 'inconclusive'} "
                       f"(coverage {(esum.get('coverage') or 0) * 100:.0f} %). " + esc(" ".join(esum.get("reasons", []))), s["small"]))
        fn = ins.get("functional") or {}
        if fn.get("rows") and fn.get("type") == "logic_truth_table":
            story.append(Spacer(1, 4))
            frows = [[P(f"<b>{h}</b>", s["cell"]) for h in ("Gate", "A", "B", "Expected Y", "Observed Y", "VOUT (ADC)", "Result")]]
            for r in fn["rows"]:
                frows.append([P(esc(r["gate"]), s["cell"]), P(str(r["vector"][0]), s["cell"]), P(str(r["vector"][1]), s["cell"]),
                              P(str(r["expected"]), s["cell"]), P(str(r["observed"]), s["cell"]),
                              P(f"{r['v_out_mv'] / 1000:.3f} V" if r.get("v_out_mv") is not None else "—", s["cell"]),
                              status_par("PASS" if r["pass"] else "FAIL", s["cell"])])
            story.append(KeepTogether([P("Functional truth table" + (" (simulated)" if fn.get("simulated") else ""), s["cellb"]),
                                       table(frows, [24 * mm, 12 * mm, 12 * mm, 22 * mm, 22 * mm, 26 * mm, W - 118 * mm])]))
        elif fn.get("rows") and fn.get("type") == "analog_buffer":
            frows = [[P(f"<b>{h}</b>", s["cell"]) for h in ("Set (mV)", "VIN (mV)", "VOUT (mV)", "Error (mV)")]]
            for r in fn["rows"]:
                frows.append([P(str(r["set_mv"]), s["cell"]), P(str(r["vin_mv"]), s["cell"]), P(str(r["vout_mv"]), s["cell"]),
                              P(str(r["vout_mv"] - r["vin_mv"]), s["cell"])])
            story.append(KeepTogether([P("Voltage-follower test" + (" (simulated)" if fn.get("simulated") else ""), s["cellb"]),
                                       table(frows, [W / 4] * 4)]))
        elif fn.get("results"):
            frows = [[P("<b>Test</b>", s["cell"]), P("<b>Result</b>", s["cell"])]]
            for k, v in fn["results"].items():
                desc = ", ".join(f"{a}: {b}" for a, b in v.items() if a not in ("host_ms", "simulated"))
                frows.append([P(esc(k.replace("_", " ")), s["cell"]), P(esc(desc), s["mono"])])
            story.append(KeepTogether([P("Functional tests" + (" (simulated)" if fn.get("simulated") else ""), s["cellb"]),
                                       table(frows, [40 * mm, W - 40 * mm])]))
        elif fn.get("status") in ("not_configured", "skipped", "timeout", "error"):
            story.append(P(f"Functional test: {esc(fn.get('reason') or fn.get('status'))}", s["small"]))
        ch = _chart_current(meas.get("series"), simulated)
        if ch:
            story += [Spacer(1, 6), P("Current stability", s["cellb"]), Image(ch, width=W, height=W * 2.2 / 7.0)]
        ivc = _chart_iv(ins.get("iv"), simulated)
        if ivc:
            story += [Spacer(1, 4), P(f"Pin I-V signature (pin {ins['iv'].get('pin')}, {ins['iv'].get('r_ohm')} Ω probe)", s["cellb"]),
                      Image(ivc, width=W * 0.6, height=W * 0.6 * 2.4 / 4.2)]

    # 5 trust score
    story.append(P("5  Trust score breakdown", s["h"]))
    rows = [[P(f"<b>{h}</b>", s["cell"]) for h in ("Dimension", "Weight", "Score", "Weighted contribution", "Status")]]
    for d in scores["dimensions"]:
        rows.append([P(esc(d["label"]), s["cell"]), P(f"{d['weight']:.2f}", s["cell"]),
                     P(f"{d['score']:.1f}" if d["score"] is not None else "—", s["cell"]),
                     P(f"{d['contribution']:.2f}" if d["contribution"] is not None else "excluded", s["cell"]),
                     P(esc((d["status"] or "").replace("_", " ")), s["cell"])])
    rows.append([P("<b>Overall</b>", s["cell"]), P(f"{sum(d['weight'] for d in scores['dimensions'] if d['score'] is not None):.2f}", s["cell"]),
                 P(f"<b>{ov:.1f}</b>" if ov is not None else "—", s["cell"]), P("", s["cell"]),
                 P(f"coverage {scores['coverage'] * 100:.0f} %", s["cell"])])
    story.append(table(rows, [52 * mm, 20 * mm, 20 * mm, 40 * mm, W - 132 * mm]))
    story.append(P(f"<font name='ICV-Mono'>{esc(scores['formula'])}</font>", s["body"]))
    story.append(P("Dimensions without a score are excluded from the weighted mean and lower the evidence coverage; "
                   "missing evidence is never counted as a pass.", s["small"]))

    # 6 result
    story.append(P("6  Final screening result", s["h"]))
    story.append(P(f'<font color="{cls_col.hexval()}">{esc(scores["classification"])}</font>', s["result"]))
    for r in scores.get("reasons", []):
        story.append(P("• " + esc(r), s["body"]))
    flags = scores.get("flags", [])
    if flags:
        story.append(Spacer(1, 3))
        rows = [[P("<b>Severity</b>", s["cell"]), P("<b>Dimension</b>", s["cell"]), P("<b>Finding</b>", s["cell"])]]
        for f in flags:
            col = FAIL if f["severity"] == "critical" else WARN
            rows.append([P(f'<font color="{col.hexval()}"><b>{esc(f["severity"].title())}</b></font>', s["cell"]),
                         P(esc(f["dimension"].title()), s["cell"]), P(esc(f["message"]), s["cell"])])
        story.append(table(rows, [22 * mm, 26 * mm, W - 48 * mm]))

    # 7 conditions
    story.append(P("7  Test conditions", s["h"]))
    vv = ((meas.get("static") or {}).get("v") or {})
    story.append(kv_table([
        ("Supply voltage", f"{cfg.get('rail_v')} V rail selected; measured {vv['mean']:.3f} V" if vv else f"{cfg.get('rail_v')} V rail selected"),
        ("Clock", cfg.get("clock") or "Not applicable"),
        ("Ambient temperature", f"{cfg['conditions']['ambient_c']} °C" if (cfg.get("conditions") or {}).get("ambient_c") not in (None, "")
         else "Not recorded"),
        ("Test duration", f"{cfg.get('duration_s')} s supply monitoring" if cfg.get("electrical") else "Not applicable"),
        ("Measurement method", meas.get("method") or "Not applicable"),
        ("Device firmware version", dev.get("firmware") or "—"),
        ("Current-sense resolution", f"{dev.get('resolution_ma')} mA" if dev.get("resolution_ma") else "—"),
        ("Reference dataset version", ins.get("reference_dataset_version")),
        ("Software version", f"IC VISION {ins.get('app_version')}"),
        ("Operator notes on conditions", (cfg.get("conditions") or {}).get("notes") or "—"),
    ], s))

    # 8 notes
    story.append(P("8  Additional notes", s["h"]))
    if ins.get("notes"):
        story.append(P(esc(ins["notes"]), s["body"]))
    story.append(P("<b>Data provenance.</b> oem = value from the manufacturer datasheet; oem_derived = computed from datasheet values "
                   "(for example a limit applied at a VCC between specified points); empirical = known-good sample measured with this "
                   "fixture; simulated = demonstration value generated by the simulator; test_criterion = IC VISION acceptance rule. "
                   "'Not measured' and 'No reference' items are excluded from scoring.", s["body"]))
    story.append(Spacer(1, 3))
    story.append(P("<b>Disclaimer.</b> This is a screening report. Consistency with reference data does not guarantee authenticity, "
                   "and a deviation does not by itself prove counterfeiting. Confirm suspect parts with destructive or laboratory "
                   "analysis (e.g. X-ray, decapsulation, full parametric test) and the authorised distribution chain.", s["body"]))
    if simulated:
        story.append(Spacer(1, 3))
        story.append(P("<b>This report was produced from simulated data for demonstration and must not be used to accept or reject parts.</b>", s["body"]))

    doc.build(story, canvasmaker=NumberedCanvas)
    database.add_report({"id": report_id, "inspection_id": iid, "path": path, "data_source": ins.get("data_source")})
    return path, report_id


def _fmt_ts(iso):
    if not iso:
        return "—"
    try:
        return _local_ts(datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(), seconds=True)
    except ValueError:
        return iso


def _local_ts(dt, seconds=False):
    off = dt.strftime("%z")
    off = f"UTC{off[:3]}:{off[3:]}" if off else "local time"
    return dt.strftime("%d %b %Y, %H:%M" + (":%S" if seconds else "")) + f" ({off})"
