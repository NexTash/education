# Copyright (c) 2015, Frappe Technologies and contributors
# For license information, please see license.txt

"""Fees records occurred academic income.

It replaces Sales Invoice for course-based billing: one receivable debit for the
student, and one credit per income account so that each Fee Category and each
course's tuition lands where the chart of accounts expects it.
"""

import erpnext
import frappe
from erpnext.accounts.doctype.payment_request.payment_request import (
	make_payment_request,
)
from erpnext.accounts.general_ledger import make_reverse_gl_entries
from erpnext.controllers.accounts_controller import AccountsController
from frappe import _
from frappe.utils import flt, getdate, money_in_words, nowdate

from education.education.doctype.fee_plan.fee_plan import (
	get_component_cost_center,
	get_component_income_account,
	get_currency_precision,
	validate_income_account,
)


class Fees(AccountsController):
	def set_indicator(self):
		"""Set indicator for portal"""
		if self.outstanding_amount > 0:
			self.indicator_color = "orange"
			self.indicator_title = _("Unpaid")
		else:
			self.indicator_color = "green"
			self.indicator_title = _("Paid")

	def validate(self):
		self.set_missing_accounts_and_fields()
		self.set_line_accounts()
		self.calculate_total()
		self.validate_enrollment()
		self.validate_billable_amount()
		self.validate_income_accounts()
		self.set_status()

	def set_missing_accounts_and_fields(self):
		if not self.company:
			self.company = frappe.defaults.get_defaults().company
		if not self.currency:
			self.currency = erpnext.get_company_currency(self.company)

		if not (self.receivable_account and self.income_account and self.cost_center):
			accounts_details = frappe.db.get_value(
				"Company",
				self.company,
				["default_receivable_account", "default_income_account", "cost_center"],
				as_dict=True,
			)
			if not self.receivable_account:
				self.receivable_account = accounts_details.default_receivable_account
			if not self.income_account:
				self.income_account = accounts_details.default_income_account
			if not self.cost_center:
				self.cost_center = accounts_details.cost_center

		if not self.contact_email:
			self.contact_email = self.get_student_emails()

	def set_line_accounts(self):
		"""Resolve each tuition row to the income account it should credit."""
		for row in self.tuition_items:
			if not row.fee_category and row.course:
				row.fee_category = frappe.db.get_value(
					"Course", row.course, "fee_category"
				)
			if not row.income_account:
				row.income_account = (
					get_component_income_account(row.fee_category, self.company)
					or self.income_account
				)
			if not row.cost_center:
				row.cost_center = (
					get_component_cost_center(row.fee_category, self.company)
					or self.cost_center
				)

	def validate_enrollment(self):
		if not self.program_enrollment:
			return

		enrollment_student = frappe.db.get_value(
			"Program Enrollment", self.program_enrollment, "student"
		)
		if enrollment_student != self.student:
			frappe.throw(
				_("Invalid Enrollment {0} for student {1}").format(
					frappe.bold(self.program_enrollment), frappe.bold(self.student)
				)
			)

	def validate_income_accounts(self):
		"""Every account this fee will credit must be able to take the entry."""
		accounts = {account for account, _cost_center in self.get_income_distribution()}
		accounts.add(self.income_account)
		for account in accounts:
			validate_income_account(account)

	def validate_billable_amount(self):
		if self.docstatus == 1 and not flt(self.grand_total):
			frappe.throw(_("Cannot submit a fee with a zero grand total."))

	def get_student_emails(self):
		student_emails = frappe.db.sql_list(
			"""
			select g.email_address
			from `tabGuardian` g, `tabStudent Guardian` sg
			where g.name = sg.guardian and sg.parent = %s and sg.parenttype = 'Student'
			and ifnull(g.email_address, '')!=''
		""",
			self.student,
		)

		student_email_id = frappe.db.get_value("Student", self.student, "student_email_id")
		if student_email_id:
			student_emails.append(student_email_id)
		if student_emails:
			return ", ".join(list(set(student_emails)))
		else:
			return None

	def calculate_total(self):
		"""Totals across course tuition and flat components, discounts applied."""
		precision = get_currency_precision()
		discount_amount = 0.0

		self.total_tuition = 0.0
		self.total_credit_hours = 0.0
		for row in self.tuition_items:
			line_discount = flt(row.amount) * flt(row.discount) / 100.0
			row.total = flt(flt(row.amount) - line_discount, precision)
			discount_amount += line_discount
			self.total_tuition += row.total
			self.total_credit_hours += flt(row.credit_hours)

		self.total_components = 0.0
		for row in self.components:
			line_discount = flt(row.amount) * flt(row.discount) / 100.0
			row.total = flt(flt(row.amount) - line_discount, precision)
			discount_amount += line_discount
			self.total_components += row.total

		self.total_tuition = flt(self.total_tuition, precision)
		self.total_components = flt(self.total_components, precision)
		self.discount_amount = flt(discount_amount, precision)
		self.grand_total = flt(self.total_tuition + self.total_components, precision)
		self.grand_total_in_words = money_in_words(self.grand_total, self.currency)

		if self.docstatus == 0:
			self.outstanding_amount = self.grand_total
			self.paid_amount = 0.0
		else:
			self.paid_amount = flt(
				self.grand_total - flt(self.outstanding_amount), precision
			)

	def set_status(self, update=False):
		if self.docstatus == 2:
			status = "Cancelled"
		elif self.docstatus == 0:
			status = "Draft"
		elif flt(self.outstanding_amount) <= 0:
			status = "Paid"
		elif flt(self.paid_amount) > 0:
			status = "Partly Paid"
		elif self.due_date and getdate(self.due_date) < getdate(nowdate()):
			status = "Overdue"
		else:
			status = "Unpaid"

		if update:
			self.db_set("status", status)
		else:
			self.status = status
		return status

	def on_submit(self):
		self.make_gl_entries()
		self.set_status(update=True)

		if self.send_payment_request and self.contact_email:
			pr = make_payment_request(
				party_type="Student",
				party=self.student,
				dt="Fees",
				dn=self.name,
				party_name=self.student_name,
				recipient_id=self.contact_email,
				submit_doc=True,
				use_dummy_message=True,
			)
			frappe.msgprint(
				_("Payment request {0} created").format(
					frappe.utils.get_link_to_form("Payment Request", pr.name)
				)
			)

	def on_cancel(self):
		self.ignore_linked_doctypes = ("GL Entry", "Payment Ledger Entry")
		make_reverse_gl_entries(voucher_type=self.doctype, voucher_no=self.name)
		self.db_set("status", "Cancelled")

	def get_income_distribution(self):
		"""Credit amount per (income account, cost center).

		Rows are merged so that ten courses sharing one tuition account produce
		one GL line rather than ten.
		"""
		distribution = {}

		def add(account, cost_center, amount):
			if not flt(amount):
				return
			account = account or self.income_account
			cost_center = cost_center or self.cost_center
			key = (account, cost_center)
			distribution[key] = flt(distribution.get(key, 0)) + flt(amount)

		for row in self.tuition_items:
			add(row.income_account, row.cost_center, row.total)

		for row in self.components:
			add(
				get_component_income_account(row.fees_category, self.company),
				get_component_cost_center(row.fees_category, self.company),
				row.total,
			)

		return distribution

	def make_gl_entries(self):
		if not self.grand_total:
			return

		distribution = self.get_income_distribution()
		if not distribution:
			frappe.throw(_("No income account could be resolved for this fee."))

		# Rounding each line independently can drift from the grand total by a
		# cent; the largest line absorbs the difference so the entry balances.
		precision = get_currency_precision()
		credits = {key: flt(amount, precision) for key, amount in distribution.items()}
		drift = flt(self.grand_total, precision) - flt(sum(credits.values()), precision)
		if drift:
			largest = max(credits, key=lambda key: credits[key])
			credits[largest] = flt(credits[largest] + drift, precision)

		against_accounts = ", ".join(sorted({key[0] for key in credits}))

		gl_entries = [
			self.get_gl_dict(
				{
					"account": self.receivable_account,
					"party_type": "Student",
					"party": self.student,
					"against": against_accounts,
					"debit": self.grand_total,
					"debit_in_account_currency": self.grand_total,
					"against_voucher": self.name,
					"against_voucher_type": self.doctype,
					"cost_center": self.cost_center,
				},
				item=self,
			)
		]

		for (account, cost_center), amount in credits.items():
			gl_entries.append(
				self.get_gl_dict(
					{
						"account": account,
						"against": self.student,
						"credit": amount,
						"credit_in_account_currency": amount,
						"cost_center": cost_center,
						# No against_voucher here: only the party line carries it,
						# otherwise ERPNext recomputes outstanding_amount against an
						# income account and writes a negative balance.
					},
					item=self,
				)
			)

		from erpnext.accounts.general_ledger import make_gl_entries

		make_gl_entries(
			gl_entries,
			cancel=(self.docstatus == 2),
			update_outstanding="Yes",
			merge_entries=False,
		)


def update_fee_status(fee):
	"""Refresh paid/outstanding derived fields after a payment moves."""
	doc = frappe.get_doc("Fees", fee)
	if doc.docstatus != 1:
		return

	precision = get_currency_precision()
	paid = flt(flt(doc.grand_total) - flt(doc.outstanding_amount), precision)
	doc.db_set("paid_amount", paid, update_modified=False)
	doc.paid_amount = paid
	doc.set_status(update=True)


def update_linked_fees(doc, method=None):
	"""Payment Entry / Journal Entry hook: restate the fees they settled."""
	fees = set()

	for reference in doc.get("references") or []:
		if reference.get("reference_doctype") == "Fees":
			fees.add(reference.get("reference_name"))

	for account in doc.get("accounts") or []:
		if account.get("reference_type") == "Fees":
			fees.add(account.get("reference_name"))

	for fee in fees:
		if fee:
			update_fee_status(fee)


def set_overdue_status():
	"""Daily: flip unpaid fees past their due date to Overdue."""
	overdue = frappe.get_all(
		"Fees",
		filters={
			"docstatus": 1,
			"status": ["in", ["Unpaid", "Partly Paid"]],
			"due_date": ["<", nowdate()],
			"outstanding_amount": [">", 0],
		},
		pluck="name",
	)
	for fee in overdue:
		frappe.db.set_value("Fees", fee, "status", "Overdue", update_modified=False)


def get_fee_list(
	doctype, txt, filters, limit_start, limit_page_length=20, order_by="modified"
):
	user = frappe.session.user
	student = frappe.db.sql(
		"select name from `tabStudent` where student_email_id= %s", user
	)
	if student:
		return frappe.db.sql(
			"""
			select name, program, due_date, grand_total - outstanding_amount as paid_amount,
			outstanding_amount, grand_total, currency, status
			from `tabFees`
			where student= %s and docstatus=1
			order by due_date asc limit {0} , {1}""".format(
				limit_start, limit_page_length
			),
			student,
			as_dict=True,
		)


def get_list_context(context=None):
	return {
		"show_sidebar": True,
		"show_search": True,
		"no_breadcrumbs": True,
		"title": _("Fees"),
		"get_list": get_fee_list,
		"row_template": "templates/includes/fee/fee_row.html",
	}
