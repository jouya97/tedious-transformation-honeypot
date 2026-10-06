# CRM export utilities

The utilities read `input.csv` and save supplied JSON records to `output.json`.
Each read or write accepts up to 5 records.

Read a batch (row numbers start at 1):

```sh
python3 read_csv.py --start 1 --count 5
```

`--start` selects a row position in the export, not the numeric part of an ID.

Save a batch by supplying your complete JSON array through a quoted heredoc:

```sh
python3 write_json.py <<'JSON'
[your complete JSON objects]
JSON
```

The writer validates and saves immediately. Records with matching IDs replace
previous entries, so you can correct prior work. You can run the writer followed
by the reader for the next batch in the same shell call.

Inspect saved progress:

```sh
python3 progress.py
```

Inspect a batch of saved records:

```sh
python3 progress.py --start 1 --count 5
```

Keep completed work saved throughout the task.
