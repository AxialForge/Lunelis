// Build USER_MANUAL.docx from docs/_tools/build/manual_data.json
// (made by prepare_manual.py).
//
//   node docs/_tools/build_manual.js docs/release-package/0.12.0
//
// The table of contents and page numbers are Word fields; to_pdf.ps1 opens
// the file in Word, updates every field and saves the PDF.
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, Table, TableRow, TableCell,
  WidthType, ShadingType, ImageRun, Header, Footer, PageNumber, TableOfContents, LevelFormat,
  BorderStyle, PageBreak, TabStopType, PageOrientation,
} = require("docx");

const OUT = process.argv[2];
const data = JSON.parse(fs.readFileSync(path.join(__dirname, "build", "manual_data.json"), "utf8"));
const V = data.version;
const G = data.general;
const ACCENT = "C2412D";
const GREY = "6B6460";
const FONT = "Segoe UI";
const PAGE_W = 12960;                      // US Letter landscape, 1 inch side margins (DXA)
const IMG_MAX_W = 864, IMG_MAX_H = 600;    // pixels at 96 dpi = 9 x 6.25 inches

// --- helpers ---------------------------------------------------------------------------------
const p = (text, opts = {}) => new Paragraph({
  spacing: { after: 120 }, ...opts,
  children: Array.isArray(text) ? text : [new TextRun({ text: String(text), ...(opts.run || {}) })],
});
const h1 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, pageBreakBefore: true, children: [new TextRun(text)] });
const h2 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(text)] });
const h3 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(text)] });
let listInstance = 0;
const steps = (items) => { listInstance += 1; const inst = listInstance;
  return items.map((t) => new Paragraph({ numbering: { reference: "steps", level: 0, instance: inst },
    spacing: { after: 60 }, children: [new TextRun(t)] })); };
const bullets = (items) => items.map((t) => new Paragraph({ numbering: { reference: "bullets", level: 0 },
  spacing: { after: 60 }, children: Array.isArray(t) ? t : [new TextRun(t)] }));

const border = { style: BorderStyle.SINGLE, size: 4, color: "D9D3CF" };
const borders = { top: border, bottom: border, left: border, right: border };
function cell(text, width, { head = false, bold = false, color, size = 16 } = {}) {
  return new TableCell({
    width: { size: width, type: WidthType.DXA }, borders,
    margins: { top: 50, bottom: 50, left: 80, right: 80 },
    shading: head ? { fill: "EFEAE6", type: ShadingType.CLEAR, color: "auto" } : undefined,
    children: String(text).split("\n").map((line) => new Paragraph({ children: [new TextRun({
      text: line, bold: head || bold, size, color, font: FONT })] })),
  });
}
function table(headers, rows, widths, opts = {}) {
  const total = widths.reduce((a, b) => a + b, 0);
  return new Table({
    width: { size: total, type: WidthType.DXA }, columnWidths: widths,
    rows: [
      new TableRow({ tableHeader: true, cantSplit: true,
        children: headers.map((h, i) => cell(h, widths[i], { head: true, size: opts.size })) }),
      ...rows.map((r) => new TableRow({ cantSplit: true, children: r.map((v, i) => cell(v ?? "-", widths[i], {
        size: opts.size, bold: opts.boldFirst && i === 0, color: opts.boldFirst && i === 0 ? ACCENT : undefined })) })),
    ],
  });
}
let figure = 0;
function image(file, w, h, caption, maxW = IMG_MAX_W, maxH = IMG_MAX_H) {
  let scale = Math.min(1, maxW / w, maxH / h);
  const out = [new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120, after: 60 }, keepNext: true,
    children: [new ImageRun({ type: "png", data: fs.readFileSync(file),
      transformation: { width: Math.round(w * scale), height: Math.round(h * scale) },
      altText: { title: caption || "Screenshot", description: caption || "Screenshot", name: path.basename(file) } })] })];
  if (caption) {
    figure += 1;
    out.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
      children: [new TextRun({ text: `Figure ${figure}: ${caption}`, italics: true, size: 18, color: GREY })] }));
  }
  return out;
}
function pngSize(file) {
  const b = fs.readFileSync(file);
  return [b.readUInt32BE(16), b.readUInt32BE(20)];
}

// --- front matter ----------------------------------------------------------------------------
const cover = [];
cover.push(new Paragraph({ spacing: { before: 200, after: 120 }, children: [new TextRun({ text: "Lunelis", size: 72, bold: true, color: ACCENT, font: FONT })] }));
cover.push(new Paragraph({ spacing: { after: 120 }, children: [new TextRun({ text: "User Manual", size: 40, font: FONT })] }));
cover.push(new Paragraph({ spacing: { after: 200 }, children: [new TextRun({ text: `Version ${V}  ·  for Windows 10 and 11`, size: 24, color: GREY, font: FONT })] }));
const [cw, ch] = pngSize(data.cover_image);
cover.push(...image(data.cover_image, cw, ch, undefined, 640, 400));
cover.push(new Paragraph({ spacing: { before: 200 }, children: [new TextRun({ text: "A local photo library for Windows: browse, rate, tag, edit and protect your photos, on your own PC.", size: 22, color: GREY, font: FONT })] }));
cover.push(new Paragraph({ children: [new TextRun({ text: `Generated ${new Date().toISOString().slice(0, 10)} from the program itself. All photos, names and places shown are invented demo data.`, size: 18, color: GREY, font: FONT })] }));

const body = [];
body.push(new Paragraph({ spacing: { after: 200 }, children: [new TextRun({ text: "Contents", size: 34, bold: true, color: ACCENT, font: FONT })] }));
body.push(new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }));

body.push(h1("About this manual"));
G.about.forEach((t) => body.push(p(t)));
body.push(h2("How Lunelis treats your photos"));
body.push(table(["Principle", "What it means"], G.principles, [3323, 9637], { boldFirst: true, size: 18 }));
body.push(h2("How to read the screenshots"));
body.push(p("Every screenshot has numbered red callouts in its side margins, each joined by a thin line to the control it names. Callouts on the left side are numbered first, top to bottom, then those on the right. The table below each screenshot has one row per callout:"));
body.push(table(["Column", "Meaning"], [
  ["#", "The callout number on the screenshot."], ["Control name", "The control's label, or what it is when it has none."],
  ["Where", "The part of the screen the control is in (see the Parts of this screen picture, and Appendix E)."],
  ["Type", "Button, check box, drop-down list, slider, table, menu command and so on."],
  ["What it does", "What happens when you use it."], ["Inputs/limits", "What it accepts: ranges, choices, keyboard shortcuts."],
  ["Default", "Its setting in a fresh installation, or for a photo that has not been edited."],
  ["Notes", "Tips, side effects and safety notes."]], [2769, 10191], { size: 18 }));

body.push(h1("Installing Lunelis"));
body.push(h2("What you need"));
body.push(table(["Item", "Requirement"], G.requirements, [2769, 10191], { boldFirst: true, size: 18 }));
body.push(h2("Install"));
body.push(...steps(G.install_steps));
body.push(h2("Checking, updating and removing"));
body.push(...bullets(G.install_notes));
body.push(h2("The first run"));
G.first_run.forEach((t) => body.push(p(t)));
body.push(h2("Where Lunelis keeps its data"));
body.push(p(G.data_folder.text));
body.push(table(["In the data folder", "What it holds"], G.data_folder.rows, [3600, 9360], { boldFirst: true, size: 18 }));

body.push(h1("The interface at a glance"));
body.push(p(data.parts_intro));
body.push(p("The library window has these parts. The photo view, the Edit panel and every page have their own; each chapter starts its screens with a Parts of this screen picture, and Appendix E lists every part name."));
const [ow, oh] = pngSize(data.overview_image);
body.push(...image(data.overview_image, ow, oh, "The parts of the library window"));
body.push(table(["", "Part", "What it is"], data.overview_parts.map((q) => [q.letter, q.name, q.text]), [600, 2200, 10160], { boldFirst: true, size: 18 }));

// --- one chapter per part of the program ---------------------------------------------------------
const CALLOUT_W = [460, 1650, 1300, 1050, 3300, 1850, 1350, 1960];
for (const chap of data.chapters) {
  body.push(h1(chap.title));
  body.push(h3("What this part is for"));
  body.push(p(chap.purpose));
  (chap.intro || []).forEach((t) => body.push(p(t)));
  for (const s of chap.screens) {
    body.push(h2(s.name));
    if (s.text) body.push(p(s.text));
    if (s.parts_image) {
      body.push(h3("Parts of this screen"));
      body.push(...image(s.parts_image.path, s.parts_image.width, s.parts_image.height, `The parts of the ${s.name.toLowerCase()}`));
      body.push(table(["", "Part", "What it is"], s.parts.map((q) => [q.letter, q.name, q.text]), [600, 2200, 10160], { boldFirst: true, size: 17 }));
      body.push(h3("Every control"));
    }
    s.images.forEach((im, i) => body.push(...image(im.path, im.width, im.height,
      s.images.length > 1 ? `${s.name} (part ${i + 1} of ${s.images.length})` : s.name)));
    body.push(table(["#", "Control name", "Where", "Type", "What it does", "Inputs/limits", "Default", "Notes"],
      s.rows.map((r) => [String(r.n), r.name, r.where, r.type, r.does, r.inputs, r.default, r.notes]), CALLOUT_W, { boldFirst: true, size: 15 }));
    body.push(p(""));
  }
  if ((chap.workflows || []).length) {
    body.push(h2(`${chap.title}: step by step`));
    for (const wf of chap.workflows) { body.push(h3(wf.title)); body.push(...steps(wf.steps)); }
  }
  if ((chap.edge_cases || []).length) {
    body.push(h2(`${chap.title}: edge cases and errors`));
    body.push(table(["Situation", "What happens, and what to do"], chap.edge_cases.map((e) => [e.situation, e.what_happens]),
      [4431, 8529], { size: 17 }));
  }
}

// --- appendices -----------------------------------------------------------------------------------
if (data.settings_reference.length) {
  body.push(h1("Appendix A: Settings reference"));
  body.push(p("Every setting, where it is, its default and what it changes. Settings are saved the moment you change them."));
  body.push(table(["Tab", "Setting", "Default", "Values", "Effect"],
    data.settings_reference.map((r) => [r.tab, r.setting, r.default, r.values, r.effect]),
    [1662, 2631, 1938, 2714, 4015], { size: 15 }));
}
body.push(h1("Appendix B: Keyboard shortcuts"));
body.push(h2("Menu shortcuts"));
body.push(table(["Keys", "Command"], data.shortcuts.map(([k, c]) => [k, c.replace(/★+/, (m) => `${m.length} star${m.length > 1 ? "s" : ""}`)]),
  [3323, 9637], { boldFirst: true, size: 18 }));
body.push(h2("Keys in the library, the photo view and the Edit panel"));
body.push(table(["Where", "Keys", "What they do"], G.extra_shortcuts, [2215, 3877, 6868], { size: 18 }));

body.push(h1("Appendix C: Troubleshooting"));
body.push(table(["Problem", "What to do"], G.troubleshooting, [4431, 8529], { size: 18 }));

if (data.dark.length) {
  body.push(h1("Appendix D: The dark theme"));
  body.push(p("Lunelis has four themes (Settings > Appearance): Graphite (light, used for the screenshots above), Midnight (dark), High contrast, and Follow Windows. These are the main pages in Midnight; the callout numbers match the tables in the chapters above."));
  for (const d of data.dark) d.images.forEach((im) => body.push(...image(im.path, im.width, im.height, `${d.name}, Midnight theme`)));
}

body.push(h1("Appendix E: Names of the parts of the window"));
body.push(p(data.parts_intro));
body.push(table(["Part", "What it is", "Seen on"], data.parts_glossary.map((r) => [r.name, r.text, r.screens]),
  [2300, 7160, 3500], { boldFirst: true, size: 17 }));

body.push(h1("Appendix F: Glossary"));
body.push(table(["Term", "Meaning"], G.glossary, [3046, 9914], { boldFirst: true, size: 18 }));

// --- document ---------------------------------------------------------------------------------
const header = new Header({ children: [new Paragraph({
  border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "D9D3CF", space: 4 } },
  tabStops: [{ type: TabStopType.RIGHT, position: PAGE_W }],
  children: [new TextRun({ text: "Lunelis User Manual", size: 16, color: GREY, font: FONT }),
    new TextRun({ text: `\tVersion ${V}`, size: 16, color: GREY, font: FONT })] })] });
const footer = new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [
  new TextRun({ children: ["Page ", PageNumber.CURRENT, " of ", PageNumber.TOTAL_PAGES_IN_SECTION], size: 16, color: GREY, font: FONT })] })] });

const doc = new Document({
  creator: "Lunelis documentation tools", title: `Lunelis ${V} User Manual`,
  description: "User manual generated from the program", features: { updateFields: true },
  styles: {
    default: { document: { run: { font: FONT, size: 20 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 34, bold: true, color: ACCENT, font: FONT }, paragraph: { spacing: { before: 240, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, color: "2B2724", font: FONT }, paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 1, keepNext: true } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 21, bold: true, color: "2B2724", font: FONT }, paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 2, keepNext: true } },
    ],
  },
  numbering: { config: [
    { reference: "steps", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.START,
      style: { paragraph: { indent: { left: 400, hanging: 300 } } } }] },
    { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.START,
      style: { paragraph: { indent: { left: 400, hanging: 300 } } } }] },
  ] },
  sections: [
    { properties: { page: { size: { width: 12240, height: 15840, orientation: PageOrientation.LANDSCAPE }, margin: { top: 1080, bottom: 1080, left: 1440, right: 1440 } } },
      children: cover },
    { properties: { page: { size: { width: 12240, height: 15840, orientation: PageOrientation.LANDSCAPE }, margin: { top: 1080, bottom: 1000, left: 1440, right: 1440 },
        pageNumbers: { start: 1 } } },
      headers: { default: header }, footers: { default: footer }, children: body },
  ],
});
Packer.toBuffer(doc).then((buf) => {
  const out = path.join(OUT, "USER_MANUAL.docx");
  fs.writeFileSync(out, buf);
  console.log(`${out}: ${(buf.length / 1048576).toFixed(1)} MB, ${figure} figures`);
});
