"""Free up row size on `tabSales Invoice` so that the v16 upgrade can add its columns.

MariaDB caps the column definitions of one table at 65_535 bytes. `tabSales Invoice` ships with
enough `varchar(140)` custom columns to sit at roughly 65_000 bytes, so ERPNext v16's fourteen
new columns overflow the cap and `ALTER TABLE ... ADD COLUMN` fails with error 1118 ("Row size
too large"), which aborts the whole migrate:

	Failed to alter schema using query: ALTER TABLE `tabSales Invoice` ADD COLUMN `apply_tds` ...

The cap counts the *declared* column types, not the data in them, so neither `innodb_strict_mode`
nor `ROW_FORMAT=DYNAMIC` gets around it. Converting the widest custom `Data` columns to `text`
takes their `varchar(140)` declaration out of the sum (562 bytes become 12, so 550 bytes each
here) without touching a single value, since `varchar` to `text` is a lossless widening.

This has to run before `sync_all()` adds the new columns, so it is registered twice:

* `before_migrate` in hooks.py, which `Migrate.pre_schema_updates()` executes before anything
  else, and
* `ultra_s.patches.free_sales_invoice_row_size` in the `[pre_model_sync]` section of
  patches.txt, which runs just before `frappe.model.sync.sync_all()`.

Both call `execute()`, which is idempotent: it does nothing once the table is inside the target,
so the second call in the same migrate is a cheap no-op. The hook is what makes this reliable -
a `Patch Log` row left behind by an earlier failed attempt silently disables the patch, but hooks
are not gated on `Patch Log` at all.

Every run prints a line, so the migrate log always shows whether this ran and what it found.
"""

import frappe

DOCTYPE = "Sales Invoice"
TABLE = "tabSales Invoice"

# MariaDB's hard cap on the summed column definitions of a table.
ROW_LIMIT_BYTES = 65_535

# Stop widening once the table is this small. Leaves ~8.5 kB for the fourteen columns v16 adds
# (about 4.5 kB) plus whatever a future release grows by.
TARGET_USED_BYTES = 57_000

# The only `Custom Field` types whose column may be turned into `text`. Frappe maps both to
# `text`, while `Link` and `Select` also live in `varchar` columns but sync would put them back
# to `varchar(140)`, and `ADD COLUMN` runs before `MODIFY` in the same pass.
SAFE_FIELDTYPES = frozenset({"Data", "Small Text"})

# Columns Frappe manages on every table. Never widened, even on tables without a DocType behind
# them.
SYSTEM_COLUMNS = frozenset(
	{
		"name",
		"creation",
		"modified",
		"modified_by",
		"owner",
		"docstatus",
		"idx",
		"parent",
		"parentfield",
		"parenttype",
	}
)

# Widened first when the budget is really tight. Any other safe custom Data field found on the
# site is used afterwards, newest first.
PRIORITY_FIELDS = (
	"custom_temp_scu_id",
	"custom_temp_scu_invoice_no_",
	"custom_temp_scu_mrc_no",
	"custom_temp_current_receipt_no",
	"custom_temp_receipt_signature",
	"revision_count",
	"custom_internal_data",
	"custom_total_receipt_number",
	"custom_submission_attempts",
	"etims_submission_attempts",
)

# Sum of the column definitions, in bytes, as MariaDB counts them for the 65,535 byte row limit:
# BLOB/TEXT columns only take a pointer.
USED_BYTES_QUERY = """
	SELECT COALESCE(SUM(
		CASE
			WHEN data_type IN ('varchar', 'char') THEN COALESCE(character_octet_length, 0) + 2
			WHEN data_type IN (
				'tinytext', 'text', 'mediumtext', 'longtext',
				'tinyblob', 'blob', 'mediumblob', 'longblob', 'json'
			) THEN 12
			WHEN data_type = 'tinyint' THEN 1
			WHEN data_type = 'smallint' THEN 2
			WHEN data_type = 'mediumint' THEN 3
			WHEN data_type = 'int' THEN 4
			WHEN data_type = 'bigint' THEN 8
			WHEN data_type = 'float' THEN 4
			WHEN data_type IN ('double', 'real') THEN 8
			WHEN data_type IN ('decimal', 'numeric') THEN 17
			WHEN data_type = 'date' THEN 3
			WHEN data_type = 'time' THEN 6
			WHEN data_type = 'datetime' THEN 8
			WHEN data_type = 'timestamp' THEN 7
			WHEN data_type = 'year' THEN 1
			ELSE 12
		END
	), 0)
	FROM information_schema.COLUMNS
	WHERE table_schema = DATABASE() AND table_name = %s
"""

# `varchar` columns that can become `text` without touching an index: no index covers the column
# and the column is nullable. `varchar(140)` costs 562 bytes of that sum and `text` costs 12, so
# the saving is the octet length plus the two length bytes minus the pointer.
CANDIDATES_QUERY = """
	SELECT c.column_name AS fieldname,
		COALESCE(c.character_octet_length, 0) + 2 - 12 AS saving,
		c.ordinal_position
	FROM information_schema.COLUMNS c
	WHERE c.table_schema = DATABASE()
		AND c.table_name = %(table)s
		AND c.data_type = 'varchar'
		AND c.is_nullable = 'YES'
		AND COALESCE(c.is_generated, 'NEVER') = 'NEVER'
		AND NOT EXISTS (
			SELECT 1
			FROM information_schema.STATISTICS s
			WHERE s.table_schema = c.table_schema
				AND s.table_name = c.table_name
				AND s.column_name = c.column_name
		)
"""


def execute():
	"""Widen custom columns on `tabSales Invoice` until the table is back inside the cap."""
	print(f"ultra_s: {TABLE} row size check")

	if not frappe.db.table_exists(DOCTYPE):
		print(f"ultra_s: {DOCTYPE} is not installed on this site, nothing to do")
		return

	used_bytes = get_used_bytes()
	if used_bytes <= TARGET_USED_BYTES:
		print(f"ultra_s: {TABLE} uses {used_bytes} of {ROW_LIMIT_BYTES} bytes, nothing to widen")
		return

	print(f"ultra_s: {TABLE} uses {used_bytes} of {ROW_LIMIT_BYTES} bytes, widening custom columns")

	custom_fields = get_custom_fields()
	to_widen = pick_columns(used_bytes)
	if not to_widen:
		message = (
			f"{TABLE} uses {used_bytes} of {ROW_LIMIT_BYTES} bytes and no custom column is left to "
			"widen, so the v16 upgrade cannot add its new columns."
		)
		print(f"ultra_s: {message}")
		frappe.log_error(title=f"{TABLE} row size", message=message)
		return

	widened = widen_columns(to_widen)
	mark_as_small_text(widened, custom_fields)
	frappe.clear_cache(doctype=DOCTYPE)

	if not widened:
		message = (
			f"{TABLE} still uses {get_used_bytes()} of {ROW_LIMIT_BYTES} bytes: none of the "
			"candidate columns could be widened."
		)
		print(f"ultra_s: {message}")
		frappe.log_error(title=f"{TABLE} row size", message=message)
		return

	print(
		f"ultra_s: widened {len(widened)} custom columns to text, "
		f"{TABLE} now uses {get_used_bytes()} of {ROW_LIMIT_BYTES} bytes"
	)


def get_used_bytes() -> int:
	used_bytes = frappe.db.sql(USED_BYTES_QUERY, (TABLE,))
	return int(used_bytes[0][0]) if used_bytes else 0


def pick_columns(used_bytes: int) -> list[frappe._dict]:
	"""The columns to widen, in order, stopping once the table reaches the target."""
	to_widen = []
	projected_bytes = used_bytes
	for candidate in get_candidates():
		if projected_bytes <= TARGET_USED_BYTES:
			break
		to_widen.append(candidate)
		projected_bytes -= max(candidate.saving, 0)

	return to_widen


def get_candidates() -> list[frappe._dict]:
	"""Every `varchar` column on the table that is safe to turn into `text`, in widening order.

	Read straight from `information_schema` so that a missing or unusual `Custom Field` row cannot
	hide a column that has to be widened. Fields the DocType itself declares are skipped, as are
	indexed and `NOT NULL` columns. What is left is either a `Custom Field` typed `Data` or
	`Small Text` - the only types sync maps to `varchar` but which can also be mapped to `text` -
	or a `custom_*` column that no `Custom Field` row declares any more and that sync therefore
	never touches. `Link` and `Select` columns stay `varchar` because sync would put them back,
	and `ADD COLUMN` runs before `MODIFY` in the same pass.
	"""
	rows = frappe.db.sql(CANDIDATES_QUERY, {"table": TABLE}, as_dict=True)
	protected = get_standard_fields() | SYSTEM_COLUMNS
	custom_fields = get_custom_fields()
	priority = {fieldname: position for position, fieldname in enumerate(PRIORITY_FIELDS)}

	candidates = []
	for row in rows:
		if row.fieldname in protected:
			continue

		custom_field = custom_fields.get(row.fieldname)
		if custom_field is not None:
			if custom_field.fieldtype not in SAFE_FIELDTYPES:
				continue
		elif not row.fieldname.startswith("custom_"):
			# Not a custom column of this doctype: leave it alone.
			continue

		candidates.append(row)

	candidates.sort(key=lambda row: (priority.get(row.fieldname, len(priority)), -row.ordinal_position))

	return candidates


def get_standard_fields() -> set[str]:
	"""Fieldnames the `Sales Invoice` DocType declares; widening one fights `sync_all()`."""
	return set(
		frappe.get_all(
			"DocField",
			filters={"parent": DOCTYPE, "parenttype": "DocType"},
			pluck="fieldname",
		)
	)


def get_custom_fields() -> dict[str, frappe._dict]:
	"""`Custom Field` rows of the doctype, keyed by fieldname."""
	return {
		row.fieldname: row
		for row in frappe.get_all(
			"Custom Field",
			filters={"dt": DOCTYPE},
			fields=["name", "fieldname", "fieldtype"],
		)
	}


def widen_columns(columns: list[frappe._dict]) -> list[frappe._dict]:
	"""Convert each column to `text`, one `ALTER TABLE` at a time.

	Separate statements so that one ineligible column cannot take the rest of the batch down with
	it: whatever MariaDB refuses is logged and skipped, and the columns that did change still
	count. Returns the columns that were actually widened.
	"""
	widened = []
	for column in columns:
		try:
			frappe.db.sql_ddl(f"ALTER TABLE `{TABLE}` MODIFY `{column.fieldname}` text")
		except Exception as error:
			print(f"ultra_s: could not widen {column.fieldname}: {error}")
			frappe.log_error(
				title=f"{TABLE} row size",
				message=f"Could not widen `{column.fieldname}` to text: {error}",
			)
			continue

		widened.append(column)

	return widened


def mark_as_small_text(columns: list[frappe._dict], custom_fields: dict[str, frappe._dict]) -> None:
	"""Keep `sync_all()` from turning the widened columns back into `varchar(140)`.

	`Custom Field.fieldtype` decides the column type on every sync, and `Small Text` is the type
	Frappe maps to `text`. Columns without a `Custom Field` row need nothing: sync does not know
	them, so the `text` stays.
	"""
	for column in columns:
		custom_field = custom_fields.get(column.fieldname)
		if not custom_field:
			continue
		frappe.db.set_value(
			"Custom Field", custom_field.name, {"fieldtype": "Small Text"}, update_modified=False
		)
