### Ultra S

Row size and migration fixes for ultratechhome

### Patches

- `ultra_s.patches.free_sales_invoice_row_size` (`[pre_model_sync]`): ERPNext v16 adds 14
  columns to `tabSales Invoice`, which pushes this site over MariaDB's 65,535 byte limit for
  the sum of a table's column definitions and aborts the upgrade with
  `(1118) Row size too large`. The patch widens the most expendable custom `Data` columns to
  `text` (and mirrors the change on `Custom Field`) so the columns fit before doctype sync runs.

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
