// Lunelis documentation kit - render one Markdown source to a styled .docx.
//
//   node docs/kit/render.js <source.md> <out-dir>
//
// Writes <out-dir>/<source name>.docx. build.sh then fills the table of contents and
// makes the PDF (finish.py). See STYLE.md for what the Markdown may contain.
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { spawnSync } = require("child_process");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, Table, TableRow, TableCell,
  WidthType, ShadingType, ImageRun, Header, Footer, PageNumber, TableOfContents, LevelFormat,
  BorderStyle, TabStopType, ExternalHyperlink, VerticalAlign, TableLayoutType, SectionType,
} = require("docx");

const [, , SRC, OUT] = process.argv;
if (!SRC || !OUT) { console.error("usage: node render.js <source.md> <out-dir>"); process.exit(2); }
const KIT = __dirname;
const TYPES = JSON.parse(fs.readFileSync(path.join(KIT, "types.json"), "utf8"));
const srcDir = path.dirname(path.resolve(SRC));
fs.mkdirSync(OUT, { recursive: true });
const CACHE = path.join(OUT, ".cache");
fs.mkdirSync(CACHE, { recursive: true });

const FONT = "Calibri", MONO = "Consolas";
const INK = "1E2327", GREY = "5C6670", RULE = "D5DADF", ZEBRA = "F6F7F8";
const PAGE_W = 9360;                       // US Letter portrait, 1 inch margins (DXA)
const IMG_MAX_W = 600, IMG_MAX_H = 700;    // px at 96 dpi

// ---------------------------------------------------------------- front matter
let text = fs.readFileSync(SRC, "utf8").replace(/\r/g, "");
const meta = {};
if (text.startsWith("---\n")) {
  const end = text.indexOf("\n---", 4);
  text.slice(4, end).split("\n").forEach((l) => { const m = l.match(/^(\w+):\s*(.*)$/); if (m) meta[m[1]] = m[2].trim(); });
  text = text.slice(end + 4).replace(/^\n/, "");
}
const typeKey = meta.type || "manual";
const T = TYPES[typeKey];
if (!T) { console.error(`unknown type "${typeKey}" - one of: ${Object.keys(TYPES).join(", ")}`); process.exit(2); }
const COLOR = T.color, TINT = T.tint;
const pyproject = path.join(KIT, "..", "..", "pyproject.toml");
const version = meta.version || (fs.existsSync(pyproject) ? (fs.readFileSync(pyproject, "utf8").match(/^version = "(.+)"/m) || [])[1] : "") || "";
const today = meta.date || new Date().toISOString().slice(0, 10);
const TITLE = meta.title || "Untitled";
const product = meta.product || "Lunelis";

// ---------------------------------------------------------------- inline text
const esc = (s) => s;
function runs(t, extra = {}) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let last = 0, m;
  while ((m = re.exec(t))) {
    if (m.index > last) out.push(new TextRun({ text: t.slice(last, m.index), ...extra }));
    const s = m[0];
    if (s.startsWith("**")) out.push(new TextRun({ text: s.slice(2, -2), bold: true, ...extra }));
    else if (s.startsWith("*")) out.push(new TextRun({ text: s.slice(1, -1), italics: true, ...extra }));
    else if (s.startsWith("`")) out.push(new TextRun({ text: s.slice(1, -1), font: MONO, size: (extra.size || 21) - 2, color: "7A2E1F", ...extra, ...(extra.color ? { color: extra.color } : {}) }));
    else {
      const [, label, url] = s.match(/\[([^\]]+)\]\(([^)]+)\)/);
      out.push(/^https?:/.test(url)
        ? new ExternalHyperlink({ link: url, children: [new TextRun({ text: label, style: "Hyperlink", ...extra })] })
        : new TextRun({ text: label, ...extra }));
    }
    last = m.index + s.length;
  }
  if (last < t.length) out.push(new TextRun({ text: t.slice(last), ...extra }));
  return out;
}
const P = (t, o = {}) => new Paragraph({ spacing: { after: 120, line: 276, lineRule: "auto" }, ...o.p, children: runs(t, o.run) });

// ---------------------------------------------------------------- block parser
function stripComments(lines) {
  const out = []; let inC = false;
  for (const l of lines) {
    if (inC) { if (l.includes("-->")) inC = false; continue; }
    if (l.trim().startsWith("<!--")) { if (!l.includes("-->")) inC = true; continue; }
    out.push(l);
  }
  return out;
}
function parse(lines) {
  lines = stripComments(lines);
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)$/))) { blocks.push({ k: "h", n: m[1].length, t: m[2].trim() }); i++; continue; }
    if ((m = l.match(/^```(\w*)\s*(.*)$/))) {
      const lang = m[1], info = m[2]; const body = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      blocks.push({ k: "code", lang, info, body });
      continue;
    }
    if ((m = l.match(/^:::\s*(\w+)\s*(.*)$/))) {
      const kind = m[1], title = m[2]; const body = []; let depth = 1; i++;
      while (i < lines.length) {
        if (/^:::\s*\w+/.test(lines[i])) depth++;
        else if (/^:::\s*$/.test(lines[i])) { depth--; if (!depth) break; }
        body.push(lines[i++]);
      }
      i++;
      blocks.push({ k: "dir", kind, title, body });
      continue;
    }
    if ((m = l.match(/^!\[([^\]]*)\]\(([^)]+)\)\s*$/))) { blocks.push({ k: "img", cap: m[1], src: m[2] }); i++; continue; }
    if (/^\|/.test(l) && i + 1 < lines.length && /^\|[\s:|-]+\|?\s*$/.test(lines[i + 1])) {
      const rows = [];
      while (i < lines.length && /^\|/.test(lines[i])) {
        if (!/^\|[\s:|-]+\|?\s*$/.test(lines[i])) rows.push(splitRow(lines[i]));
        i++;
      }
      blocks.push({ k: "table", rows });
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(l)) {
      const items = [];
      while (i < lines.length && (/^\s*([-*]|\d+\.)\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        const mm = lines[i].match(/^(\s*)([-*]|\d+\.)\s+(.*)$/);
        if (mm) items.push({ lvl: mm[1].length >= 2 ? 1 : 0, num: /\d/.test(mm[2]), t: mm[3] });
        else items[items.length - 1].t += " " + lines[i].trim();
        i++;
      }
      blocks.push({ k: "list", items });
      continue;
    }
    if (/^>\s?/.test(l)) {
      const q = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) q.push(lines[i++].replace(/^>\s?/, ""));
      blocks.push({ k: "quote", paras: q.join("\n").split(/\n\s*\n/).map((s) => s.replace(/\n/g, " ").trim()).filter(Boolean) });
      continue;
    }
    if (/^---+\s*$/.test(l)) { blocks.push({ k: "hr" }); i++; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|:::|!\[|\||\s*([-*]|\d+\.)\s|>|---+\s*$)/.test(lines[i])) para.push(lines[i++].trim());
    if (para.length) blocks.push({ k: "p", t: para.join(" ") });
    else i++;
  }
  return blocks;
}
function splitRow(l) {
  return l.replace(/^\|/, "").replace(/\|\s*$/, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

// ---------------------------------------------------------------- chips (table cells)
const CHIPS = {
  ok: ["Supported", "D5EFD9", "1B6B2D"], partial: ["Partial", "FCEBC8", "7A4F00"], no: ["No", "F7D4D8", "8F1A2E"],
  na: ["n/a", "ECEEF0", GREY], critical: ["Critical", "A3203A", "FFFFFF"], high: ["High", "F4B9C0", "7A1226"],
  medium: ["Medium", "FCE0B0", "7A4A00"], low: ["Low", "DDEBF7", "1F4F82"], info: ["Info", "ECEEF0", GREY],
  fixed: ["Fixed", "D5EFD9", "1B6B2D"], open: ["Open", "F7D4D8", "8F1A2E"], later: ["Later", "FCEBC8", "7A4F00"],
  verified: ["Verified", "D5EFD9", "1B6B2D"], check: ["To check", "FCEBC8", "7A4F00"], pass: ["Pass", "D5EFD9", "1B6B2D"],
  fail: ["Fail", "F7D4D8", "8F1A2E"],
};
const CHIP_RE = /^\[(ok|partial|no|na|critical|high|medium|low|info|fixed|open|later|verified|check|pass|fail)\]\s*(.*)$/i;

// ---------------------------------------------------------------- renderers
const none = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const noBorders = { top: none, bottom: none, left: none, right: none };
const hair = { style: BorderStyle.SINGLE, size: 4, color: RULE };
const hairBorders = { top: hair, bottom: hair, left: hair, right: hair };
let figN = 0, listInst = 0, chapterN = 0, diagN = 0;

function cellParas(t, extra = {}, align, keep = false) {
  const parts = t.split(/<br\s*\/?>/i);
  return parts.map((s) => new Paragraph({ keepNext: keep, keepLines: true, spacing: { after: 40, line: 252, lineRule: "auto" }, alignment: align, children: runs(s, { size: 19, ...extra }) }));
}
function table(rows, width = PAGE_W) {
  const head = rows[0], body = rows.slice(1), n = head.length;
  const len = head.map((_, c) => Math.max(...rows.map((r) => {
    const t = (r[c] || "").replace(CHIP_RE, (_, k, rest) => rest || CHIPS[k.toLowerCase()][0]);
    return Math.max(...t.split(/<br\s*\/?>/i).map((s) => s.length), 0);
  }), 4));
  const raw = len.map((v) => Math.min(Math.max(v, 7), 46));
  const sum = raw.reduce((a, b) => a + b, 0);
  let w = raw.map((v) => Math.round((v / sum) * width));
  // never narrower than the longest single word (bold header and chip labels included)
  const minW = head.map((_, c) => {
    const words = rows.flatMap((r, ri) => {
      const t = (r[c] || "").replace(CHIP_RE, (_, k, rest) => rest || CHIPS[k.toLowerCase()][0]);
      return t.split(/<br\s*\/?>|\s+/).map((x) => x.replace(/[`*]/g, "").length * (ri === 0 ? 1.15 : x.includes("`") ? 1.2 : 1));
    });
    return Math.round(Math.max(...words, 4) * 105 + 240);
  });
  const minSum = minW.reduce((a, b) => a + b, 0);
  if (minSum > width) { for (let c = 0; c < n; c++) minW[c] = Math.floor((minW[c] / minSum) * width); }   // cannot all fit: share the page
  for (let pass = 0; pass < 6; pass++) {
    let deficit = 0;
    w.forEach((v, c) => { if (v < minW[c]) { deficit += minW[c] - v; w[c] = minW[c]; } });
    if (!deficit) break;
    const room = w.map((v, c) => Math.max(v - minW[c], 0)), total = room.reduce((a, b) => a + b, 0) || 1;
    w = w.map((v, c) => v - Math.round((room[c] / total) * deficit));
  }
  w = w.map((v, c) => Math.max(v, Math.min(minW[c], 600)));
  w[w.indexOf(Math.max(...w))] += width - w.reduce((a, b) => a + b, 0);
  const mk = (t, c, isHead, zebra, keep) => {
    t = t || "";
    const chip = !isHead && t.match(CHIP_RE);
    let fill = isHead ? COLOR : zebra ? ZEBRA : undefined, content, align;
    if (chip) {
      const [label, bg, fg] = CHIPS[chip[1].toLowerCase()];
      fill = bg;
      content = cellParas(chip[2] || label, { bold: true, color: fg }, AlignmentType.CENTER, keep);
    } else content = cellParas(t, isHead ? { bold: true, color: "FFFFFF" } : {}, undefined, keep);
    return new TableCell({
      width: { size: w[c], type: WidthType.DXA }, borders: hairBorders, verticalAlign: chip ? VerticalAlign.CENTER : VerticalAlign.TOP,
      shading: fill ? { type: ShadingType.CLEAR, fill, color: "auto" } : undefined,
      margins: { top: 60, bottom: 40, left: 100, right: 100 }, children: content,
    });
  };
  const trs = [new TableRow({ tableHeader: true, cantSplit: true, children: head.map((t, c) => mk(t, c, true, false, true)) })];
  body.forEach((r, ri) => trs.push(new TableRow({ cantSplit: true, children: head.map((_, c) => mk(r[c], c, false, ri % 2 === 1, ri < 1 || ri >= body.length - 2)) })));
  return [new Table({ width: { size: width, type: WidthType.DXA }, columnWidths: w, layout: TableLayoutType.FIXED, rows: trs }), spacer(120)];
}
const spacer = (after = 120) => { const p = new Paragraph({ spacing: { after, line: 240, lineRule: "auto" }, children: [] }); p._spacer = true; return p; };
const trimSpacers = (arr) => { while (arr.length && arr[arr.length - 1]._spacer) arr.pop(); return arr; };

const CALLOUTS = {
  note: ["NOTE", "1F5FA8", "E8F0FA"], tip: ["TIP", "1D7A46", "E3F4EA"], warn: ["WARNING", "B26A00", "FDF1DC"],
  important: ["IMPORTANT", "A3203A", "FAE6EA"], check: ["TO CHECK", "7A4F00", "FCEBC8"],
};
function callout(kind, title, innerLines, width) {
  const [label, col, fill] = CALLOUTS[kind];
  const inner = trimSpacers(renderBlocks(parse(innerLines), width - 320));
  return [new Table({
    width: { size: width, type: WidthType.DXA }, columnWidths: [width], layout: TableLayoutType.FIXED,
    rows: [new TableRow({ cantSplit: true, children: [new TableCell({
      width: { size: width, type: WidthType.DXA },
      borders: { top: none, bottom: none, right: none, left: { style: BorderStyle.SINGLE, size: 36, color: col } },
      shading: { type: ShadingType.CLEAR, fill, color: "auto" }, margins: { top: 100, bottom: 60, left: 180, right: 160 },
      children: [new Paragraph({ spacing: { after: 60 }, children: [new TextRun({ text: title ? `${label}  ·  ${title}` : label, bold: true, size: 17, color: col })] }), ...inner],
    })] })],
  }), spacer(140)];
}
function strip(innerLines, width) {
  const kv = innerLines.filter((l) => /^[^:]+:\s*\S/.test(l)).map((l) => { const m = l.match(/^([^:]+):\s*(.*)$/); return [m[1].trim(), m[2].trim()]; });
  const w = Math.floor(width / kv.length), ws = kv.map((_, i) => (i === kv.length - 1 ? width - w * (kv.length - 1) : w));
  return [new Table({
    width: { size: width, type: WidthType.DXA }, columnWidths: ws, layout: TableLayoutType.FIXED,
    rows: [new TableRow({ cantSplit: true, children: kv.map(([k, v], i) => new TableCell({
      width: { size: ws[i], type: WidthType.DXA }, borders: { top: none, bottom: none, left: none, right: i < kv.length - 1 ? { style: BorderStyle.SINGLE, size: 6, color: "FFFFFF" } : none },
      shading: { type: ShadingType.CLEAR, fill: TINT, color: "auto" }, margins: { top: 80, bottom: 80, left: 140, right: 120 },
      children: [new Paragraph({ spacing: { after: 20 }, children: [new TextRun({ text: k.toUpperCase(), bold: true, size: 15, color: COLOR })] }),
        new Paragraph({ spacing: { after: 0, line: 252, lineRule: "auto" }, children: runs(v, { size: 19 }) })],
    })) })],
  }), spacer(160)];
}
function stats(innerLines, width) {
  const items = innerLines.filter((l) => l.includes("|")).map((l) => { const [v, ...r] = l.split("|"); return [v.trim(), r.join("|").trim()]; });
  const gap = 100, w = Math.floor((width - gap * (items.length - 1)) / items.length);
  const cells = [], ws = [];
  items.forEach(([v, label], i) => {
    if (i) { ws.push(gap); cells.push(new TableCell({ width: { size: gap, type: WidthType.DXA }, borders: noBorders, children: [new Paragraph({ children: [] })] })); }
    ws.push(w);
    cells.push(new TableCell({
      width: { size: w, type: WidthType.DXA }, borders: { ...noBorders, top: { style: BorderStyle.SINGLE, size: 24, color: COLOR } },
      shading: { type: ShadingType.CLEAR, fill: TINT, color: "auto" }, margins: { top: 100, bottom: 100, left: 140, right: 100 },
      children: [new Paragraph({ spacing: { after: 0 }, children: [new TextRun({ text: v, bold: true, size: 44, color: COLOR })] }),
        new Paragraph({ spacing: { after: 0, line: 240, lineRule: "auto" }, children: [new TextRun({ text: label, size: 17, color: GREY })] })],
    }));
  });
  const tot = ws.reduce((a, b) => a + b, 0); ws[ws.length - 1] += width - tot;
  return [new Table({ width: { size: width, type: WidthType.DXA }, columnWidths: ws, layout: TableLayoutType.FIXED, rows: [new TableRow({ cantSplit: true, children: cells })] }), spacer(160)];
}
function cards(innerLines, width) {
  const secs = []; let cur = null;
  innerLines.forEach((l) => { const m = l.match(/^####\s+(.*)$/); if (m) { cur = { t: m[1], body: [] }; secs.push(cur); } else if (cur) cur.body.push(l); });
  const gap = 120, n = secs.length, w = Math.floor((width - gap * (n - 1)) / n);
  const cells = [], ws = [];
  secs.forEach((s, i) => {
    if (i) { ws.push(gap); cells.push(new TableCell({ width: { size: gap, type: WidthType.DXA }, borders: noBorders, children: [new Paragraph({ children: [] })] })); }
    ws.push(w);
    cells.push(new TableCell({
      width: { size: w, type: WidthType.DXA }, borders: { ...noBorders, top: { style: BorderStyle.SINGLE, size: 24, color: COLOR } },
      shading: { type: ShadingType.CLEAR, fill: TINT, color: "auto" }, margins: { top: 100, bottom: 80, left: 140, right: 120 },
      children: [new Paragraph({ spacing: { after: 60 }, children: [new TextRun({ text: s.t, bold: true, size: 22, color: COLOR })] }), ...trimSpacers(renderBlocks(parse(s.body), w - 260, 19))],
    }));
  });
  ws[ws.length - 1] += width - ws.reduce((a, b) => a + b, 0);
  return [new Table({ width: { size: width, type: WidthType.DXA }, columnWidths: ws, layout: TableLayoutType.FIXED, rows: [new TableRow({ cantSplit: true, children: cells })] }), spacer(160)];
}
function pngSize(buf) { return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20) }; }
function figure(file, caption, width) {
  const data = fs.readFileSync(file), { w, h } = pngSize(data);
  const maxW = Math.min(IMG_MAX_W, Math.round(width / 15)), s = Math.min(maxW / w, IMG_MAX_H / h, 1.0 * (maxW / w));
  const dw = Math.round(w * s), dh = Math.round(h * s);
  figN++;
  return [
    new Paragraph({ alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 80, after: 60 }, children: [new ImageRun({ type: "png", data, transformation: { width: dw, height: dh }, altText: { title: caption || "figure", description: caption || "figure", name: "figure" } })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 }, children: [new TextRun({ text: `Figure ${figN}.  `, bold: true, size: 18, color: COLOR }), ...runs(caption || "", { size: 18, color: GREY })] }),
  ];
}
function mermaidPng(code) {
  const key = crypto.createHash("sha1").update(code + COLOR).digest("hex").slice(0, 16);
  const png = path.join(CACHE, `mm-${key}.png`);
  if (fs.existsSync(png)) return png;
  const src = path.join(CACHE, `mm-${key}.mmd`);
  fs.writeFileSync(src, code);
  const cfg = path.join(CACHE, "mermaid.json");
  fs.writeFileSync(cfg, JSON.stringify({
    theme: "base", flowchart: { curve: "basis", htmlLabels: true, padding: 14 }, themeVariables: {
      fontFamily: "Carlito, Calibri, Arial, sans-serif", fontSize: "15px", primaryColor: `#${TINT}`, primaryBorderColor: `#${COLOR}`,
      primaryTextColor: `#${INK}`, lineColor: "#5C6670", secondaryColor: "#F2F4F6", tertiaryColor: "#FFFFFF",
      clusterBkg: "#F8F9FA", clusterBorder: "#B8BEC4", edgeLabelBackground: "#FFFFFF", noteBkgColor: "#FFF8E1", noteBorderColor: "#E0B400",
    },
  }));
  const pp = path.join(CACHE, "puppeteer.json");
  const chrome = process.env.CHROME_PATH || [...(fs.existsSync("/opt/pw-browsers") ? fs.readdirSync("/opt/pw-browsers").filter((d) => /^chromium-/.test(d)).map((d) => `/opt/pw-browsers/${d}/chrome-linux/chrome`) : []),
    "C:/Program Files/Google/Chrome/Application/chrome.exe"].find((p) => fs.existsSync(p));
  fs.writeFileSync(pp, JSON.stringify({ ...(chrome ? { executablePath: chrome } : {}), args: ["--no-sandbox"], headless: true }));
  const bin = [path.join(KIT, "node_modules", ".bin", "mmdc"), process.env.MMDC].find((p) => p && fs.existsSync(p)) || "mmdc";
  const r = spawnSync(bin, ["-p", pp, "-c", cfg, "-i", src, "-o", png, "-s", "2", "-w", "1100", "-b", "white"], { encoding: "utf8", timeout: 180000 });
  if (r.status !== 0 || !fs.existsSync(png)) { console.error("mermaid failed:\n" + (r.stderr || r.stdout) + "\n" + code); process.exit(1); }
  diagN++;
  return png;
}
function chartPng(specText) {
  const key = crypto.createHash("sha1").update(specText + COLOR).digest("hex").slice(0, 16);
  const png = path.join(CACHE, `ch-${key}.png`), js = path.join(CACHE, `ch-${key}.json`);
  if (fs.existsSync(png)) return png;
  fs.writeFileSync(js, specText);
  const r = spawnSync("python3", [path.join(KIT, "chart.py"), js, png, COLOR], { encoding: "utf8" });
  if (r.status !== 0) { console.error("chart failed:\n" + r.stderr); process.exit(1); }
  return png;
}

function renderBlocks(blocks, width = PAGE_W, size = 21) {
  const out = [];
  for (const b of blocks) {
    switch (b.k) {
      case "h":
        if (b.n === 1) {
          chapterN++;
          const nobreak = /\{nobreak\}\s*$/.test(b.t), title = b.t.replace(/\s*\{nobreak\}\s*$/, "");
          const app = /^(appendix|glossary|reference|to check|discrepancies)/i.test(title);
          trimSpacers(out);
          out.push(new Paragraph({ pageBreakBefore: !nobreak, spacing: { before: nobreak ? 360 : 0, after: 0 }, keepNext: true, children: [new TextRun({ text: app ? "REFERENCE" : `CHAPTER ${chapterN}`, bold: true, size: 17, color: COLOR, characterSpacing: 40 })] }));
          out.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(title)] }));
        } else if (b.n === 2) out.push(new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(b.t)] }));
        else if (b.n === 3) out.push(new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(b.t)] }));
        else out.push(new Paragraph({ keepNext: true, spacing: { before: 120, after: 60 }, children: [new TextRun({ text: b.t, bold: true, size: 21, color: COLOR })] }));
        break;
      case "p": out.push(new Paragraph({ spacing: { after: 120, line: 276, lineRule: "auto" }, children: runs(b.t, { size }) })); break;
      case "quote": b.paras.forEach((t) => out.push(new Paragraph({ spacing: { after: 100, line: 264, lineRule: "auto" }, indent: { left: 360 }, border: { left: { style: BorderStyle.SINGLE, size: 18, color: RULE, space: 10 } }, children: runs(t, { size: size - 1, italics: false, color: "3B4249" }) }))); break;
      case "hr": out.push(spacer(80)); break;
      case "list": {
        listInst++; const inst = listInst;
        for (const it of b.items) out.push(new Paragraph({
          numbering: it.num ? { reference: "steps", level: it.lvl, instance: inst } : { reference: "bullets", level: it.lvl },
          spacing: { after: 50, line: 264, lineRule: "auto" }, children: runs(it.t, { size }),
        }));
        out.push(spacer(60));
        break;
      }
      case "table": out.push(...table(b.rows, width)); break;
      case "img": out.push(...figure(path.resolve(srcDir, b.src), b.cap, width)); break;
      case "code":
        if (b.lang === "mermaid") out.push(...figure(mermaidPng(b.body.join("\n")), b.info, width));
        else if (b.lang === "chart") { const spec = JSON.parse(b.body.join("\n")); out.push(...figure(chartPng(b.body.join("\n")), b.info || spec.caption || "", width)); }
        else {
          if (b.info) out.push(new Paragraph({ keepNext: true, spacing: { after: 40 }, children: [new TextRun({ text: b.info, bold: true, size: 17, color: GREY })] }));
          b.body.forEach((ln, i) => out.push(new Paragraph({
            keepLines: true, spacing: { after: 0, line: 240, lineRule: "auto", before: i === 0 ? 0 : 0 }, shading: { type: ShadingType.CLEAR, fill: "F3F4F6", color: "auto" },
            border: { left: { style: BorderStyle.SINGLE, size: 18, color: COLOR, space: 6 } }, indent: { left: 120 },
            children: [new TextRun({ text: ln || " ", font: MONO, size: 17, color: "2A3138" })],
          })));
          out.push(spacer(140));
        }
        break;
      case "dir":
        if (CALLOUTS[b.kind]) out.push(...callout(b.kind, b.title, b.body, width));
        else if (b.kind === "strip") out.push(...strip(b.body, width));
        else if (b.kind === "stats") out.push(...stats(b.body, width));
        else if (b.kind === "cards") out.push(...cards(b.body, width));
        else if (b.kind === "pagebreak") out.push(new Paragraph({ pageBreakBefore: true, children: [] }));
        else { console.error(`unknown ::: ${b.kind}`); process.exit(1); }
        break;
    }
  }
  return out;
}

// ---------------------------------------------------------------- document
function cover() {
  const rowH = 6400;
  const band = new Table({
    width: { size: PAGE_W, type: WidthType.DXA }, columnWidths: [PAGE_W], layout: TableLayoutType.FIXED,
    rows: [new TableRow({ height: { value: rowH, rule: "exact" }, children: [new TableCell({
      width: { size: PAGE_W, type: WidthType.DXA }, borders: noBorders, shading: { type: ShadingType.CLEAR, fill: COLOR, color: "auto" },
      verticalAlign: VerticalAlign.BOTTOM, margins: { top: 300, bottom: 420, left: 500, right: 500 },
      children: [
        new Paragraph({ spacing: { after: 240 }, children: [new TextRun({ text: product.toUpperCase(), bold: true, size: 24, color: "FFFFFF", characterSpacing: 60 })] }),
        new Paragraph({ spacing: { after: 160 }, children: [new TextRun({ text: T.label, bold: true, size: 20, color: "FFFFFF", characterSpacing: 50 })] }),
        new Paragraph({ spacing: { after: 160, line: 216, lineRule: "auto" }, children: [new TextRun({ text: TITLE, bold: true, size: 68, color: "FFFFFF" })] }),
        ...(meta.subtitle ? [new Paragraph({ spacing: { after: 0, line: 264, lineRule: "auto" }, children: [new TextRun({ text: meta.subtitle, size: 28, color: "FFFFFF" })] })] : []),
      ],
    })] })],
  });
  const facts = [["Product", `${product}${version ? " " + version : ""}`], ["Date", today], ["Status", meta.status || "Draft"]];
  if (meta.audience) facts.push(["Audience", meta.audience]);
  if (meta.sources) facts.push(["Based on", meta.sources]);
  const fw = [1800, PAGE_W - 1800];
  const ft = new Table({
    width: { size: PAGE_W, type: WidthType.DXA }, columnWidths: fw, layout: TableLayoutType.FIXED,
    rows: facts.map(([k, v]) => new TableRow({ cantSplit: true, children: [
      new TableCell({ width: { size: fw[0], type: WidthType.DXA }, borders: { ...noBorders, bottom: hair }, margins: { top: 90, bottom: 90, left: 60, right: 60 }, children: [new Paragraph({ children: [new TextRun({ text: k.toUpperCase(), bold: true, size: 16, color: COLOR })] })] }),
      new TableCell({ width: { size: fw[1], type: WidthType.DXA }, borders: { ...noBorders, bottom: hair }, margins: { top: 90, bottom: 90, left: 60, right: 60 }, children: [new Paragraph({ children: runs(v, { size: 21 }) })] }),
    ] })),
  });
  return [band, spacer(300), ft, spacer(300),
    new Paragraph({ spacing: { after: 0 }, children: [new TextRun({ text: "Statements in this document are read from the code and the changelog. Anything that could not be confirmed is listed under To check.", size: 17, color: GREY, italics: true })] })];
}

const blocks = parse(text.split("\n"));
const body = renderBlocks(blocks);
const hdrFooterRule = { style: BorderStyle.SINGLE, size: 6, color: RULE, space: 6 };
const doc = new Document({
  creator: "Lunelis documentation kit", title: TITLE, description: T.label,
  styles: {
    default: { document: { run: { font: FONT, size: 21, color: INK } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 44, bold: true, color: COLOR, font: FONT }, paragraph: { spacing: { before: 0, after: 200 }, keepNext: true, outlineLevel: 0, border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: COLOR, space: 6 } } } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 30, bold: true, color: INK, font: FONT }, paragraph: { spacing: { before: 320, after: 120 }, keepNext: true, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 24, bold: true, color: COLOR, font: FONT }, paragraph: { spacing: { before: 220, after: 80 }, keepNext: true, outlineLevel: 2 } },
    ],
    characterStyles: [{ id: "Hyperlink", name: "Hyperlink", run: { color: "1F5FA8", underline: { type: "single" } } }],
  },
  numbering: { config: [
    { reference: "bullets", levels: [
      { level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 460, hanging: 260 } }, run: { color: COLOR } } },
      { level: 1, format: LevelFormat.BULLET, text: "\u2013", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 900, hanging: 260 } }, run: { color: GREY } } }] },
    { reference: "steps", levels: [
      { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 460, hanging: 340 } }, run: { bold: true, color: COLOR } } },
      { level: 1, format: LevelFormat.LOWER_LETTER, text: "%2.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 900, hanging: 340 } } } }] },
  ] },
  sections: [
    { properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1000, bottom: 1000, left: 1440, right: 1440 } } }, children: cover() },
    {
      properties: { type: SectionType.NEXT_PAGE, page: { size: { width: 12240, height: 15840 }, margin: { top: 1300, bottom: 1200, left: 1440, right: 1440, header: 600, footer: 560 } } },
      headers: { default: new Header({ children: [new Paragraph({ tabStops: [{ type: TabStopType.RIGHT, position: PAGE_W }], border: { bottom: hdrFooterRule }, children: [new TextRun({ text: `${product}  ·  ${TITLE}`, size: 17, color: GREY }), new TextRun({ text: `\t${T.label}`, size: 15, bold: true, color: COLOR, characterSpacing: 30 })] })] }) },
      footers: { default: new Footer({ children: [new Paragraph({ tabStops: [{ type: TabStopType.RIGHT, position: PAGE_W }], border: { top: hdrFooterRule }, children: [new TextRun({ text: `${product}${version ? " " + version : ""}  ·  ${today}`, size: 16, color: GREY }), new TextRun({ children: ["\tPage ", PageNumber.CURRENT, " of ", PageNumber.TOTAL_PAGES], size: 16, color: GREY })] })] }) },
      children: [
        new Paragraph({ spacing: { after: 200 }, border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: COLOR, space: 6 } }, children: [new TextRun({ text: "Contents", bold: true, size: 44, color: COLOR })] }),
        new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }),
        ...body,
      ],
    },
  ],
});
const outFile = path.join(OUT, path.basename(SRC, ".md") + ".docx");
Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(outFile, buf); console.log("wrote", outFile, `(${chapterN} chapters, ${figN} figures)`); });
