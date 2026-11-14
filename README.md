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
bench get-app $URL_OF_THIS_REPO --branch version-16
bench install-app ultra_s
```

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
