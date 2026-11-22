# Optional report extras

loop-autopsy has one core job: **stop a runaway loop and show the evidence for why it never ended.**

The modules below are small, self-contained helpers around the report. They change how a report is **presented, translated, saved or visualised**. None of them decides when a loop is stopped or what is proven about it, and **none of them is needed to stop a loop or understand why it never ended.** Every feature in the main [README](../README.md) works without them.

| Module | What it adds | How it relates to the core |
| ------ | ------------ | -------------------------- |
| `report_theme` | Light and dark colour themes (`get_theme("dark")`) for the HTML report container. | Only supplies colours. Unknown theme names raise `ValueError` instead of silently falling back. |
| `report_i18n` | Fixed report labels in English, Indonesian and Chinese (`message("hint", "id")`). | Translates labels only (titles, "Last rounds checked", "Hint:", ...). Explanations and hints still come from the detector, so existing reports are not translated by surprise. |
| `report_export` | Saving an already-rendered report to a file (`export_text`, `export_html`). | Takes the text or HTML the report renderer already produced and writes it out; it does not change `report.py`. |
| `sparkline` | A tiny chart of a variable's path against its stop value, as Unicode blocks (`sparkline_line`) or inline SVG (`sparkline_svg`). | Adds a visual next to the replay table: how far the variable still is from the value that would end the loop. |

All four use only the Python standard library and do not import other `loop_autopsy` modules, so they can be used or ignored independently.

They are helpers you call from your own code. The reports loop-autopsy shows by itself do not use them: they are always in English with the default colours, and there is no `%autopsy config` setting for a theme or a language.

## Examples

```python
from loop_autopsy.extras.report_theme import get_theme
from loop_autopsy.extras.report_i18n import message
from loop_autopsy.extras.report_export import export_text
from loop_autopsy.extras.sparkline import sparkline_line, sparkline_svg

theme = get_theme("dark")                  # colours for the HTML report
message("hint", "id")                      # Indonesian label
sparkline_line("i", [0, 1, 2, 3], target=10, language="id")
sparkline_svg([0, 1, 2, 3], target=10, target_color=theme.muted)
export_text("report text", "reports/last.txt")
```

Supported languages: `en`, `id`, `zh`. An unsupported language raises `ValueError` listing the valid choices.

## Which one should I read first?

If you are new to the project, skip this page and start with the main README. Come back here only when you want to restyle, translate or share a report.

## Design rule for extras

An extra may change how a report is **presented**, but it must not change **when a loop is stopped** or **what is proven about it**. Detection stays in the core modules, so the same loop always gets the same verdict, with or without extras.
