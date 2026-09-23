"""Seed the billing switches on Education Settings.

A Single doctype does not pick up defaults for fields added later, so without
this an upgraded site reads them all as empty and silently stops raising fees.

A site that already runs on Fee Structures is pinned to the legacy engine: it
keeps behaving exactly as before until someone configures Fee Plans and flips
the switch themselves.
"""

import frappe


def execute():
	settings = frappe.get_single("Education Settings")

	has_fee_structures = bool(
		frappe.db.count("Fee Structure", {"docstatus": 1})
	)

	values = {
		"fee_engine": "Structure Based" if has_fee_structures else "Course Based",
		"billing_document": "Sales Order" if settings.get("create_so") else "Sales Invoice",
		"create_fee_on_course_registration": 1,
		"auto_submit_fees": 0,
		"default_fee_due_days": 30,
	}

	for field, value in values.items():
		if settings.get(field) in (None, ""):
			frappe.db.set_single_value("Education Settings", field, value)

	if has_fee_structures:
		print(
			"Education Settings.fee_engine pinned to 'Structure Based' because this "
			"site has submitted Fee Structures. Switch it to 'Course Based' once "
			"Fee Plans are configured."
		)
