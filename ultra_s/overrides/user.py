"""User controller override.

`User.active_sessions` is a *computed* (virtual) child table: the property
returns a list of plain dicts instead of `User Session Display` documents.
Frappe converts those dicts into child documents in
`frappe.model.document._update_computed_ct_props()`, which only wraps the
property when the `active_sessions` field is flagged `is_virtual` in the site's
metadata.

When that flag is missing (stale `tabDocField` row after an upgrade, or because
the `User` controller class was built before metadata sync), the raw property is
used. `Document.as_dict()` iterates over every entry of `_table_fieldnames` and
calls `.as_dict()` on each row, so the plain dicts blow up with:

	AttributeError: 'dict' object has no attribute 'as_dict'

`frappe.website.utils.get_home_page()` calls
`frappe.get_user().load_user()`, which does
`frappe.get_doc("User", user).as_dict()` - hence every affected user gets a 500
right after login (Administrator keeps working only while its cached `user_doc`
/ `home_page` values in Redis are still warm).

This override materialises those dict rows into child documents just before
serialisation - exactly what `_update_computed_ct_props()` would have done.
"""

from frappe.core.doctype.user.user import User


class CustomUser(User):
	def as_dict(self, *args, **kwargs) -> dict:
		self.materialize_computed_child_tables()
		return super().as_dict(*args, **kwargs)

	def materialize_computed_child_tables(self) -> None:
		"""Convert plain dicts held by table fields into child documents.

		`set()` pushes the rows through `extend()`/`_init_child()`, which builds the
		real child controller for the field's `options` doctype. Tables that already
		contain documents (i.e. healthy sites) are left untouched, so this is a
		no-op unless the bug is actually present.
		"""
		for fieldname in self._table_fieldnames:
			rows = getattr(self, fieldname, None) or []

			if isinstance(rows, list) and any(isinstance(row, dict) for row in rows):
				self.set(fieldname, rows)
