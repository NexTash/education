# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Bulk driver for course-based billing.

Replaces the Fee Schedule loop: instead of a schedule document per program per
semester, the registrar filters a term here, previews what every student owes
under their own Fee Plan, and generates the Fees in one background run.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, cstr, flt, nowdate
from frappe.utils.background_jobs import enqueue

from education.education.doctype.course_registration.course_registration import (
	get_registered_students,
)
from education.education.utils import get_default_company


class FeeGenerationTool(Document):
	@frappe.whitelist()
	def fetch_students(self):
		"""Preview: every unbilled registration matching the filters."""
		self.set("students", [])
		self.error_log = None

		if not self.company:
			self.company = get_default_company()

		registrations = get_registered_students(
			academic_year=self.academic_year,
			academic_term=self.academic_term,
			program=self.program,
			student_category=self.student_category,
		)

		group_students = self.get_student_group_members()

		for registration in registrations:
			if group_students is not None and registration.student not in group_students:
				continue

			self.append(
				"students",
				{
					"select_student": 1,
					"student": registration.student,
					"student_name": registration.student_name,
					"program": registration.program,
					"semester": registration.semester,
					"total_credit_hours": registration.total_credit_hours,
					"course_registration": registration.name,
					"fee_plan": frappe.db.get_value(
						"Course Registration", registration.name, "fee_plan"
					),
					"grand_total": registration.grand_total,
				},
			)

		self.calculate_summary()
		return len(self.students)

	def get_student_group_members(self):
		if not self.student_group:
			return None
		return set(
			frappe.get_all(
				"Student Group Student",
				filters={"parent": self.student_group, "active": 1},
				pluck="student",
			)
		)

	def calculate_summary(self):
		selected = [row for row in self.students if row.select_student]
		self.total_students = len(selected)
		self.total_amount = sum(flt(row.grand_total) for row in selected)

	@frappe.whitelist()
	def generate_fees(self):
		selected = [
			row.course_registration for row in self.students if row.select_student
		]
		if not selected:
			frappe.throw(_("Select at least one student."))

		self.save()

		if len(selected) > 10:
			frappe.msgprint(
				_(
					"Fees for {0} students will be created in the background. Any failures are listed in the Error Log below."
				).format(len(selected))
			)
			enqueue(
				generate_fees_in_background,
				queue="long",
				timeout=6000,
				event="education_generate_fees",
				registrations=selected,
				due_date=self.due_date,
				user=frappe.session.user,
			)
			return len(selected)

		created, errors = create_fees_for_registrations(selected, self.due_date)
		self.reload()
		self.db_set("error_log", "\n".join(errors) or None)
		frappe.msgprint(
			_("{0} fee(s) created, {1} failed.").format(created, len(errors)),
			alert=True,
		)
		return created


def create_fees_for_registrations(registrations, due_date=None):
	created, errors = 0, []

	for index, name in enumerate(registrations):
		savepoint = "fee_gen_{0}".format(index)
		try:
			frappe.db.savepoint(savepoint)
			registration = frappe.get_doc("Course Registration", name)
			fee = registration.create_fee(throw_on_zero=False)
			if fee and due_date:
				frappe.db.set_value("Fees", fee, "due_date", due_date)
			if fee:
				created += 1
		except Exception as exception:
			frappe.db.rollback(save_point=savepoint)
			errors.append("{0}: {1}".format(name, cstr(exception)))

	return created, errors


def generate_fees_in_background(registrations, due_date=None, user=None):
	created, errors = create_fees_for_registrations(registrations, due_date)

	frappe.db.set_value(
		"Fee Generation Tool",
		"Fee Generation Tool",
		"error_log",
		"\n".join(errors) or None,
	)

	if user:
		frappe.publish_realtime(
			"education_fee_generation_done",
			{"created": created, "failed": len(errors)},
			user=user,
		)


@frappe.whitelist()
def get_default_due_date():
	days = cint(
		frappe.db.get_single_value("Education Settings", "default_fee_due_days")
	) or 30
	return add_days(nowdate(), days)
