"""Seed the fee challan settings on Education Settings.

A Single doctype does not pick up defaults for fields added later, so without
this the challan prints with no bank details, no payment terms and no late-fee
line on an upgraded site.
"""

import frappe

DEFAULTS = {
	"fee_challan_banks": "ASKARI BANK|0111-0320200642\nUNITED BANK LIMITED|130701018977",
	"fee_challan_note": (
		"Please deposit your Fees in any branch of Askari Bank or "
		"United Bank Limited only"
	),
	"fee_challan_terms": (
		"Fees are non-refundable and non transferable.\n"
		"Fee must be paid on or before the due date."
	),
	"late_fee_percent": 6,
}


def execute():
	settings = frappe.get_single("Education Settings")
	for field, value in DEFAULTS.items():
		if settings.get(field) in (None, ""):
			frappe.db.set_single_value("Education Settings", field, value)
