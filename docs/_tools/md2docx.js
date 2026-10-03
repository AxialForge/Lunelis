// Render a Markdown file to .docx in the manual's style (then to_pdf.ps1 makes the PDF).
//
//   node docs/_tools/md2docx.js <in.md> <out.docx> "<header title>" [portrait|landscape]
//
// Supports what the release documents use: # / ## / ### headings, paragraphs,
// - bullets (one level of nesting), 1. numbered steps, | tables |, ``` code
// blocks, ![caption](image.png) on its own line, **bold**, `code` and
// [text](url). The first "# " heading becomes the cover title; a table of
// contents follows it.
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, Table, TableRow, TableCell,
  WidthType, ShadingType, ImageRun, Header, Footer, PageNumber, TableOfContents, LevelFormat,
  BorderStyle, TabStopType, PageOrientation, ExternalHyperlink,
} = require("docx");

const [, , IN, OUTFILE, TITLE = "Lunelis", ORIENT = "portrait"] = process.argv;
const LANDSCAPE = ORIENT === "landscape";
const PAGE_W = LANDSCAPE ? 12960 : 9360;
const IMG_W = LANDSCAPE ? 864 : 624, IMG_H = LANDSCAPE ? 560 : 760;
const ACCENT = "C2412D", GREY = "6B6460", FONT = "Segoe UI", MONO = "Consolas";
const base = path.dirname(path.resolve(IN));
const lines = fs.readFileSync(IN, "utf8").replace(/\r/g, "").split("\n");
const version = (fs.readFileSync(path.join(__dirname, "..", "..", "pyproject.toml"), "utf8").match(/^version = "(.+)"/m) || [])[1];

function runs(text, extra = {}) {
  // **bold**, `code`, [text](url)
  const out = [];
  const re = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(new TextRun({ text: text.slice(last, m.index), ...extra }));
    const t = m[0];
    if (t.startsWith("**")) out.push(new TextRun({ text: t.slice(2, -2), bold: true, ...extra }));
    else if (t.startsWith("*")) out.push(new TextRun({ text: t.slice(1, -1), italics: true, ...extra }));
    else if (t.startsWith("`")) out.push(new TextRun({ text: t.slice(1, -1), font: MONO, size: 18, color: "7A2E1F", ...extra }));
    else {
      const [, label, url] = t.match(/\[([^\]]+)\]\(([^)]+)\)/);
      out.push(/^https?:/.test(url)
        ? new ExternalHyperlink({ link: url, children: [new TextRun({ text: label, style: "Hyperlink", ...extra })] })
        : new TextRun({ text: label, ...extra }));
    }
    last = m.index + t.length;
  }
  if (last < text.length) out.push(new TextRun({ text: text.slice(last), ...extra }));
  return out;
}
const border = { style: BorderStyle.SINGLE, size: 4, color: "D9D3CF" };
const borders = { top: border, bottom: border, left: border, right: border };
function table(rows) {
  const head = rows[0], body = rows.slice(1);
  const n = head.length;
  // Column widths from the longest text in each column, within limits.
  const len = head.map((_, i) => Math.max(...rows.map((r) => (r[i] || "").length), 3));
  const raw = len.map((l) => Math.min(Math.max(l, 10), 60));
  const sum = raw.reduce((a, b) => a + b, 0);
  const widths = raw.map((r) => Math.round((r / sum) * PAGE_W));
  widths[n - 1] += PAGE_W - widths.reduce((a, b) => a + b, 0);
  const cell = (t, i, isHead) => new TableCell({
    width: { size: widths[i], type: WidthType.DXA }, borders, margins: { top: 50, bottom: 50, left: 80, right: 80 },
    shading: isHead ? { fill: "EFEAE6", type: ShadingType.CLEAR, color: "auto" } : undefined,
    children: [new Paragraph({ children: runs(t || "", { size: 17, bold: isHead || undefined }) })],
  });
  return new Table({ width: { size: PAGE_W, type: WidthType.DXA }, columnWidths: widths, rows: [
    new TableRow({ tableHeader: true, cantSplit: true, children: head.map((t, i) => cell(t, i, true)) }),
    ...body.map((r) => new TableRow({ cantSplit: true, children: head.map((_, i) => cell(r[i], i, false)) })),
  ] });
}
function pngSize(file) { const b = fs.readFileSync(file); return [b.readUInt32BE(16), b.readUInt32BE(20)]; }

const cover = [], body = [];
let target = cover, listInst = 0, figure = 0, i = 0, title = null;
const splitRow = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
while (i < lines.length) {
  const l = lines[i];
  if (/^# /.test(l) && title === null) {
    title = l.slice(2).trim();
    cover.push(new Paragraph({ spacing: { before: LANDSCAPE ? 600 : 2400, after: 160 }, children: [new TextRun({ text: title, size: 60, bold: true, color: ACCENT, font: FONT })] }));
    i++;
    while (i < lines.length && !/^## /.test(lines[i])) {      // cover blurb up to the first section
      if (lines[i].trim()) cover.push(new Paragraph({ spacing: { after: 120 }, children: runs(lines[i].trim(), { size: 24, color: GREY }) }));
      i++;
    }
    cover.push(new Paragraph({ spacing: { before: 400 }, children: [new TextRun({ text: `Lunelis ${version}  ·  generated ${new Date().toISOString().slice(0, 10)}`, size: 20, color: GREY, font: FONT })] }));
    target = body;
    body.push(new Paragraph({ spacing: { after: 200 }, children: [new TextRun({ text: "Contents", size: 34, bold: true, color: ACCENT, font: FONT })] }));
    body.push(new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }));
    continue;
  }
  if (/^#{1,3} /.test(l)) {
    const level = l.match(/^#+/)[0].length;
    const text = l.replace(/^#+ /, "");
    const heading = [HeadingLevel.HEADING_1, HeadingLevel.HEADING_1, HeadingLevel.HEADING_2, HeadingLevel.HEADING_3][level];
    target.push(new Paragraph({ heading, pageBreakBefore: level === 2 && target.length > 2, children: [new TextRun(text)] }));
    i++; continue;
  }
  if (/^```/.test(l)) {
    const code = []; i++;
    while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
    i++;
    target.push(new Paragraph({ spacing: { before: 80, after: 160 }, shading: { fill: "F4F1EE", type: ShadingType.CLEAR, color: "auto" },
      border: { left: { style: BorderStyle.SINGLE, size: 12, color: "D9D3CF", space: 6 } },
      children: code.flatMap((c, k) => [new TextRun({ text: c || " ", font: MONO, size: 17, break: k ? 1 : 0 })]) }));
    continue;
  }
  if (/^!\[/.test(l)) {
    const [, cap, src] = l.match(/^!\[([^\]]*)\]\(([^)]+)\)/);
    const file = path.resolve(base, src);
    const [w, h] = pngSize(file);
    const s = Math.min(1, IMG_W / w, IMG_H / h);
    target.push(new Paragraph({ alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 120, after: 60 }, children: [new ImageRun({
      type: "png", data: fs.readFileSync(file), transformation: { width: Math.round(w * s), height: Math.round(h * s) },
      altText: { title: cap, description: cap, name: path.basename(file) } })] }));
    figure++;
    target.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 }, children: [new TextRun({ text: `Figure ${figure}: ${cap}`, italics: true, size: 18, color: GREY })] }));
    i++; continue;
  }
  if (/^\|/.test(l)) {
    const rows = [];
    while (i < lines.length && /^\|/.test(lines[i])) {
      if (!/^\|[\s:|-]+\|$/.test(lines[i].trim())) rows.push(splitRow(lines[i]));
      i++;
    }
    target.push(table(rows));
    target.push(new Paragraph({ spacing: { after: 80 }, children: [] }));
    continue;
  }
  if (/^\s*([-*]|\d+\.) /.test(l)) {
    listInst++;
    while (i < lines.length && /^\s*([-*]|\d+\.) /.test(lines[i])) {
      const indent = lines[i].match(/^\s*/)[0].length >= 2 ? 1 : 0;
      const numbered = /^\s*\d+\. /.test(lines[i]);
      let text = lines[i].replace(/^\s*([-*]|\d+\.) /, "");
      i++;
      while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*]|\d+\.) /.test(lines[i])) text += " " + lines[i++].trim();
      target.push(new Paragraph({ numbering: numbered ? { reference: "steps", level: indent, instance: listInst } : { reference: "bullets", level: indent },
        spacing: { after: 60 }, children: runs(text) }));
    }
    continue;
  }
  if (!l.trim()) { i++; continue; }
  const para = [l.trim()]; i++;
  while (i < lines.length && lines[i].trim() && !/^(#|```|!\[|\||\s*([-*]|\d+\.) )/.test(lines[i])) para.push(lines[i++].trim());
  target.push(new Paragraph({ spacing: { after: 120 }, children: runs(para.join(" ")) }));
}

const size = { width: 12240, height: 15840, ...(LANDSCAPE ? { orientation: PageOrientation.LANDSCAPE } : {}) };
const margin = { top: 1080, bottom: 1000, left: 1440, right: 1440 };
const lvl = (format, text) => [0, 1].map((level) => ({ level, format, text: format === LevelFormat.BULLET ? (level ? "–" : "•") : `%${level + 1}.`,
  alignment: AlignmentType.START, style: { paragraph: { indent: { left: 400 + level * 400, hanging: 300 } } } }));
const doc = new Document({
  creator: "Lunelis documentation tools", title: title || TITLE, features: { updateFields: true },
  styles: {
    default: { document: { run: { font: FONT, size: 20 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, color: ACCENT, font: FONT }, paragraph: { spacing: { before: 240, after: 160 }, outlineLevel: 0, keepNext: true } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 25, bold: true, color: "2B2724", font: FONT }, paragraph: { spacing: { before: 240, after: 100 }, outlineLevel: 1, keepNext: true } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 21, bold: true, color: "2B2724", font: FONT }, paragraph: { spacing: { before: 180, after: 80 }, outlineLevel: 2, keepNext: true } },
    ],
  },
  numbering: { config: [
    { reference: "steps", levels: lvl(LevelFormat.DECIMAL) },
    { reference: "bullets", levels: lvl(LevelFormat.BULLET) },
  ] },
  sections: [
    { properties: { page: { size, margin } }, children: cover },
    { properties: { page: { size, margin, pageNumbers: { start: 1 } } },
      headers: { default: new Header({ children: [new Paragraph({
        border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "D9D3CF", space: 4 } },
        tabStops: [{ type: TabStopType.RIGHT, position: PAGE_W }],
        children: [new TextRun({ text: TITLE, size: 16, color: GREY }), new TextRun({ text: `\tVersion ${version}`, size: 16, color: GREY })] })] }) },
      footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [
        new TextRun({ children: ["Page ", PageNumber.CURRENT, " of ", PageNumber.TOTAL_PAGES_IN_SECTION], size: 16, color: GREY })] })] }) },
      children: body },
  ],
});
Packer.toBuffer(doc).then((b) => { fs.writeFileSync(OUTFILE, b); console.log(`${OUTFILE}: ${(b.length / 1024).toFixed(0)} KB, ${figure} figures`); });
