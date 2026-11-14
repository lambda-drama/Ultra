"""Widen legacy custom columns so that the ERPNext v16 Sales Invoice columns fit.

MariaDB caps the sum of all column definitions of a single table at 65,535 bytes.
With utf8mb4 a `varchar(140)` - what every `Data` field maps to - costs 562 bytes of
that budget, while a `text` column only costs ~12. Sites with many custom `Data`
fields on `tabSales Invoice` therefore run out of room and ERPNext v16's
`ALTER TABLE ... ADD COLUMN` batch aborts the upgrade with:

	(1118, 'Row size too large. The maximum row size for the used table type,
	not counting BLOBs, is 65535')

This patch widens just enough legacy custom `Data` columns (not indexed, not unique,
not mandatory and not part of the standard doctype) to `text`, and mirrors the change
in `Custom Field` so that the doctype sync which follows keeps them as `text`
instead of putting `varchar(140)` back. `varchar` -> `text` never loses data.

It has to run in `[pre_model_sync]`: Frappe's schema diff issues `ADD COLUMN`
before `MODIFY COLUMN`, so the freed space must be available before the v16
columns are added.

Safe to re-run: it only acts when the table is over budget, and both the DDL and
the `Custom Field` update are idempotent.
"""

import frappe

# The table that overflows during the v16 upgrade.
DOCTYPE = "Sales Invoice"
TABLE = "tabSales Invoice"

# MariaDB's hard limit for the sum of column definitions in one table.
ROW_LIMIT_BYTES = 65_535

# ERPNext v16 adds ~4.5 kB of columns to tabSales Invoice (14 of them) and later
# versions will add more, so aim well below the hard limit instead of nibbling at it.
TARGET_USED_BYTES = 57_000

# Legacy duplicates that are widened first when the budget is really tight. Any other
# safe custom Data field found on the site is used afterwards, newest first.
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

# Sum of the column definitions, in bytes, as MariaDB counts them for the 65,535 byte
# row limit: BLOB/TEXT columns only take a pointer.
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

# Custom Data fields that can become text without touching an index: no index covers
# the column, the field is not unique and it is not mandatory.
CANDIDATES_QUERY = """
	SELECT cf.name AS custom_field, cf.fieldname, cf.idx,
		COALESCE(c.character_octet_length, 0) + 2 - 12 AS saving
	FROM `tabCustom Field` cf
	INNER JOIN information_schema.COLUMNS c
		ON c.table_schema = DATABASE()
		AND c.table_name = %(table)s
		AND c.column_name = cf.fieldname
		AND c.data_type = 'varchar'
	LEFT JOIN information_schema.STATISTICS s
		ON s.table_schema = c.table_schema
		AND s.table_name = c.table_name
		AND s.column_name = c.column_name
	WHERE cf.dt = %(doctype)s
		AND cf.fieldtype = 'Data'
		AND s.column_name IS NULL
		AND COALESCE(cf.`unique`, 0) = 0
		AND COALESCE(cf.`search_index`, 0) = 0
		AND COALESCE(cf.reqd, 0) = 0
"""


def execute():
	if not frappe.db.table_exists(DOCTYPE):
		return

	used_bytes = get_used_bytes()
	if used_bytes <= TARGET_USED_BYTES:
		print(
			f"{TABLE}: {used_bytes} of {ROW_LIMIT_BYTES} bytes used by column definitions, nothing to widen"
		)
		return

	print(f"{TABLE}: {used_bytes} of {ROW_LIMIT_BYTES} bytes used by column definitions")

	to_widen = []
	projected_bytes = used_bytes
	for candidate in get_candidates():
		if projected_bytes <= TARGET_USED_BYTES:
			break
		to_widen.append(candidate)
		projected_bytes -= max(candidate.saving, 0)

	if not to_widen:
		message = (
			f"{TABLE} uses {used_bytes} of {ROW_LIMIT_BYTES} bytes and no safe custom Data "
			"column is left to widen. Delete unused custom fields on Sales Invoice."
		)
		print(message)
		frappe.log_error(title="Sales Invoice row size", message=message)
		return

	widen_columns(to_widen)
	mark_as_small_text(to_widen)

	print(
		f"{TABLE}: widened {len(to_widen)} custom columns to text "
		f"({', '.join(c.fieldname for c in to_widen)})"
	)
	print(f"{TABLE}: projected {projected_bytes} of {ROW_LIMIT_BYTES} bytes used by column definitions")

	frappe.clear_cache(doctype=DOCTYPE)


def get_used_bytes() -> int:
	"""Bytes the current column definitions of TABLE occupy of MariaDB's row limit."""
	return int(frappe.db.sql(USED_BYTES_QUERY, (TABLE,))[0][0])


def get_candidates() -> list[frappe._dict]:
	"""Custom Data fields of DOCTYPE that can safely be widened to text, most expendable first."""
	candidates = frappe.db.sql(CANDIDATES_QUERY, {"doctype": DOCTYPE, "table": TABLE}, as_dict=True)

	# Never touch a field ERPNext defines itself: its definition would be restored on
	# the next sync and the two would keep fighting.
	standard_fields = {df.fieldname for df in frappe.get_doc("DocType", DOCTYPE).fields}
	candidates = [c for c in candidates if c.fieldname not in standard_fields]

	priority = {fieldname: position for position, fieldname in enumerate(PRIORITY_FIELDS)}
	candidates.sort(key=lambda c: (priority.get(c.fieldname, len(PRIORITY_FIELDS)), -(c.idx or 0)))
	return candidates


def widen_columns(candidates):
	clauses = ", ".join(f"MODIFY `{c.fieldname}` text" for c in candidates)
	frappe.db.sql_ddl(f"ALTER TABLE `{TABLE}` {clauses}")


def mark_as_small_text(candidates):
	"""Keep the metadata in line with the new column type, `Small Text` maps to `text`."""
	for candidate in candidates:
		frappe.db.set_value(
			"Custom Field", candidate.custom_field, "fieldtype", "Small Text", update_modified=False
		)
