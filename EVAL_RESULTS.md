# BidBrief evaluation — test

_Run 2026-10-14 20:15. Numbers are measured, not promised._

## sample-mapleford (synthetic)

| What | Result | Target |
|---|---|---|
| Bid items: rows matched exactly | 100.0% (26/26) | 95%+ on clean forms |
| Bid items: missed / invented | 0 missed, 0 invented, 26 extracted vs 26 in form | invented = 0 |
| Brief: fields correct with a correct page | 100.0% (9/9) | 90%+ |
| Ask: answers correct and cited to the right page | 40.0% (6/15) | 85%+ |
| Ask: unanswerable marked 'not found' | 80.0% (4/5) | 90%+ |
| Risks: real risks found | 88.9% (8/9) · 1 flags not in key | report |
| Speed: time to process | 0 s for 24 pages (ingest 0 s) |  |

## Citations

- Model quotes that passed the check before filtering: 100.0% (39/39)
- Quotes shown as verified that really appear on the cited page: 100% (enforced by the citation check; anything else is dropped or shown grey as unverified)

## Error analysis (one line per failure)

- [sample-mapleford (synthetic)] Ask q01 'What are the liquidated damages?' — model misread it (got 'SC-2 Liquidated Damages.')
- [sample-mapleford (synthetic)] Ask q02 'What testing is required for the new water main?' — cited a different page (got 'SYNTHETIC SAMPLE - NOT A REAL BID - Mapleford Contract W-2026-04 Page 1 TOWN OF ')
- [sample-mapleford (synthetic)] Ask q03 'Are there night work restrictions?' — model misread it (got 'SYNTHETIC SAMPLE - NOT A REAL BID - Mapleford Contract W-2026-04 Page 13 SUPPLEM')
- [sample-mapleford (synthetic)] Ask q04 'How much retainage is held?' — cited a different page (got 'A pre-bid conference will be held at 10:00 AM on Tuesday, November 3, 2026 at th')
- [sample-mapleford (synthetic)] Ask q05 'What is the contract time?' — model misread it (got 'SYNTHETIC SAMPLE - NOT A REAL BID - Mapleford Contract W-2026-04 Page 12 SUPPLEM')
- [sample-mapleford (synthetic)] Ask q06 'What are the working hours?' — model misread it (got 'SC-4 Working Hours.')
- [sample-mapleford (synthetic)] Ask q10 'What compaction is required for trench backfill?' — cited a different page (got 'SYNTHETIC SAMPLE - NOT A REAL BID - Mapleford Contract W-2026-04 Page 7 BID FORM')
- [sample-mapleford (synthetic)] Ask q14 'How much notice is needed for a detour?' — retrieval missed the page (got "The documents don't answer this.")
- [sample-mapleford (synthetic)] Ask q15 'Is the pre-bid conference mandatory?' — cited a different page (got 'Question: Is the pre-bid conference mandatory?')
- [sample-mapleford (synthetic)] Ask q18 'What brand of fire hydrant must be used?' — answered a question the documents don't answer (got verified: 'Quantity Unit Item Description Unit Price Total 1 1 LS Mobilization and Demobili')
- [sample-mapleford (synthetic)] Risk Labor (prevailing wage, apprentices) — real risk not flagged
- [sample-mapleford (synthetic)] Risk flag Labor p.2 'Check this: 12 Prevailing Wage Rates .......................' — flag not in the answer key

### Biggest causes

- 4 × model misread it
- 4 × cited a different page
- 1 × retrieval missed the page
- 1 × answered a question the documents don't answer
- 1 × real risk not flagged
- 1 × flag not in the answer key
