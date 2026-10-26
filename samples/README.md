# samples/ — real-world reference data for the importers

Nothing here is ever read or modified by the app at runtime. This folder is only
for **you** to drop exports into so the new importers can be built against the
real formats instead of guessed ones. Everything can be committed or kept
out of git — your choice (they're not in `.gitignore` yet).

## 1. Oink Earnings Report (the important one)

- Get it from: Oink extension → Earnings Tracker → *Earnings Report / Download Data (CSV)*
- Drop it here named anything like `earnings_sample.csv`
- **Redact**: your name, email, or address if any of those appear as columns or values
- **Keep**: column headers (exactly as exported), dates, ASINs, amounts — the join
  with `output/manifest.csv` lives on those columns

## 2. Oink "My CC Campaign Products Report" (Pro export)

- If it's a CSV/JSON, drop it here as `cc_campaign_products_sample.*`
- Same redaction rule as above

## 3. One Creator Connections campaign, copy-pasted

- Open a campaign you've joined, select-all, paste into `campaign-example.txt` or
  `.md` — messy is fine, I just need to see: campaign name, brand, the ASIN list,
  any required talking points, and the deadline/date fields
- This is the fallback format even if you have the Pro export, because it covers
  campaigns you haven't accepted yet

## 4. (Optional) one real raw clip

- Any 30–60 second clip with speech, any filename
- Skip this if you'd rather not — `test_media.ps1` synthesizes usable demo clips

---

Once these land, I tune the flexible importers to match them exactly. Until then,
everything builds on tolerant column-name mapping with clear error messages.
