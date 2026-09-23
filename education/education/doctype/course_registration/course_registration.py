# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Course Registration is what the registrar does every semester anyway.

It is also the billing trigger: the courses on it are priced from the student's
own Fee Plan, so the university never configures a fee document per program per
semester.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, nowdate

from education.education.doctype.fee_plan.fee_plan import (
	TUITION_COMPONENTS_ONLY,
	TUITION_FLAT,
	get_course_fee,
	get_plan_components,
)
from education.education.utils import (
	get_default_company,
	is_first_term_of_academic_year,
)

BILLABLE_STATUSES = ("Registered",)


class CourseRegistration(Document):
	def validate(self):
		self.set_missing_values()
		self.validate_enrollment()
		self.validate_academic_term()
		self.validate_duplicate_registration()
		self.autofill_scheme_courses()
		self.validate_courses()
		self.price_courses()
		self.set_components()
		self.calculate_totals()

	def set_missing_values(self):
		if not self.company:
			self.company = get_default_company()
		if not self.registration_date:
			self.registration_date = nowdate()

		enrollment = frappe.db.get_value(
			"Program Enrollment",
			self.program_enrollment,
			["student", "program", "fee_plan", "current_semester", "academic_year"],
			as_dict=True,
		)
		if not enrollment:
			return

		self.program = enrollment.program
		if not self.fee_plan:
			self.fee_plan = enrollment.fee_plan
		if not self.semester:
			self.semester = cint(enrollment.current_semester) or 1
		if not self.academic_year:
			self.academic_year = enrollment.academic_year

		if not self.currency and self.company:
			self.currency = frappe.db.get_value(
				"Company", self.company, "default_currency"
			)

		if not self.is_admission:
			self.is_admission = cint(not self.has_earlier_registration())

	def has_earlier_registration(self):
		return bool(
			frappe.db.exists(
				"Course Registration",
				{
					"program_enrollment": self.program_enrollment,
					"docstatus": 1,
					"name": ["!=", self.name],
				},
			)
		)

	def validate_enrollment(self):
		enrollment_student = frappe.db.get_value(
			"Program Enrollment", self.program_enrollment, "student"
		)
		if enrollment_student != self.student:
			frappe.throw(
				_("Program Enrollment {0} does not belong to student {1}.").format(
					frappe.bold(self.program_enrollment), frappe.bold(self.student)
				)
			)

	def validate_academic_term(self):
		if not self.academic_term:
			return
		term_year = frappe.db.get_value(
			"Academic Term", self.academic_term, "academic_year"
		)
		if term_year and self.academic_year and term_year != self.academic_year:
			frappe.throw(
				_("Academic Term {0} belongs to {1}, not {2}.").format(
					frappe.bold(self.academic_term),
					frappe.bold(term_year),
					frappe.bold(self.academic_year),
				)
			)

	def validate_duplicate_registration(self):
		duplicate = frappe.db.exists(
			"Course Registration",
			{
				"program_enrollment": self.program_enrollment,
				"academic_term": self.academic_term,
				"docstatus": ["<", 2],
				"name": ["!=", self.name],
			},
		)
		if duplicate:
			frappe.throw(
				_("{0} already registers this student for {1}.").format(
					frappe.utils.get_link_to_form("Course Registration", duplicate),
					frappe.bold(self.academic_term),
				)
			)

	def autofill_scheme_courses(self):
		"""Load the semester's courses the first time the registration is saved.

		The registrar should not have to type a course in just to be allowed to
		save, and only then press a button to fetch the real list.
		"""
		if self.courses or self.docstatus != 0:
			return
		if not (self.program_enrollment and self.semester):
			return
		self.fetch_scheme_courses(self.semester)

	def validate_courses(self):
		seen = set()
		for row in self.courses:
			if row.course in seen:
				frappe.throw(
					_("Course {0} is registered more than once.").format(frappe.bold(row.course))
				)
			seen.add(row.course)

	def price_courses(self):
		"""Resolve every registered course against the student's own plan."""
		if not self.fee_plan:
			for row in self.courses:
				row.rate = 0
				row.amount = 0
				row.rate_source = None
			return

		plan = frappe.get_cached_doc("Fee Plan", self.fee_plan)
		scheme = self.get_scheme_overrides()

		for row in self.courses:
			if row.status not in BILLABLE_STATUSES:
				row.rate = 0
				row.amount = 0
				row.fee_basis = None
				row.rate_source = _("Not billable ({0})").format(row.status)
				continue

			scheme_row = scheme.get(row.course) or {}
			if not row.course_type and scheme_row.get("course_type"):
				row.course_type = scheme_row["course_type"]
			if not flt(row.credit_hours):
				row.credit_hours = flt(scheme_row.get("credit_hours"))
			row.scheme_semester = scheme_row.get("semester")

			priced = get_course_fee(
				plan,
				row.course,
				credit_hours=row.credit_hours,
				fee_override=scheme_row.get("fee_override"),
				is_repeat=row.is_repeat,
				course_type=row.course_type,
			)
			row.fee_basis = priced["fee_basis"]
			row.rate = priced["rate"]
			row.amount = priced["amount"]
			row.fee_category = priced["fee_category"]
			row.rate_source = priced["rate_source"]

	def get_scheme_overrides(self):
		"""The student's frozen study scheme, keyed by course."""
		rows = frappe.get_all(
			"Student Study Scheme",
			filters={
				"parent": self.program_enrollment,
				"parenttype": "Program Enrollment",
			},
			fields=[
				"semester",
				"course",
				"course_type",
				"credit_hours",
				"fee_override",
			],
		)
		return {row.course: row for row in rows}

	def get_event_charges(self):
		if not self.event_charges:
			return []
		return [event.strip() for event in self.event_charges.split(",") if event.strip()]

	def set_components(self):
		"""Pull the semester's non-tuition charges from the plan.

		Rows the user added by hand are preserved; only plan-sourced rows are
		refreshed, so a one-off charge typed into the grid survives a re-save.
		"""
		if not self.fee_plan:
			return

		plan = frappe.get_cached_doc("Fee Plan", self.fee_plan)
		manual = [
			{
				"fees_category": row.fees_category,
				"description": row.description,
				"amount": row.amount,
				"discount": row.discount,
			}
			for row in self.components
			if not (row.description or "").startswith("[Plan]")
		]

		resolved = get_plan_components(
			plan,
			semester=self.semester,
			is_admission=self.is_admission,
			is_first_term_of_year=is_first_term_of_academic_year(
				self.academic_term, self.academic_year
			),
			events=self.get_event_charges(),
		)

		manual_categories = {row["fees_category"] for row in manual}
		self.set("components", [])
		for row in manual:
			self.append("components", row)

		for component in resolved:
			if component["fees_category"] in manual_categories:
				continue
			self.append(
				"components",
				{
					"fees_category": component["fees_category"],
					"description": "[Plan] {0}".format(
						component.get("description") or component["charge_frequency"]
					),
					"amount": component["amount"],
				},
			)

	def calculate_totals(self):
		billable = [row for row in self.courses if row.status in BILLABLE_STATUSES]
		self.total_courses = len(billable)
		self.total_credit_hours = sum(flt(row.credit_hours) for row in billable)

		if self.fee_plan:
			plan = frappe.get_cached_doc("Fee Plan", self.fee_plan)
			if plan.tuition_basis == TUITION_FLAT:
				# One charge for the semester, no matter how many courses.
				self.tuition_amount = flt(plan.flat_semester_tuition) if billable else 0
			elif plan.tuition_basis == TUITION_COMPONENTS_ONLY:
				self.tuition_amount = 0
			else:
				self.tuition_amount = sum(flt(row.amount) for row in billable)
		else:
			self.tuition_amount = sum(flt(row.amount) for row in billable)

		for row in self.components:
			row.total = flt(row.amount) - (flt(row.amount) * flt(row.discount) / 100)

		self.component_amount = sum(flt(row.total) for row in self.components)
		self.grand_total = flt(self.tuition_amount) + flt(self.component_amount)

	def before_submit(self):
		if not [row for row in self.courses if row.status in BILLABLE_STATUSES]:
			frappe.throw(
				_(
					"Add at least one registered course. Use Fetch Scheme Courses to load semester {0} from the study scheme."
				).format(self.semester)
			)
		if not self.fee_plan:
			frappe.throw(
				_(
					"No Fee Plan is set. Submit a Fee Plan covering {0} before registering courses."
				).format(frappe.bold(self.program))
			)
		self.status = "Registered"

	def on_submit(self):
		self.create_course_enrollments()
		self.update_study_scheme_status("Registered")
		self.update_enrollment_semester()

		if frappe.db.get_single_value(
			"Education Settings", "create_fee_on_course_registration"
		):
			self.create_fee(throw_on_zero=False)

	def on_cancel(self):
		self.ignore_linked_doctypes = ("GL Entry", "Payment Ledger Entry")
		self.validate_fee_not_submitted()
		self.delete_course_enrollments()
		self.update_study_scheme_status("Planned")
		self.db_set("status", "Cancelled")

	def validate_fee_not_submitted(self):
		if self.fee and frappe.db.get_value("Fees", self.fee, "docstatus") == 1:
			frappe.throw(
				_("Cancel {0} before cancelling this registration.").format(
					frappe.utils.get_link_to_form("Fees", self.fee)
				)
			)

	def create_course_enrollments(self):
		"""Keep attendance and assessment working off Course Enrollment."""
		for row in self.courses:
			if row.status not in BILLABLE_STATUSES:
				continue
			filters = {
				"student": self.student,
				"course": row.course,
				"program_enrollment": self.program_enrollment,
			}
			if frappe.db.exists("Course Enrollment", filters):
				continue
			frappe.get_doc(
				dict(
					filters,
					doctype="Course Enrollment",
					enrollment_date=self.registration_date,
				)
			).insert(ignore_permissions=True)

	def delete_course_enrollments(self):
		for row in self.courses:
			name = frappe.db.exists(
				"Course Enrollment",
				{
					"student": self.student,
					"course": row.course,
					"program_enrollment": self.program_enrollment,
				},
			)
			if name:
				frappe.delete_doc(
					"Course Enrollment", name, ignore_permissions=True, force=True
				)

	def update_study_scheme_status(self, status):
		registered = {row.course for row in self.courses if row.status in BILLABLE_STATUSES}
		if not registered:
			return

		enrollment = frappe.get_doc("Program Enrollment", self.program_enrollment)
		changed = False
		for row in enrollment.study_scheme:
			if row.course in registered:
				row.db_set("status", status, update_modified=False)
				row.db_set(
					"academic_term",
					self.academic_term if status == "Registered" else None,
					update_modified=False,
				)
				changed = True
		if changed:
			enrollment.notify_update()

	def update_enrollment_semester(self):
		current = cint(
			frappe.db.get_value(
				"Program Enrollment", self.program_enrollment, "current_semester"
			)
		)
		if cint(self.semester) > current:
			frappe.db.set_value(
				"Program Enrollment",
				self.program_enrollment,
				"current_semester",
				cint(self.semester),
			)

	@frappe.whitelist()
	def fetch_scheme_courses(self, semester=None):
		"""Load this semester's courses from the student's frozen study scheme."""
		semester = cint(semester or self.semester)
		existing = {row.course for row in self.courses}

		rows = frappe.get_all(
			"Student Study Scheme",
			filters={
				"parent": self.program_enrollment,
				"parenttype": "Program Enrollment",
				"semester": semester,
			},
			fields=["course", "course_name", "credit_hours", "course_type", "status"],
			order_by="idx asc",
		)

		added = 0
		for row in rows:
			if row.course in existing or row.status in ("Completed", "Exempted"):
				continue
			self.append(
				"courses",
				{
					"course": row.course,
					"course_name": row.course_name,
					"credit_hours": row.credit_hours,
					"course_type": row.course_type,
					"status": "Registered",
					"is_repeat": 1 if row.status == "Failed" else 0,
					"scheme_semester": semester,
				},
			)
			added += 1

		return added

	@frappe.whitelist()
	def create_fee(self, throw_on_zero=True):
		"""Raise the Fees document for this registration."""
		if self.docstatus != 1:
			frappe.throw(_("Submit the registration before creating a fee."))

		if self.fee and frappe.db.get_value("Fees", self.fee, "docstatus") != 2:
			frappe.throw(
				_("Fee {0} already exists for this registration.").format(
					frappe.utils.get_link_to_form("Fees", self.fee)
				)
			)

		if not flt(self.grand_total):
			if throw_on_zero:
				frappe.throw(_("Nothing to bill: the registration total is zero."))
			return None

		fee = frappe.new_doc("Fees")
		fee.update(
			{
				"student": self.student,
				"program_enrollment": self.program_enrollment,
				"program": self.program,
				"academic_year": self.academic_year,
				"academic_term": self.academic_term,
				"semester": self.semester,
				"fee_plan": self.fee_plan,
				"course_registration": self.name,
				"company": self.company,
				"currency": self.currency,
				"posting_date": nowdate(),
				"due_date": add_days(
					nowdate(),
					cint(
						frappe.db.get_single_value("Education Settings", "default_fee_due_days")
					)
					or 30,
				),
				"total_credit_hours": self.total_credit_hours,
			}
		)

		plan = frappe.get_cached_doc("Fee Plan", self.fee_plan)
		fee.receivable_account = plan.receivable_account
		fee.income_account = plan.income_account
		fee.cost_center = plan.cost_center

		if plan.tuition_basis == TUITION_COMPONENTS_ONLY:
			pass
		elif plan.tuition_basis == TUITION_FLAT:
			fee.append(
				"tuition_items",
				{
					"description": _("Semester Tuition"),
					"fee_basis": TUITION_FLAT,
					"rate": flt(self.tuition_amount),
					"amount": flt(self.tuition_amount),
					"credit_hours": self.total_credit_hours,
				},
			)
		else:
			for row in self.courses:
				if row.status not in BILLABLE_STATUSES:
					continue
				fee.append(
					"tuition_items",
					{
						"course": row.course,
						"course_name": row.course_name,
						"credit_hours": row.credit_hours,
						"fee_basis": row.fee_basis,
						"rate": row.rate,
						"amount": row.amount,
						"fee_category": row.fee_category,
						"is_repeat": row.is_repeat,
					},
				)

		for row in self.components:
			fee.append(
				"components",
				{
					"fees_category": row.fees_category,
					"description": row.description,
					"amount": row.amount,
					"discount": row.discount,
				},
			)

		fee.insert(ignore_permissions=True)

		if frappe.db.get_single_value("Education Settings", "auto_submit_fees"):
			fee.submit()

		self.db_set("fee", fee.name)
		self.db_set("status", "Billed")

		frappe.msgprint(
			_("Fee {0} created.").format(
				frappe.utils.get_link_to_form("Fees", fee.name)
			),
			alert=True,
		)
		return fee.name


@frappe.whitelist()
def get_registered_students(academic_year, academic_term, program=None, student_category=None):
	"""Submitted registrations that have not been billed yet."""
	filters = {
		"docstatus": 1,
		"academic_year": academic_year,
		"academic_term": academic_term,
		"status": "Registered",
	}
	if program:
		filters["program"] = program

	registrations = frappe.get_all(
		"Course Registration",
		filters=filters,
		fields=[
			"name",
			"student",
			"student_name",
			"program",
			"program_enrollment",
			"semester",
			"total_credit_hours",
			"grand_total",
			"currency",
		],
		order_by="program asc, student_name asc",
	)

	if not student_category:
		return registrations

	allowed = set(
		frappe.get_all(
			"Program Enrollment",
			filters={"student_category": student_category, "docstatus": 1},
			pluck="name",
		)
	)
	return [r for r in registrations if r.program_enrollment in allowed]
