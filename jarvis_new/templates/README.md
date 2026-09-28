# Brand templates

One folder per document kind. Jarvis copies these to
`$JARVIS_HOME/templates/<name>/` on first use — Sir edits the copies, so
updates to this repo never clobber his brand.

A template is three files:

- `template.html` — the document. `{{placeholders}}` are HTML-escaped;
  `{{#items}}...{{/items}}` repeats once per line item (each `{{key}}`
  inside resolves against the item first, then the whole document).
  Brand colors arrive as placeholders too (`{{brand_color}}`,
  `{{accent_color}}`), so rebranding is a style.json edit.
- `style.json` — brand defaults: company, address, colors, currency, tax
  rate, payment terms, invoice number format (`{YYYY}` + `{NNNN}`),
  due days. Explicit voice-given fields always win over these.
- `rules.md` — plain-language style rules for the drafting helper. Words
  only, never layout: layout lives in the HTML.

Invoice numbers auto-increment via `counter.json` next to the template
(`INV-2026-0001`, ...). Delete it to restart numbering.
