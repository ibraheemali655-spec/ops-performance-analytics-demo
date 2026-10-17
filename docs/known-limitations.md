# Known limitations of the recovery logic

The ETL in `etl/enrich.py` mirrors the production pipeline's logic on purpose. These five
behaviours are inherited from it and are not exercised by the generator:

1. **Orphan month by Start Date.** The year-to-date sweep tests Deadline and Start Date month by
   month, so a project whose Start Date falls in an earlier month than its Deadline is booked to
   the Start Date month. That includes a project whose Deadline is still in the future.
2. **Orphan, then late jobs.** A project appended as an orphan whose jobs appear later is counted
   twice: the late-job check compares task IDs, never the parent project ID.
3. **Moved deadlines.** Base rows are not checked against earlier months' files, so a job whose
   Deadline moves to a later month appears in both months.
4. **Year boundary.** Both sweeps cover the target year only, so a late job or orphan from
   December that surfaces in January is never recovered (`tests/test_cross_year.py`).
5. **First run into an empty folder.** With no earlier months' files present, every earlier
   month's rows of the year are appended as "late".
