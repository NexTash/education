"""Recompute outstanding_amount on submitted Fees from the receivable ledger.

Early course-based fees put against_voucher on the income credit lines as well
as the party line, so ERPNext recomputed the outstanding balance against an
income account and stored a negative figure. The general ledger itself was
always correct; only the cached field on Fees was wrong.
"""

import frappe
from frappe.utils import flt


def execute():
	fees = frappe.get_all(
		"Fees",
		filters={"docstatus": 1},
		fields=["name", "grand_total", "receivable_account", "outstanding_amount"],
	)

	for fee in fees:
		if not fee.receivable_account:
			continue

		balance = frappe.db.sql(
			"""
			select sum(debit) - sum(credit)
			from `tabGL Entry`
			where against_voucher_type = 'Fees'
				and against_voucher = %s
				and account = %s
				and is_cancelled = 0
			""",
			(fee.name, fee.receivable_account),
		)[0][0]

		outstanding = flt(balance if balance is not None else fee.grand_total)
		paid = flt(fee.grand_total) - outstanding

		if flt(fee.outstanding_amount) == outstanding:
			continue

		frappe.db.set_value(
			"Fees",
			fee.name,
			{"outstanding_amount": outstanding, "paid_amount": paid},
			update_modified=False,
		)

	frappe.db.commit()

	# statuses follow from the repaired amounts
	from education.education.doctype.fees.fees import update_fee_status

	for fee in fees:
		update_fee_status(fee.name)
