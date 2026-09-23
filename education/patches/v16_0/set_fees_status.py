"""Backfill status and paid_amount on existing Fees records."""

import frappe
from frappe.utils import flt, getdate, nowdate


def execute():
	fees = frappe.get_all(
		"Fees",
		fields=["name", "docstatus", "grand_total", "outstanding_amount", "due_date"],
	)

	today = getdate(nowdate())

	for fee in fees:
		paid = flt(fee.grand_total) - flt(fee.outstanding_amount)

		if fee.docstatus == 2:
			status = "Cancelled"
		elif fee.docstatus == 0:
			status = "Draft"
		elif flt(fee.outstanding_amount) <= 0:
			status = "Paid"
		elif paid > 0:
			status = "Partly Paid"
		elif fee.due_date and getdate(fee.due_date) < today:
			status = "Overdue"
		else:
			status = "Unpaid"

		frappe.db.set_value(
			"Fees",
			fee.name,
			{"status": status, "paid_amount": paid},
			update_modified=False,
		)
