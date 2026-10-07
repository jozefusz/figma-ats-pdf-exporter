# Designing a Figma frame that exports cleanly

The exporter redraws your frame from its layers, so what comes out depends on
how the frame is built. Everything below was tested on real frames. You can
inspect each case in the
[sample Figma file](https://www.figma.com/design/Wez5wYYyPKEuBJRrAXP9zR/figma-ats-pdf-exporter)
(frames `T1` to `T9` sit beside the sample CV).

**Auto layout or not doesn't matter.** The tool reads each layer's final
position and size, so absolutely positioned frames (T1 to T9) and auto-layout
frames (the sample CV) both export correctly.

## How the tool tells you something is wrong

| You see | Meaning |
|---|---|
| `warning: ...` | Export succeeded, but something was dropped, approximated or risky. Read it. |
| `error: ...` (exit code 1) | The frame uses something the tool refuses to guess at. Nothing is written. |
| `verification found issues` (exit code 2) | The PDF was written, but at least one extractor read it back wrongly. The output shows which words and which extractor. |

Run `pdftoppm -png -r 110 output/cv.pdf output/preview` and look at the page
too. Verification checks text, not looks.

## Do and don't

### Letter-spacing (tracking)

| | |
|---|---|
| **Do** keep tracking below **40%** of the font size. For headings with several words, stay below about **17%** in a monospace font (Fragment Mono) or **35%** in a proportional one (Bricolage Grotesque); the warning gives the exact value per layer | Tracked text stays one run with real spaces |
| **Don't** use 40% or more | poppler's `pdftotext` returns single letters (`W I D E`) |
| **Don't** track multi-word text widely | The gap between words reaches about 1 em and `pdftotext` puts each word on its own line |
| **Avoid** exactly 10.0% | At some font sizes it splits letters in `pdftotext`. 9% and 11% look the same and don't |

`pypdf` and most ATS parsers read all of these correctly; only layout-based
tools such as `pdftotext` split them. The tool warns before export (with the
largest safe value for each layer) and checks the real output afterwards.

If you can't change the design, run with `--tighten-word-gaps`. It narrows the
gap between words in tracked text and roughly doubles the tracking a
multi-word heading can carry. It does not help letter-level splits.

### Text styling

| | |
|---|---|
| **Do** use one style per text layer | |
| **Don't** bold, colour or size part of a layer's text | Refused with an `error`. Silently styling the whole layer was the alternative, so the tool stops instead. **Fix:** split into separate layers, one style each |
| **Do** make a link the whole text layer | Becomes a real PDF link |
| **Don't** link part of a layer | Warns, and the whole line is linked. **Fix:** put the link in its own layer |
| **Do** use solid text colours | |
| **Don't** use gradient or image fills on text | Warns, and the text is drawn solid black. **Fix:** solid colour |
| **Don't** rotate text | Warns, and the text is drawn upright at the rotated box's position. **Fix:** keep text upright |
| **Don't** add strokes to text | Warns and ignores the stroke |
| **Don't** convert text to outlines | The text becomes a shape and is not extractable. Warns and names the layer. **Fix:** keep live text |

Uppercase set with Figma's text-case option is fine; the tool applies it.

### Shapes and backgrounds

| | |
|---|---|
| **Do** use filled rectangles, ellipses and frames with solid fills | Drawn, including rounded corners (one radius for all four corners) and frame backgrounds, so white text on a dark card stays readable |
| **Do** make dividers 1px-tall filled rectangles | Drawn |
| **Don't** make dividers with the line tool, or boxes with only a stroke | Not drawn. Warns. **Fix:** a thin filled rectangle |
| **Don't** use different radii per corner | Warns and draws square |
| **Don't** use gradient or image fills on shapes | Not drawn. Warns |
| **Don't** rely on drop shadows or blurs | Warns. They are not drawn |

### Fonts and characters

| | |
|---|---|
| **Do** use Bricolage Grotesque or Fragment Mono, or add your font | Any other family falls back to Helvetica with a warning: still real text, wrong typeface. See "Fonts" in the README |
| **Don't** put emoji in the text | They are removed on purpose, since the bundled fonts have no glyph for them. The surrounding spaces are tidied |

### Reading order

| | |
|---|---|
| **Do** stack layers in the order you want them read | Tools that read the PDF in file order (such as `pypdf`) follow layer order, and the lowest layer in Figma's layers panel is read first |
| **Do** build multi-column layouts one column at a time | Layout-based tools (`pdftotext`) ignore layer order and read by position, so layer order is what you control for file-order tools |
| **Don't** worry about a title and a date sharing a baseline | The tool separates consecutive runs, so the last word of one layer never fuses into the first word of the next (tested with the date layer below the title layer) |

## Before you export

1. Keep text live (no outlines), one style per layer, solid colours, upright.
2. Keep tracking under 40% (under about 17% for headings with several words), and off exactly 10%.
3. Use filled shapes only; no line tool, no outline-only boxes.
4. Run the exporter, read every `warning`, and open the preview.
5. Exit code 0 means both extractors agreed with your Figma text.
