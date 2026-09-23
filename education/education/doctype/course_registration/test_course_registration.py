# Copyright (c) 2026, Frappe and contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate

from education.education.doctype.fee_plan.fee_plan import get_applicable_fee_plan
from education.education.test_utils import (
	create_academic_term,
	create_academic_year,
	create_course_registration,
	create_course_with_fee,
	create_fee_category_with_accounts,
	create_fee_plan,
	create_program_enrollment,
	create_program_with_scheme,
	create_student,
)

def get_company():
	"""Use whichever company the site already has, so accounts resolve."""
	company = frappe.get_all(
		"Company",
		fields=["name", "default_income_account", "cost_center", "default_receivable_account"],
		limit=1,
	)[0]

	# A chart of accounts that flags an income account as Receivable cannot
	# carry fee income without a party, so pick one that can.
	clean = frappe.get_all(
		"Account",
		filters={
			"company": company.name,
			"root_type": "Income",
			"is_group": 0,
			"account_type": ["not in", ["Receivable", "Payable"]],
		},
		pluck="name",
		limit=1,
	)
	if clean:
		company.default_income_account = clean[0]
	return company


class TestCourseBasedFees(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_company()

		create_academic_year("2020-2021", "2020-09-01", "2021-08-31")
		create_academic_year("2024-2025", "2024-09-01", "2025-08-31")
		create_academic_term(
			academic_year="2024-2025",
			term_name="Fall",
			term_start_date="2024-09-01",
			term_end_date="2025-01-31",
		)
		create_academic_term(
			academic_year="2024-2025",
			term_name="Spring",
			term_start_date="2025-02-01",
			term_end_date="2025-06-30",
		)

		cls.tuition_category = create_fee_category_with_accounts(
			"_Test Tuition",
			cls.company.name,
			cls.company.default_income_account,
			cls.company.cost_center,
		)
		cls.enrollment_category = create_fee_category_with_accounts(
			"_Test Enrollment Fee",
			cls.company.name,
			cls.company.default_income_account,
			cls.company.cost_center,
		)

		cls.course_a = create_course_with_fee(
			"_Test Course A", credit_hours=3, fee_category=cls.tuition_category.name
		)
		cls.course_b = create_course_with_fee(
			"_Test Course B", credit_hours=4, fee_category=cls.tuition_category.name
		)
		cls.course_elective = create_course_with_fee(
			"_Test Course Elective", credit_hours=2, fee_category=cls.tuition_category.name
		)

		cls.program = create_program_with_scheme(
			"_Test BS Program",
			[
				{"semester": 1, "course": cls.course_a.name, "course_type": "Core"},
				{"semester": 1, "course": cls.course_b.name, "course_type": "Core"},
				{
					"semester": 2,
					"course": cls.course_elective.name,
					"course_type": "Elective",
				},
			],
		)

		# Two cohorts, two prices for the very same course.
		cls.plan_2020 = create_fee_plan(
			"_Test Plan 2020",
			cls.company.name,
			effective_from="2020-09-01",
			program=cls.program.name,
			income_account=cls.company.default_income_account,
			rate_per_credit_hour=1000,
			components=[
				{
					"fee_category": cls.enrollment_category.name,
					"amount": 5000,
					"charge_frequency": "Once at Admission",
				},
			],
		)
		cls.plan_2024 = create_fee_plan(
			"_Test Plan 2024",
			cls.company.name,
			effective_from="2024-09-01",
			program=cls.program.name,
			income_account=cls.company.default_income_account,
			rate_per_credit_hour=1500,
			repeat_course_rate=50,
			elective_course_rate=80,
			components=[
				{
					"fee_category": cls.enrollment_category.name,
					"amount": 8000,
					"charge_frequency": "Once at Admission",
				},
				{
					"fee_category": cls.tuition_category.name,
					"amount": 2000,
					"charge_frequency": "Every Semester",
				},
				{
					"fee_category": cls.tuition_category.name,
					"amount": 3000,
					"charge_frequency": "On Event",
					"event": "Semester Change",
				},
			],
		)

	def enroll(self, email, enrollment_date, academic_year):
		student = create_student(
			first_name="Test", last_name=email.split("@")[0], student_email_id=email
		)
		enrollment = create_program_enrollment(
			student.name,
			program=self.program.name,
			academic_year=academic_year,
			academic_term=None,
			enrollment_date=enrollment_date,
			submit=True,
		)
		return student, enrollment

	def test_plan_resolution_picks_the_cohort_in_force(self):
		self.assertEqual(
			get_applicable_fee_plan(
				program=self.program.name,
				company=self.company.name,
				on_date="2021-01-15",
			),
			self.plan_2020.name,
		)
		self.assertEqual(
			get_applicable_fee_plan(
				program=self.program.name,
				company=self.company.name,
				on_date="2024-10-01",
			),
			self.plan_2024.name,
		)

	def test_enrollment_freezes_plan_and_scheme(self):
		_, enrollment = self.enroll(
			"cohort2020@example.com", "2020-09-15", "2020-2021"
		)
		self.assertEqual(enrollment.fee_plan, self.plan_2020.name)
		self.assertEqual(len(enrollment.study_scheme), 3)
		self.assertEqual(flt(enrollment.total_credit_hours), 9.0)

	def test_same_course_costs_different_amounts_per_cohort(self):
		_, old = self.enroll("old@example.com", "2020-09-15", "2020-2021")
		_, new = self.enroll("new@example.com", "2024-09-15", "2024-2025")

		courses = [{"course": self.course_a.name, "status": "Registered"}]

		old_registration = create_course_registration(
			old.student,
			old.name,
			"2024-2025",
			"2024-2025 (Fall)",
			company=self.company.name,
			courses=courses,
		)
		new_registration = create_course_registration(
			new.student,
			new.name,
			"2024-2025",
			"2024-2025 (Fall)",
			company=self.company.name,
			courses=courses,
		)

		# 3 credit hours at the cohort's own rate.
		self.assertEqual(flt(old_registration.tuition_amount), 3000.0)
		self.assertEqual(flt(new_registration.tuition_amount), 4500.0)

	def test_tuition_is_the_sum_of_registered_courses(self):
		_, enrollment = self.enroll("sum@example.com", "2024-09-15", "2024-2025")

		registration = create_course_registration(
			enrollment.student,
			enrollment.name,
			"2024-2025",
			"2024-2025 (Fall)",
			company=self.company.name,
			courses=[
				{"course": self.course_a.name, "status": "Registered"},
				{"course": self.course_b.name, "status": "Registered"},
			],
		)

		# (3 + 4) credit hours x 1500
		self.assertEqual(flt(registration.total_credit_hours), 7.0)
		self.assertEqual(flt(registration.tuition_amount), 10500.0)

	def test_dropped_courses_are_not_billed(self):
		_, enrollment = self.enroll("dropped@example.com", "2024-09-15", "2024-2025")

		registration = create_course_registration(
			enrollment.student,
			enrollment.name,
			"2024-2025",
			"2024-2025 (Fall)",
			company=self.company.name,
			courses=[
				{"course": self.course_a.name, "status": "Registered"},
				{"course": self.course_b.name, "status": "Dropped"},
			],
		)

		self.assertEqual(flt(registration.tuition_amount), 4500.0)
		self.assertEqual(registration.total_courses, 1)

	def test_repeat_and_elective_rates_apply(self):
		_, enrollment = self.enroll("repeat@example.com", "2024-09-15", "2024-2025")

		registration = create_course_registration(
			enrollment.student,
			enrollment.name,
			"2024-2025",
			"2024-2025 (Fall)",
			company=self.company.name,
			courses=[
				{"course": self.course_a.name, "status": "Registered", "is_repeat": 1},
				{
					"course": self.course_elective.name,
					"status": "Registered",
					"course_type": "Elective",
				},
			],
		)

		amounts = {row.course: flt(row.amount) for row in registration.courses}
		# 3 x 1500 at 50%
		self.assertEqual(amounts[self.course_a.name], 2250.0)
		# 2 x 1500 at 80%
		self.assertEqual(amounts[self.course_elective.name], 2400.0)

	def test_admission_component_charged_once(self):
		_, enrollment = self.enroll("admission@example.com", "2024-09-15", "2024-2025")

		first = create_course_registration(
			enrollment.student,
			enrollment.name,
			"2024-2025",
			"2024-2025 (Fall)",
			company=self.company.name,
			courses=[{"course": self.course_a.name, "status": "Registered"}],
			submit=True,
		)
		first_categories = [row.fees_category for row in first.components]
		self.assertIn(self.enrollment_category.name, first_categories)
		# 8000 admission + 2000 every-semester
		self.assertEqual(flt(first.component_amount), 10000.0)

		second = create_course_registration(
			enrollment.student,
			enrollment.name,
			"2024-2025",
			"2024-2025 (Spring)",
			semester=2,
			company=self.company.name,
			courses=[{"course": self.course_b.name, "status": "Registered"}],
		)
		second_categories = [row.fees_category for row in second.components]
		self.assertNotIn(self.enrollment_category.name, second_categories)
		self.assertEqual(flt(second.component_amount), 2000.0)

	def test_event_charge_is_added_on_request(self):
		_, enrollment = self.enroll("event@example.com", "2024-09-15", "2024-2025")

		registration = create_course_registration(
			enrollment.student,
			enrollment.name,
			"2024-2025",
			"2024-2025 (Fall)",
			company=self.company.name,
			courses=[{"course": self.course_a.name, "status": "Registered"}],
			event_charges="Semester Change",
		)

		# 8000 admission + 2000 every-semester + 3000 semester change
		self.assertEqual(flt(registration.component_amount), 13000.0)

	def test_submitting_registration_creates_fee_and_balanced_gl(self):
		_, enrollment = self.enroll("billing@example.com", "2024-09-15", "2024-2025")

		frappe.db.set_single_value(
			"Education Settings", "create_fee_on_course_registration", 1
		)

		registration = create_course_registration(
			enrollment.student,
			enrollment.name,
			"2024-2025",
			"2024-2025 (Fall)",
			company=self.company.name,
			courses=[
				{"course": self.course_a.name, "status": "Registered"},
				{"course": self.course_b.name, "status": "Registered"},
			],
			submit=True,
		)
		registration.reload()

		self.assertTrue(registration.fee)
		fee = frappe.get_doc("Fees", registration.fee)

		self.assertEqual(len(fee.tuition_items), 2)
		self.assertEqual(flt(fee.total_tuition), 10500.0)
		self.assertEqual(flt(fee.total_components), 10000.0)
		self.assertEqual(flt(fee.grand_total), 20500.0)
		self.assertEqual(fee.status, "Draft")

		fee.submit()
		fee.reload()
		self.assertIn(fee.status, ("Unpaid", "Overdue"))

		entries = frappe.get_all(
			"GL Entry",
			filters={"voucher_type": "Fees", "voucher_no": fee.name, "is_cancelled": 0},
			fields=["account", "debit", "credit"],
		)
		self.assertTrue(entries)
		self.assertAlmostEqual(
			sum(flt(e.debit) for e in entries),
			sum(flt(e.credit) for e in entries),
			places=2,
		)
		self.assertAlmostEqual(
			sum(flt(e.debit) for e in entries), 20500.0, places=2
		)

	def test_course_enrollments_created_on_submit(self):
		_, enrollment = self.enroll("enrol@example.com", "2024-09-15", "2024-2025")

		create_course_registration(
			enrollment.student,
			enrollment.name,
			"2024-2025",
			"2024-2025 (Fall)",
			company=self.company.name,
			courses=[{"course": self.course_a.name, "status": "Registered"}],
			submit=True,
		)

		self.assertTrue(
			frappe.db.exists(
				"Course Enrollment",
				{
					"student": enrollment.student,
					"course": self.course_a.name,
					"program_enrollment": enrollment.name,
				},
			)
		)

	def test_duplicate_registration_for_same_term_is_blocked(self):
		_, enrollment = self.enroll("dupe@example.com", "2024-09-15", "2024-2025")

		create_course_registration(
			enrollment.student,
			enrollment.name,
			"2024-2025",
			"2024-2025 (Fall)",
			company=self.company.name,
			courses=[{"course": self.course_a.name, "status": "Registered"}],
		)

		self.assertRaises(
			frappe.ValidationError,
			create_course_registration,
			enrollment.student,
			enrollment.name,
			"2024-2025",
			"2024-2025 (Fall)",
			company=self.company.name,
			courses=[{"course": self.course_b.name, "status": "Registered"}],
		)


class TestTuitionBasesAndBulkBilling(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_company()

		create_academic_year("2025-2026", "2025-09-01", "2026-08-31")
		create_academic_term(
			academic_year="2025-2026",
			term_name="Fall",
			term_start_date="2025-09-01",
			term_end_date="2026-01-31",
		)
		cls.term = "2025-2026 (Fall)"

		cls.category = create_fee_category_with_accounts(
			"_Test Basis Tuition",
			cls.company.name,
			cls.company.default_income_account,
			cls.company.cost_center,
		)

		cls.course_one = create_course_with_fee(
			"_Test Priced Course One",
			credit_hours=3,
			fee_basis="Per Course",
			default_course_fee=7000,
			fee_category=cls.category.name,
		)
		cls.course_two = create_course_with_fee(
			"_Test Priced Course Two",
			credit_hours=3,
			fee_basis="Per Course",
			default_course_fee=9000,
			fee_category=cls.category.name,
		)
		cls.lab = create_course_with_fee(
			"_Test Priced Lab",
			credit_hours=1,
			fee_basis="Per Course",
			default_course_fee=0,
			fee_category=cls.category.name,
		)

		cls.program = create_program_with_scheme(
			"_Test Basis Program",
			[
				{"semester": 1, "course": cls.course_one.name, "course_type": "Core"},
				{"semester": 1, "course": cls.course_two.name, "course_type": "Core"},
				{"semester": 1, "course": cls.lab.name, "course_type": "Lab"},
			],
		)

		cls.per_course_plan = create_fee_plan(
			"_Test Per Course Plan",
			cls.company.name,
			effective_from="2025-09-01",
			program=cls.program.name,
			tuition_basis="Per Course",
			income_account=cls.company.default_income_account,
			course_rate_overrides=[
				{
					"course": cls.lab.name,
					"fee_basis": "Per Course",
					"amount": 4000,
					"fee_category": cls.category.name,
				}
			],
		)
		cls.flat_plan = create_fee_plan(
			"_Test Flat Plan",
			cls.company.name,
			effective_from="2025-09-01",
			tuition_basis="Flat per Semester",
			income_account=cls.company.default_income_account,
			flat_semester_tuition=45000,
		)

	def enroll(self, email, fee_plan):
		student = create_student(
			first_name="Basis", last_name=email.split("@")[0], student_email_id=email
		)
		enrollment = create_program_enrollment(
			student.name,
			program=self.program.name,
			academic_year="2025-2026",
			academic_term=None,
			enrollment_date="2025-09-10",
			submit=False,
		)
		enrollment.fee_plan = fee_plan
		enrollment.save()
		enrollment.submit()
		return enrollment

	def register(self, enrollment, courses, submit=False):
		return create_course_registration(
			enrollment.student,
			enrollment.name,
			"2025-2026",
			self.term,
			company=self.company.name,
			courses=courses,
			submit=submit,
		)

	def test_per_course_basis_uses_the_course_fee_field(self):
		enrollment = self.enroll("percourse@example.com", self.per_course_plan.name)
		registration = self.register(
			enrollment,
			[
				{"course": self.course_one.name, "status": "Registered"},
				{"course": self.course_two.name, "status": "Registered"},
				{"course": self.lab.name, "status": "Registered"},
			],
		)

		amounts = {row.course: flt(row.amount) for row in registration.courses}
		self.assertEqual(amounts[self.course_one.name], 7000.0)
		self.assertEqual(amounts[self.course_two.name], 9000.0)
		# The plan's override beats the course's own (zero) default.
		self.assertEqual(amounts[self.lab.name], 4000.0)
		self.assertEqual(flt(registration.tuition_amount), 20000.0)

	def test_components_only_charges_no_tuition(self):
		plan = create_fee_plan(
			"_Test Components Only Plan",
			self.company.name,
			effective_from="2025-09-01",
			tuition_basis="Components Only",
			income_account=self.company.default_income_account,
			components=[
				{
					"fee_category": self.category.name,
					"amount": 33000,
					"charge_frequency": "Every Semester",
				}
			],
		)
		enrollment = self.enroll("componentsonly@example.com", plan.name)
		registration = self.register(
			enrollment,
			[
				{"course": self.course_one.name, "status": "Registered"},
				{"course": self.course_two.name, "status": "Registered"},
			],
			submit=True,
		)
		registration.reload()

		self.assertEqual(flt(registration.tuition_amount), 0.0)
		self.assertEqual(flt(registration.grand_total), 33000.0)

		fee = frappe.get_doc("Fees", registration.fee)
		self.assertEqual(len(fee.tuition_items), 0)
		self.assertEqual(flt(fee.grand_total), 33000.0)

	def test_flat_per_semester_ignores_course_count(self):
		one = self.enroll("flatone@example.com", self.flat_plan.name)
		many = self.enroll("flatmany@example.com", self.flat_plan.name)

		single = self.register(
			one, [{"course": self.course_one.name, "status": "Registered"}]
		)
		triple = self.register(
			many,
			[
				{"course": self.course_one.name, "status": "Registered"},
				{"course": self.course_two.name, "status": "Registered"},
				{"course": self.lab.name, "status": "Registered"},
			],
		)

		self.assertEqual(flt(single.tuition_amount), 45000.0)
		self.assertEqual(flt(triple.tuition_amount), 45000.0)

	def test_generation_tool_bills_unbilled_registrations_once(self):
		frappe.db.set_single_value("Education Settings", "fee_engine", "Course Based")
		frappe.db.set_single_value(
			"Education Settings", "create_fee_on_course_registration", 0
		)

		first = self.enroll("tool1@example.com", self.per_course_plan.name)
		second = self.enroll("tool2@example.com", self.per_course_plan.name)

		reg_one = self.register(
			first,
			[{"course": self.course_one.name, "status": "Registered"}],
			submit=True,
		)
		reg_two = self.register(
			second,
			[{"course": self.course_two.name, "status": "Registered"}],
			submit=True,
		)
		self.assertFalse(reg_one.fee)

		tool = frappe.get_single("Fee Generation Tool")
		tool.update(
			{
				"academic_year": "2025-2026",
				"academic_term": self.term,
				"program": self.program.name,
				"company": self.company.name,
				"posting_date": nowdate(),
			}
		)
		tool.fetch_students()
		picked = {row.course_registration for row in tool.students}
		self.assertTrue({reg_one.name, reg_two.name} <= picked)

		tool.generate_fees()
		reg_one.reload()
		reg_two.reload()
		self.assertTrue(reg_one.fee)
		self.assertTrue(reg_two.fee)
		self.assertEqual(reg_one.status, "Billed")

		# A billed registration must not be picked up a second time.
		tool.reload()
		tool.update(
			{
				"academic_year": "2025-2026",
				"academic_term": self.term,
				"program": self.program.name,
				"company": self.company.name,
			}
		)
		tool.fetch_students()
		self.assertNotIn(
			reg_one.name, {row.course_registration for row in tool.students}
		)

	def test_payment_moves_fee_to_partly_paid(self):
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

		frappe.db.set_single_value(
			"Education Settings", "create_fee_on_course_registration", 1
		)
		enrollment = self.enroll("paying@example.com", self.per_course_plan.name)
		registration = self.register(
			enrollment,
			[{"course": self.course_one.name, "status": "Registered"}],
			submit=True,
		)
		registration.reload()

		fee = frappe.get_doc("Fees", registration.fee)
		fee.submit()
		fee.reload()
		self.assertIn(fee.status, ("Unpaid", "Overdue"))

		payment = get_payment_entry(
			"Fees", fee.name, party_type="Student", payment_type="Receive"
		)
		half = flt(fee.grand_total) / 2
		payment.paid_amount = half
		payment.received_amount = half
		payment.references[0].allocated_amount = half
		payment.source_exchange_rate = 1
		payment.target_exchange_rate = 1
		payment.reference_no = "TEST-PAY"
		payment.reference_date = nowdate()
		payment.insert()
		payment.submit()

		fee.reload()
		self.assertEqual(flt(fee.paid_amount), half)
		self.assertEqual(fee.status, "Partly Paid")
