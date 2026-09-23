# Copyright (c) 2015, Frappe Technologies and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class Program(Document):
	def validate(self):
		self.validate_study_scheme()
		self.calculate_total_credit_hours()

	def validate_study_scheme(self):
		"""Keep the scheme within the declared semester count and free of duplicates."""
		seen = set()
		for row in self.study_scheme:
			if cint(row.semester) < 1:
				frappe.throw(
					_("Semester in row {0} must be 1 or greater.").format(row.idx)
				)
			if self.no_of_semesters and cint(row.semester) > cint(self.no_of_semesters):
				frappe.throw(
					_("Semester {0} in row {1} exceeds the program's {2} semesters.").format(
						row.semester, row.idx, self.no_of_semesters
					)
				)
			key = (cint(row.semester), row.course)
			if key in seen:
				frappe.throw(
					_("Course {0} is repeated in semester {1}.").format(
						frappe.bold(row.course), row.semester
					)
				)
			seen.add(key)

	def calculate_total_credit_hours(self):
		self.total_credit_hours = sum(flt(row.credit_hours) for row in self.study_scheme)

	def get_course_list(self):
		program_course_list = self.courses
		course_list = [
			frappe.get_doc("Course", program_course.course)
			for program_course in program_course_list
		]
		return course_list

	def get_scheme_courses(self, semester=None):
		"""Rows of the study scheme, optionally limited to one semester."""
		if semester is None:
			return list(self.study_scheme)
		return [row for row in self.study_scheme if cint(row.semester) == cint(semester)]

	@frappe.whitelist()
	def build_scheme_from_courses(self, semester=1):
		"""Seed the study scheme from the legacy Program Course table."""
		existing = {(cint(r.semester), r.course) for r in self.study_scheme}
		added = 0
		for course in self.courses:
			if (cint(semester), course.course) in existing:
				continue
			self.append(
				"study_scheme",
				{
					"semester": cint(semester),
					"course": course.course,
					"course_name": course.course_name,
					"credit_hours": frappe.db.get_value(
						"Course", course.course, "credit_hours"
					),
					"required": course.required,
				},
			)
			added += 1
		return added


@frappe.whitelist()
def get_program_study_scheme(program, semester=None):
	"""Study scheme rows for a program, used by Course Registration."""
	filters = {"parent": program, "parenttype": "Program"}
	if semester:
		filters["semester"] = cint(semester)

	return frappe.get_all(
		"Program Study Scheme",
		filters=filters,
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
