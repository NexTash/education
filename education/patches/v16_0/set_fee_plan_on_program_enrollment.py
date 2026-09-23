"""Stamp existing enrollments with their plan and freeze their study scheme.

Without this, students enrolled before the course-based engine have no rate
card and no curriculum of their own, so nothing can be priced for them.
"""

import frappe

from education.education.doctype.fee_plan.fee_plan import get_applicable_fee_plan
from education.education.utils import get_default_company


def execute():
	enrollments = frappe.get_all(
		"Program Enrollment",
		filters={"docstatus": 1},
		fields=["name", "program", "student_category", "academic_year", "enrollment_date"],
	)
	if not enrollments:
		return

	company = get_default_company()

	for enrollment in enrollments:
		updates = {}

		if not frappe.db.get_value("Program Enrollment", enrollment.name, "fee_plan"):
			plan = get_applicable_fee_plan(
				program=enrollment.program,
				student_category=enrollment.student_category,
				company=company,
				on_date=enrollment.enrollment_date,
				academic_year=enrollment.academic_year,
			)
			if plan:
				updates["fee_plan"] = plan

		if updates:
			frappe.db.set_value(
				"Program Enrollment", enrollment.name, updates, update_modified=False
			)

		if frappe.db.exists(
			"Student Study Scheme",
			{"parent": enrollment.name, "parenttype": "Program Enrollment"},
		):
			continue

		scheme = frappe.get_all(
			"Program Study Scheme",
			filters={"parent": enrollment.program, "parenttype": "Program"},
			fields=[
				"semester",
				"course",
				"course_name",
				"credit_hours",
				"course_type",
				"required",
				"fee_override",
			],
			order_by="semester asc, idx asc",
		)
		if not scheme:
			continue

		# Child rows only: the parent is submitted and must not be rewritten.
		for idx, row in enumerate(scheme, start=1):
			frappe.get_doc(
				dict(
					row,
					doctype="Student Study Scheme",
					parent=enrollment.name,
					parenttype="Program Enrollment",
					parentfield="study_scheme",
					idx=idx,
					status="Planned",
				)
			).insert(ignore_permissions=True)
