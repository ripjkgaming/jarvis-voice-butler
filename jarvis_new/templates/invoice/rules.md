# Invoice style rules (for the drafting helper)

These plain-language rules shape the text fields Jarvis drafts. The layout
and brand colors come from template.html + style.json — never describe
formatting here, only words.

- Address the client by name exactly as Sir says it; never invent a company
  suffix (Ltd, Inc) Sir didn't give.
- Line-item descriptions are short noun phrases: "Logo design", not whole
  sentences. One deliverable per line.
- Quantities are plain numbers ("3"), never words ("three").
- Prices are bare numbers with no currency sign; the template adds currency.
- The notes field stays empty unless Sir mentions terms, discounts, or a
  thank-you — then one short sentence, warm but professional.
- Never invent items, rates, or discounts. If Sir didn't say it, leave it out
  and let the voice tool ask.
