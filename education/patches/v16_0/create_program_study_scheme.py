"""Seed the Program Study Scheme from the legacy Program Course table.

Every existing course lands in semester 1. The registrar then redistributes
them across semesters, which is a one-time edit per program rather than the
per-term fee configuration this replaces.
"""

import frappe


def execute():
	programs = frappe.get_all("Program", pluck="name")

	for name in programs:
		if frappe.db.exists(
			"Program Study Scheme", {"parent": name, "parenttype": "Program"}
		):
			continue

		program = frappe.get_doc("Program", name)
		if not program.courses:
			continue

		for course in program.courses:
			program.append(
				"study_scheme",
				{
					"semester": 1,
					"course": course.course,
					"course_name": course.course_name,
					"credit_hours": frappe.db.get_value(
						"Course", course.course, "credit_hours"
					),
					"course_type": "Core",
					"required": course.required,
				},
			)

		if not program.no_of_semesters:
			program.no_of_semesters = 8

		program.flags.ignore_mandatory = True
		program.flags.ignore_permissions = True
		program.save()
