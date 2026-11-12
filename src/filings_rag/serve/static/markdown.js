// A small Markdown renderer for answers: paragraphs, headings, bullet and numbered lists,
// pipe tables, **bold**, *italic*, `code`, and citation links ([1], [F2], [1, F3]).
// Everything is HTML-escaped first, so nothing from the model can inject markup; only the
// tags made here reach the page. No dependencies: the CSP allows scripts from this site only.
(function () {
  const ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

  const BULLET = /^\s*[-*•]\s+(.*)$/;
  const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
  const HEADING = /^\s*#{1,6}\s+(.*?)\s*#*\s*$/;
  const ROW = /^\s*\|.*\|\s*$/;
  const RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

  // `s` is already escaped.
  function inline(s, known) {
    const codes = [];
    s = s.replace(/`([^`]+)`/g, (m, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
    s = s.replace(/\*\*(?=\S)([^*]*?\S)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*(?=\S)([^*]*?\S)\*(?![*\w])/g, "$1<em>$2</em>");
    s = s.replace(/\[((?:F?\d+)(?:\s*,\s*F?\d+)*)\]/g, (m, keys) =>
      keys.split(",").map((k) => {
        k = k.trim();
        return known.has(k) ? `<a href="#src-${k}">[${k}]</a>` : `[${k}]`;
      }).join(""));
    return s.replace(/\u0000(\d+)\u0000/g, (m, i) => `<code>${codes[Number(i)]}</code>`);
  }

  const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

  function startsBlock(lines, i) {
    const l = lines[i];
    return !l.trim() || BULLET.test(l) || NUMBERED.test(l) || HEADING.test(l) ||
      (ROW.test(l) && i + 1 < lines.length && RULE.test(lines[i + 1]));
  }

  function renderAnswer(text, knownKeys) {
    const known = new Set([...(knownKeys || [])].map(String));
    // NUL marks protected code spans in inline(), so none may come from the text itself.
    const lines = esc(text).replace(/\u0000/g, "").split(/\r?\n/);
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const l = lines[i];
      let m;
      if (!l.trim()) { i++; continue; }
      if ((m = l.match(HEADING))) {
        out.push(`<h3>${inline(m[1], known)}</h3>`);
        i++;
      } else if (BULLET.test(l) || NUMBERED.test(l)) {
        const re = BULLET.test(l) ? BULLET : NUMBERED;
        const tag = re === BULLET ? "ul" : "ol";
        const items = [];
        while (i < lines.length && re.test(lines[i])) {
          let item = lines[i].match(re)[1];
          i++;
          // A wrapped line that doesn't start a new block belongs to this item.
          while (i < lines.length && /^\s+\S/.test(lines[i]) && !startsBlock(lines, i)) item += " " + lines[i++].trim();
          items.push(`<li>${inline(item, known)}</li>`);
        }
        out.push(`<${tag}>${items.join("")}</${tag}>`);
      } else if (ROW.test(l) && i + 1 < lines.length && RULE.test(lines[i + 1])) {
        const head = cells(l).map((c) => `<th>${inline(c, known)}</th>`).join("");
        i += 2;
        const body = [];
        while (i < lines.length && ROW.test(lines[i])) {
          body.push(`<tr>${cells(lines[i]).map((c) => `<td>${inline(c, known)}</td>`).join("")}</tr>`);
          i++;
        }
        out.push(`<div class="md-table"><table><thead><tr>${head}</tr></thead><tbody>${body.join("")}</tbody></table></div>`);
      } else {
        const para = [l];
        i++;
        while (i < lines.length && !startsBlock(lines, i)) para.push(lines[i++]);
        out.push(`<p>${para.map((p) => inline(p.trim(), known)).join("<br>")}</p>`);
      }
    }
    return out.join("\n");
  }

  globalThis.renderAnswer = renderAnswer;
})();
