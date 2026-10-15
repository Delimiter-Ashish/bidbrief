# Answer key template

Copy this folder to `data/answer_keys/<package_id>/` (same name as the folder in `data/packages/`)
and fill it **by hand from the PDF**. Do not copy BidBrief's output into the key; the key is the ground truth.

- `bid_items.csv`: every row of the bid form. Copy quantity and unit exactly as printed (keep commas). `page` is the PDF page number (1 = first page of the file).
- `brief.csv`: for each field, `value` is a short text a correct answer must contain (for example `$1,500` or `210`), `pages` are the PDF pages that state it (use `;` between pages).
- `questions.csv`: 20 per package: 15 answerable (`answerable=1`, `expected_contains` = a short text the answer must contain, `pages`), 5 not answerable from the documents (`answerable=0`).
- `risks.csv`: the risks a careful reader would flag. `category` is one of: Schedule, Liquidated damages, Payment, Bonds and insurance, Site conditions, Traffic, Testing, Responsibility shifts, Labor.

The evaluation skips folders whose name starts with `_`.
