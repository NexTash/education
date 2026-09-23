# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Fee Plan is the rate card.

One plan prices any number of programs for one intake cohort, which is what
keeps the university from maintaining a Fee Structure per program per semester.
Everything that decides *how much* a student owes is resolved from here.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate, nowdate

TUITION_PER_CREDIT_HOUR = "Per Credit Hour"
TUITION_PER_COURSE = "Per Course"
TUITION_FLAT = "Flat per Semester"
TUITION_COMPONENTS_ONLY = "Components Only"


class FeePlan(Document):
	def validate(self):
		self.set_missing_accounts()
		self.validate_dates()
		self.validate_tuition_basis()
		self.validate_components()
		self.validate_course_rate_overrides()
		validate_income_account(self.income_account)

	def set_missing_accounts(self):
		if not self.company:
			self.company = frappe.defaults.get_defaults().company

		if not self.company:
			return

		defaults = frappe.db.get_value(
			"Company",
			self.company,
			[
				"default_currency",
				"default_receivable_account",
				"default_income_account",
				"cost_center",
			],
			as_dict=True,
		)
		if not defaults:
			return

		if not self.currency:
			self.currency = defaults.default_currency
		if not self.receivable_account:
			self.receivable_account = defaults.default_receivable_account
		if not self.income_account and is_valid_income_account(
			defaults.default_income_account
		):
			# Only adopt the company default when it can actually take the entry.
			self.income_account = defaults.default_income_account
		if not self.cost_center:
			self.cost_center = defaults.cost_center

	def validate_dates(self):
		if self.effective_till and getdate(self.effective_till) < getdate(self.effective_from):
			frappe.throw(_("Effective Till cannot be earlier than Effective From."))

	def validate_tuition_basis(self):
		if self.tuition_basis == TUITION_COMPONENTS_ONLY:
			if not self.components:
				frappe.throw(
					_("A {0} plan needs at least one Fee Component.").format(
						frappe.bold(TUITION_COMPONENTS_ONLY)
					)
				)
			return

		if self.tuition_basis == TUITION_PER_CREDIT_HOUR and not flt(
			self.rate_per_credit_hour
		):
			frappe.throw(
				_("Rate per Credit Hour is required for the {0} tuition basis.").format(
					frappe.bold(self.tuition_basis)
				)
			)
		if self.tuition_basis == TUITION_FLAT and not flt(self.flat_semester_tuition):
			frappe.throw(
				_("Flat Semester Tuition is required for the {0} tuition basis.").format(
					frappe.bold(self.tuition_basis)
				)
			)
		if self.tuition_basis == TUITION_PER_COURSE:
			unpriced = frappe.get_all(
				"Course",
				filters={"default_course_fee": ["in", [0, None]]},
				pluck="name",
				limit=1,
			)
			if unpriced:
				frappe.msgprint(
					_(
						"Tuition basis is {0}. Courses without a Default Course Fee, such as {1}, will be billed at zero unless listed under Course Rate Overrides."
					).format(frappe.bold(self.tuition_basis), frappe.bold(unpriced[0])),
					alert=True,
				)

	def validate_components(self):
		for row in self.components:
			if row.charge_frequency == "On Event" and not row.event:
				frappe.throw(
					_("Row {0}: select an Event for a component charged On Event.").format(row.idx)
				)
			if (
				cint(row.applies_till_semester)
				and cint(row.applies_from_semester) > cint(row.applies_till_semester)
			):
				frappe.throw(
					_("Row {0}: Applies From Semester cannot be after Applies Till Semester.").format(
						row.idx
					)
				)
			component_account = get_component_income_account(row.fee_category, self.company)
			if not component_account:
				frappe.msgprint(
					_("Fee Category {0} in row {1} has no income account for {2}.").format(
						frappe.bold(row.fee_category), row.idx, frappe.bold(self.company)
					),
					alert=True,
				)
				continue

			# Warn here and hard-fail on the Fees document: the account only has
			# to be postable by the time money is actually recorded.
			problem = check_income_account(component_account)
			if problem:
				frappe.msgprint(
					_("Row {0}: {1}").format(row.idx, problem),
					title=_("Fee Category account needs attention"),
					indicator="orange",
				)

	def validate_course_rate_overrides(self):
		seen = set()
		for row in self.course_rate_overrides:
			if row.course in seen:
				frappe.throw(
					_("Course {0} appears more than once in Course Rate Overrides.").format(
						frappe.bold(row.course)
					)
				)
			seen.add(row.course)

	def get_course_rate_override(self, course):
		for row in self.course_rate_overrides:
			if row.course == course:
				return row
		return None


def check_income_account(account):
	"""Why an account cannot carry fee income, or None if it can.

	A chart of accounts that marks an income account as Receivable or Payable
	makes every GL entry demand a party, and the failure surfaces at submit with
	a message that says nothing about fees. Catch it where it is configured.
	"""
	if not account:
		return None

	details = frappe.db.get_value(
		"Account", account, ["root_type", "account_type", "is_group"], as_dict=True
	)
	if not details:
		return None

	if details.is_group:
		return _("Income Account {0} is a group account.").format(frappe.bold(account))

	if details.root_type != "Income":
		return _(
			"Account {0} has root type {1}. Fee income must post to an Income account."
		).format(frappe.bold(account), frappe.bold(details.root_type))

	if details.account_type in ("Receivable", "Payable"):
		return _(
			"Account {0} is an Income account but its Account Type is {1}. Clear the Account Type so fee income can post without a party."
		).format(frappe.bold(account), frappe.bold(details.account_type))

	return None


def is_valid_income_account(account):
	return not check_income_account(account)


def validate_income_account(account):
	problem = check_income_account(account)
	if problem:
		frappe.throw(problem)


def get_component_income_account(fee_category, company):
	if not (fee_category and company):
		return None
	return frappe.db.get_value(
		"Fee Category Default",
		{"parent": fee_category, "company": company},
		"income_account",
	)


def get_component_cost_center(fee_category, company):
	if not (fee_category and company):
		return None
	return frappe.db.get_value(
		"Fee Category Default",
		{"parent": fee_category, "company": company},
		"selling_cost_center",
	)


@frappe.whitelist()
def get_applicable_fee_plan(
	program=None, student_category=None, company=None, on_date=None, academic_year=None
):
	"""The plan that governs a student, most specific match wins.

	A plan that names a program beats one that leaves it blank, and among equally
	specific plans the one that came into effect most recently wins.
	"""
	on_date = getdate(on_date or nowdate())

	filters = {"docstatus": 1, "disabled": 0}
	if company:
		filters["company"] = company

	candidates = frappe.get_all(
		"Fee Plan",
		filters=filters,
		fields=[
			"name",
			"program",
			"student_category",
			"intake_academic_year",
			"effective_from",
			"effective_till",
		],
	)

	best, best_score = None, -1
	for plan in candidates:
		if plan.effective_from and getdate(plan.effective_from) > on_date:
			continue
		if plan.effective_till and getdate(plan.effective_till) < on_date:
			continue

		score = 0
		# A plan that names a dimension only applies when that dimension matches.
		if plan.program:
			if plan.program != program:
				continue
			score += 4
		if plan.student_category:
			if plan.student_category != student_category:
				continue
			score += 2
		if plan.intake_academic_year:
			if plan.intake_academic_year != academic_year:
				continue
			score += 1

		if score > best_score or (
			score == best_score
			and best
			and getdate(plan.effective_from) > getdate(best.effective_from)
		):
			best, best_score = plan, score

	return best.name if best else None


def _as_plan(plan):
	return plan if isinstance(plan, Document) else frappe.get_cached_doc("Fee Plan", plan)


@frappe.whitelist()
def get_course_fee(
	plan,
	course,
	credit_hours=None,
	fee_override=None,
	is_repeat=0,
	course_type=None,
):
	"""Price one course under one plan.

	Precedence, most specific first:
	  1. the Study Scheme's fee override for this course in this program
	  2. a Course Rate Override row on the plan
	  3. the plan's tuition basis
	Elective and repeat multipliers are applied to whichever of those produced
	the amount.
	"""
	plan = _as_plan(plan)
	course_doc = frappe.get_cached_doc("Course", course)

	credit_hours = flt(credit_hours) if credit_hours is not None else flt(
		course_doc.credit_hours
	)

	result = {
		"course": course,
		"course_name": course_doc.course_name,
		"credit_hours": credit_hours,
		"rate": 0.0,
		"amount": 0.0,
		"fee_basis": plan.tuition_basis,
		"fee_category": course_doc.fee_category,
		"rate_source": None,
	}

	override_row = plan.get_course_rate_override(course)

	if flt(fee_override):
		result["amount"] = flt(fee_override)
		result["rate"] = flt(fee_override)
		result["fee_basis"] = TUITION_PER_COURSE
		result["rate_source"] = "Study Scheme Override"
	elif override_row:
		result["fee_basis"] = override_row.fee_basis
		result["rate"] = flt(override_row.amount)
		result["amount"] = (
			flt(override_row.amount) * credit_hours
			if override_row.fee_basis == TUITION_PER_CREDIT_HOUR
			else flt(override_row.amount)
		)
		result["fee_category"] = override_row.fee_category or result["fee_category"]
		result["rate_source"] = "Fee Plan Course Rate"
	elif plan.tuition_basis == TUITION_PER_CREDIT_HOUR:
		result["rate"] = flt(plan.rate_per_credit_hour)
		result["amount"] = flt(plan.rate_per_credit_hour) * credit_hours
		result["rate_source"] = "Fee Plan Rate per Credit Hour"
	elif plan.tuition_basis == TUITION_PER_COURSE:
		result["rate"] = flt(course_doc.default_course_fee)
		result["amount"] = flt(course_doc.default_course_fee)
		result["rate_source"] = "Course Default Fee"
	else:
		# Flat per Semester charges once for the semester; Components Only
		# charges no tuition at all. Neither prices a course.
		result["rate_source"] = plan.tuition_basis

	if course_type == "Elective" and flt(plan.elective_course_rate):
		result["amount"] = result["amount"] * flt(plan.elective_course_rate) / 100.0
	if cint(is_repeat) and flt(plan.repeat_course_rate):
		result["amount"] = result["amount"] * flt(plan.repeat_course_rate) / 100.0

	result["amount"] = flt(result["amount"], get_currency_precision())
	result["rate"] = flt(result["rate"], get_currency_precision())
	return result


@frappe.whitelist()
def get_plan_components(
	plan,
	semester=1,
	is_admission=0,
	is_first_term_of_year=0,
	events=None,
):
	"""Non-tuition charges that apply to one billing run.

	This is where the enrollment fee, semester change fee and the flat
	per-semester charges come from.
	"""
	plan = _as_plan(plan)
	semester = cint(semester)

	if isinstance(events, str):
		events = frappe.parse_json(events)
	events = set(events or [])

	applicable = []
	for row in plan.components:
		if cint(row.applies_from_semester) and semester < cint(row.applies_from_semester):
			continue
		if cint(row.applies_till_semester) and semester > cint(row.applies_till_semester):
			continue

		if row.charge_frequency == "Once at Admission" and not cint(is_admission):
			continue
		if row.charge_frequency == "Annually" and not cint(is_first_term_of_year):
			continue
		if row.charge_frequency == "On Event" and row.event not in events:
			continue

		applicable.append(
			{
				"fees_category": row.fee_category,
				"description": row.description,
				"amount": flt(row.amount, get_currency_precision()),
				"charge_frequency": row.charge_frequency,
				"event": row.event,
				"is_refundable": row.is_refundable,
			}
		)

	return applicable


def get_currency_precision():
	return cint(frappe.db.get_default("currency_precision")) or 2
