# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""A student's account, read the way a university reads it.

Rows run in semester order — Admission, Sem 1, Sem 2 ... — not raw posting-date
order, and every semester block closes with a balance that carries into the next.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

ADMISSION_LABEL = _("Admission")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.student:
		frappe.throw(_("Select a Student."))

	entries = get_ledger_entries(filters)
	data, closing = build_rows(entries)
	return get_columns(), data, None, None, get_report_summary(closing, entries)


def get_columns():
	return [
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 95},
		{"label": _("Semester"), "fieldname": "semester_label", "fieldtype": "Data", "width": 110},
		{"label": _("Particulars"), "fieldname": "particulars", "fieldtype": "Data", "width": 280},
		{
			"label": _("Voucher Type"), "fieldname": "voucher_type",
			"fieldtype": "Data", "width": 120,
		},
		{
			"label": _("Voucher No"), "fieldname": "voucher_no",
			"fieldtype": "Dynamic Link", "options": "voucher_type", "width": 170,
		},
		{"label": _("Charges"), "fieldname": "debit", "fieldtype": "Currency", "width": 120},
		{"label": _("Paid"), "fieldname": "credit", "fieldtype": "Currency", "width": 120},
		{"label": _("Balance"), "fieldname": "balance", "fieldtype": "Currency", "width": 130},
	]


def get_ledger_entries(filters):
	"""Receivable movements for one student, enriched with their semester."""
	conditions = {"party_type": "Student", "party": filters.student, "is_cancelled": 0}
	if filters.get("company"):
		conditions["company"] = filters.company

	gl_entries = frappe.get_all(
		"GL Entry",
		filters=conditions,
		fields=[
			"posting_date", "voucher_type", "voucher_no", "against_voucher",
			"against_voucher_type", "debit", "credit", "remarks", "creation",
		],
		order_by="posting_date asc, creation asc",
	)

	# A fee's semester comes from the Fees document; a payment inherits the
	# semester of whatever it was applied against.
	fee_names = {e.voucher_no for e in gl_entries if e.voucher_type == "Fees"}
	fee_names |= {
		e.against_voucher for e in gl_entries
		if e.against_voucher_type == "Fees" and e.against_voucher
	}
	fee_names.discard(None)

	fee_info = {}
	if fee_names:
		for fee in frappe.get_all(
			"Fees",
			filters={"name": ["in", list(fee_names)]},
			fields=["name", "semester", "academic_term", "academic_year", "program"],
		):
			fee_info[fee.name] = fee

	term_order = get_term_order({f.academic_term for f in fee_info.values() if f.academic_term})

	for entry in gl_entries:
		fee = None
		if entry.voucher_type == "Fees":
			fee = fee_info.get(entry.voucher_no)
		elif entry.against_voucher_type == "Fees":
			fee = fee_info.get(entry.against_voucher)

		if fee and cint(fee.semester):
			entry.semester = cint(fee.semester)
			entry.semester_label = _("Semester {0}").format(fee.semester)
			entry.academic_term = fee.academic_term
			entry.sort_key = (
				term_order.get(fee.academic_term, getdate(entry.posting_date)),
				cint(fee.semester),
			)
		else:
			# Admission-time charges and unapplied payments sit before Semester 1.
			entry.semester = 0
			entry.semester_label = ADMISSION_LABEL
			entry.academic_term = fee.academic_term if fee else None
			entry.sort_key = (getdate("1900-01-01"), 0)

	gl_entries.sort(key=lambda e: (e.sort_key, getdate(e.posting_date), e.creation))
	return gl_entries


def get_term_order(terms):
	"""Map each academic term to its start date so semesters read chronologically."""
	if not terms:
		return {}
	rows = frappe.get_all(
		"Academic Term",
		filters={"name": ["in", list(terms)]},
		fields=["name", "term_start_date"],
	)
	return {r.name: getdate(r.term_start_date) for r in rows if r.term_start_date}


def build_rows(entries):
	"""Group into semester blocks, each closing with a carried-forward balance."""
	data = []
	balance = 0.0
	current = None

	for entry in entries:
		if entry.semester_label != current:
			if current is not None:
				data.append(closing_row(current, balance))
				data.append({})
			current = entry.semester_label
			data.append({
				"particulars": current,
				"is_group": 1,
				"balance": balance,
				"posting_date": None,
			})
			if balance:
				data.append({
					"particulars": _("Opening Balance"),
					"balance": balance,
					"indent": 1,
				})

		balance += flt(entry.debit) - flt(entry.credit)
		data.append({
			"posting_date": entry.posting_date,
			"semester_label": entry.semester_label,
			"particulars": describe(entry),
			"voucher_type": entry.voucher_type,
			"voucher_no": entry.voucher_no,
			"debit": flt(entry.debit),
			"credit": flt(entry.credit),
			"balance": balance,
			"indent": 1,
		})

	if current is not None:
		data.append(closing_row(current, balance))

	return data, balance


def closing_row(label, balance):
	return {
		"particulars": _("Closing Balance — {0}").format(label),
		"balance": balance,
		"is_group": 1,
	}


def describe(entry):
	if entry.voucher_type == "Fees":
		return _("Fee Charged")
	if entry.voucher_type == "Payment Entry":
		return _("Payment Received")
	if entry.voucher_type == "Journal Entry":
		return _("Journal Entry")
	return entry.voucher_type


def get_report_summary(closing, entries):
	charged = sum(flt(e.debit) for e in entries)
	paid = sum(flt(e.credit) for e in entries)
	return [
		{"value": charged, "label": _("Total Charged"), "datatype": "Currency", "indicator": "Blue"},
		{"value": paid, "label": _("Total Paid"), "datatype": "Currency", "indicator": "Green"},
		{
			"value": closing, "label": _("Balance Due"), "datatype": "Currency",
			"indicator": "Red" if closing > 0 else "Green",
		},
	]
