### Ultra S

Row size and migration fixes for ultratechhome

### Patches

- `ultra_s.patches.free_sales_invoice_row_size` (`[pre_model_sync]` **and** `before_migrate`):
  ERPNext v16 adds 14 columns to `tabSales Invoice`, which pushes this site over MariaDB's
  65,535 byte limit for the sum of a table's column definitions and aborts the upgrade with
  `(1118) Row size too large`. The patch widens the most expendable custom `Data` columns to
  `text` (and mirrors the change on `Custom Field`) so the columns fit before doctype sync runs.

It is registered twice on purpose. `Patch Log` silently disables a patch that an earlier, failed
attempt already recorded, so `before_migrate` in `hooks.py` - which `Migrate.pre_schema_updates()`
runs at the very start of every `bench migrate`, before any patch pass - calls the same idempotent
`execute()` as the `[pre_model_sync]` entry in `patches.txt`. Every run prints, so the migrate log
says exactly what happened:

| log line | meaning |
| --- | --- |
| `ultra_s: tabSales Invoice uses 65011 of 65535 bytes, widening custom columns` then `widened 15 custom columns to text` | the fix was applied |
| `ultra_s: ... uses 56761 of 65535 bytes, nothing to widen` | already applied, nothing to do |
| no `ultra_s:` line at all | the app's code never ran: it is not installed on the site, or not in the bench of that release |

On Frappe Cloud that last line is the one that matters: the app has to be part of the bench of the
release the site is being updated to, and installed on the site (`bench --site <site> install-app
ultra_s`), otherwise `migrate` never loads this app and the row size fix cannot happen at all -
regardless of `patches.txt`.

Columns are discovered from `information_schema` alone - `varchar`, nullable, no index covering
them, not a field the DocType itself declares - and only `Custom Field` rows typed `Data` or
`Small Text` (or a leftover `custom_*` column with no `Custom Field` row) qualify, because sync
maps those to `text` too. `Link` and `Select` columns are left alone: they live in `varchar`
columns as well, but sync would put them back after the `ADD COLUMN` pass has already failed.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/lambda-drama/Ultra --branch ft/im
bench install-app ultra_s
```

> The app lives on branch `ft/im` of `lambda-drama/Ultra` (the `version-16` branch of that
> repository holds the separate `ultra_f` app). The repository folder is therefore cloned as
> `apps/Ultra` while the Python package — and the app name registered in `sites/apps.txt` — is
> `ultra_s` (taken from `[project].name` in `pyproject.toml`), which bench supports.

`pyproject.toml` declares the Frappe/ERPNext versions this app supports — Frappe **v15 and v16**
(the patch is plain `information_schema` + `ALTER TABLE` work, so it runs unchanged on both; on a
v15 site it simply frees the same headroom before the upgrade):

```toml
requires-python = ">=3.10"          # v15 benches run 3.10/3.11, v16 benches run 3.14

[tool.bench.frappe-dependencies]
frappe = ">=15.0.0,<17.0.0"
erpnext = ">=15.0.0,<17.0.0"
```

This section is required: bench refuses to resolve the app without it, and Frappe Cloud aborts
with *"Could not find a compatible Frappe version in pyproject.toml"* or *"Version 15 is not
supported by branch …"*. The declared range must include **every** Frappe major the app is built
on, so keep `frappe`/`erpnext` in sync when moving to another version.

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/ultra_s
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
