# Offline fixtures

`source_file/indeed_client.py` reads this directory when
`LIVE_COLLECTION_ENABLED = False` (the default).

* A file named `<keyword>-<city>.json`, slugified and lower-cased, is used
  for exactly that search — e.g. `network-engineer-pune.json`.
* A file named `default.json` is used for every search that has no file of
  its own.
* Anything with no matching file falls back to deterministic **generated**
  fixture jobs, so the whole pipeline runs with no files here at all.

Shape — either a bare list of job objects, or:

```json
{ "total": 2, "jobs": [ { ...job... }, { ...job... } ] }
```

A job object may use any of the field names the extractor understands
(`title`/`job_title`, `company_name`, `description`, `salary_text`,
`formatted_location`, `apply_url`, `indeed_apply_url`, `source_url`, …).
`example-fixture-shape.json` below is a complete, commented-by-example
record; its slug matches no real keyword/city pair, so it is never used by a
crawl — it is there to copy from when a sample from an authorised source
becomes available.

**Fixture rows are not Indeed data.** Generated fixtures carry
`"fixture": true` and `*.fixture.invalid` hosts so they can never be mistaken
for live postings in the Excel output.
