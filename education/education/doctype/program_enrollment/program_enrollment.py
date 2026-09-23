# Copyright (c) 2015, Frappe and contributors
# For license information, please see license.txt


import frappe
from frappe import _, msgprint
from frappe.desk.reportview import get_match_cond
from frappe.model.document import Document
from frappe.query_builder.functions import Min
from frappe.utils import cint, comma_and, flt, get_link_to_form, getdate
from education.education.doctype.fee_plan.fee_plan import get_applicable_fee_plan
from education.education.doctype.fee_schedule.fee_schedule import (
	create_sales_invoice,
	create_sales_order,
)
from education.education.utils import get_default_company, is_course_based_billing


class ProgramEnrollment(Document):
	def validate(self):
		self.set_student_name()
		self.validate_duplication()

		if not self.courses:
			self.extend("courses", self.get_courses())

		self.set_fee_plan()
		self.set_study_scheme()
		self.calculate_total_credit_hours()

	def set_fee_plan(self):
		"""Stamp the rate card in force on the enrollment date.

		Resolved once and never re-resolved, so a 2020 intake keeps its 2020
		prices even after newer plans are published.
		"""
		if self.fee_plan or self.docstatus != 0:
			return

		self.fee_plan = get_applicable_fee_plan(
			program=self.program,
			student_category=self.student_category,
			company=get_default_company(),
			on_date=self.enrollment_date,
			academic_year=self.academic_year,
		)

		if not self.fee_plan and is_course_based_billing():
			frappe.msgprint(
				_(
					"No Fee Plan applies to {0} on {1}. Fees for this student cannot be priced until one is submitted."
				).format(frappe.bold(self.program), frappe.bold(self.enrollment_date)),
				alert=True,
			)

	def set_study_scheme(self):
		"""Freeze the program's curriculum onto the enrollment."""
		if self.study_scheme or self.docstatus != 0:
			return

		scheme = frappe.get_all(
			"Program Study Scheme",
			filters={"parent": self.program, "parenttype": "Program"},
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

		for row in scheme:
			self.append("study_scheme", dict(row, status="Planned"))

	def calculate_total_credit_hours(self):
		self.total_credit_hours = sum(flt(row.credit_hours) for row in self.study_scheme)

	@frappe.whitelist()
	def refresh_study_scheme_from_program(self):
		"""Explicit, deliberate re-sync. Fee overrides already frozen are kept."""
		frozen = {(cint(r.semester), r.course): r for r in self.study_scheme}
		self.set("study_scheme", [])
		self.set_study_scheme()

		for row in self.study_scheme:
			previous = frozen.get((cint(row.semester), row.course))
			if previous:
				row.status = previous.status
				row.academic_term = previous.academic_term
				if flt(previous.fee_override):
					row.fee_override = previous.fee_override

		self.calculate_total_credit_hours()
		self.save()
		return len(self.study_scheme)

	def get_scheme_rows(self, semester):
		return [row for row in self.study_scheme if cint(row.semester) == cint(semester)]

	def set_student_name(self):
		if not self.student_name:
			self.student_name = frappe.db.get_value("Student", self.student, "student_name")

	def on_submit(self):
		self.update_student_joining_date()
		self.make_fee_records()
		self.create_course_enrollments()

	def on_cancel(self):
		self.delete_course_enrollments()
		pass

	def validate_duplication(self):
		enrollment = frappe.db.exists(
			"Program Enrollment",
			{
				"student": self.student,
				"program": self.program,
				"academic_year": self.academic_year,
				"academic_term": self.academic_term,
				"docstatus": ("<", 2),
				"name": ("!=", self.name),
			},
		)
		if enrollment:
			frappe.throw(_("Student is already enrolled."))

	def update_student_joining_date(self):
		table = frappe.qb.DocType("Program Enrollment")
		date = (
			frappe.qb.from_(table)
			.select(Min(table.enrollment_date).as_("enrollment_date"))
			.where(table.student == self.student)
		).run(as_dict=True)

		if date:
			frappe.db.set_value("Student", self.student, "joining_date", date[0].enrollment_date)

	def make_fee_records(self):
		"""Legacy structure-based billing.

		Course-based billing raises fees from Course Registration instead, so
		this is skipped entirely when that engine is on.
		"""
		if is_course_based_billing():
			return

		create_so = frappe.db.get_single_value("Education Settings", "create_so")

		fees_list = []
		doctype = ""
		for d in self.fees:
			if create_so:
				sales_order = create_sales_order(d.fee_schedule, self.student)
				doctype = "Sales Order"
				fees_list.append(sales_order)
			else:
				sales_invoice = create_sales_invoice(d.fee_schedule, self.student)
				doctype = "Sales Invoice"
				fees_list.append(sales_invoice)

		if fees_list:
			fees_list = [
				"""<a href="/app/Form/%s/%s" target="_blank">%s</a>""" % (doctype, fee, fee)
				for fee in fees_list
			]
			msgprint(_("Fee Records Created - {0}").format(comma_and(fees_list)))

	@frappe.whitelist()
	def get_courses(self):
		return frappe.db.sql(
			"""select course,course_name from `tabProgram Course` where parent = %s and required = 1""",
			(self.program),
			as_dict=1,
		)

	def create_course_enrollments(self):
		for course in self.courses:
			filters = {
				"student": self.student,
				"course": course.course,
				"program_enrollment": self.name,
			}
			if not frappe.db.exists("Course Enrollment", filters):
				filters.update(
					{"doctype": "Course Enrollment", "enrollment_date": self.enrollment_date}
				)
				frappe.get_doc(filters).save()

	def get_all_course_enrollments(self):
		course_enrollment_names = frappe.get_list(
			"Course Enrollment", filters={"program_enrollment": self.name}
		)
		return [
			frappe.get_doc("Course Enrollment", course_enrollment.name)
			for course_enrollment in course_enrollment_names
		]

	def delete_course_enrollments(self):
		for course_enrollment in self.get_all_course_enrollments():
			frappe.delete_doc("Course Enrollment", course_enrollment.name)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_program_courses(doctype, txt, searchfield, start, page_len, filters):
	if not filters.get("program"):
		frappe.msgprint(_("Please select a Program first."))
		return []

	doctype = "Program Course"
	return frappe.db.sql(
		"""select course, course_name from `tabProgram Course`
        where  parent = %(program)s and course like %(txt)s {match_cond}
        order by
            if(locate(%(_txt)s, course), locate(%(_txt)s, course), 99999),
            idx desc,
            `tabProgram Course`.course asc
        limit {start}, {page_len}""".format(
			match_cond=get_match_cond(doctype), start=start, page_len=page_len
		),
		{
			"txt": "%{0}%".format(txt),
			"_txt": txt.replace("%", ""),
			"program": filters["program"],
		},
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_students(doctype, txt, searchfield, start, page_len, filters):
	if not filters.get("academic_term"):
		filters["academic_term"] = frappe.defaults.get_defaults().academic_term

	if not filters.get("academic_year"):
		filters["academic_year"] = frappe.defaults.get_defaults().academic_year

	enrolled_students = frappe.get_list(
		"Program Enrollment",
		filters={
			"academic_term": filters.get("academic_term"),
			"academic_year": filters.get("academic_year"),
		},
		fields=["student"],
	)

	students = [d.student for d in enrolled_students] if enrolled_students else [""]

	return frappe.db.sql(
		"""select
            name, student_name from tabStudent
        where
            name not in (%s)
        and
            `%s` LIKE %s
        order by
            idx desc, name
        limit %s, %s"""
		% (", ".join(["%s"] * len(students)), searchfield, "%s", "%s", "%s"),
		tuple(students + ["%%%s%%" % txt, start, page_len]),
	)
