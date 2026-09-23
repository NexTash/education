"""Turn each submitted Fee Structure into a Fee Plan.

The structure's components become Every Semester components on the plan, so a
site keeps charging what it charged before while it moves its tuition onto a
course basis at its own pace.
"""

import frappe
from frappe.utils import flt

from education.education.doctype.fee_plan.fee_plan import check_income_account


def get_fallback_fee_category():
	"""A category to hang an un-itemised structure total on."""
	name = "Migrated Fee"
	if not frappe.db.exists("Fee Category", name):
		frappe.get_doc(
			{
				"doctype": "Fee Category",
				"category_name": name,
				"description": "Created by the Fee Plan migration for structures with no components.",
			}
		).insert(ignore_permissions=True)
	return name


def execute():
	structures = frappe.get_all(
		"Fee Structure",
		filters={"docstatus": 1},
		fields=[
			"name",
			"program",
			"student_category",
			"academic_year",
			"company",
			"receivable_account",
			"cost_center",
			"total_amount",
		],
	)

	for structure in structures:
		plan_name = "{0} (migrated)".format(structure.name)
		if frappe.db.exists("Fee Plan", plan_name):
			continue

		year_start = frappe.db.get_value(
			"Academic Year", structure.academic_year, "year_start_date"
		)
		if not year_start:
			continue

		plan = frappe.new_doc("Fee Plan")
		plan.update(
			{
				"plan_name": plan_name,
				"program": structure.program,
				"student_category": structure.student_category,
				"intake_academic_year": structure.academic_year,
				"effective_from": year_start,
				"company": structure.company or frappe.defaults.get_defaults().company,
				"receivable_account": structure.receivable_account,
				"cost_center": structure.cost_center,
				# A Fee Structure has no tuition basis: its whole total sits in
				# the components. Carry that over verbatim and let the school
				# move to a course basis when it is ready.
				"tuition_basis": "Components Only",
			}
		)

		components = frappe.get_all(
			"Fee Component",
			filters={"parent": structure.name, "parenttype": "Fee Structure"},
			fields=["fees_category", "description", "amount"],
			order_by="idx asc",
		)
		for component in components:
			plan.append(
				"components",
				{
					"fee_category": component.fees_category,
					"description": component.description,
					"amount": flt(component.amount),
					"charge_frequency": "Every Semester",
				},
			)

		if not components:
			# Nothing itemised: bill the structure total as one component.
			plan.append(
				"components",
				{
					"fee_category": get_fallback_fee_category(),
					"amount": flt(structure.total_amount),
					"charge_frequency": "Every Semester",
				},
			)

		# A chart of accounts that flags an income account as Receivable cannot
		# carry fee income. Leave the field blank rather than failing the whole
		# migration, and say so.
		problem = check_income_account(plan.income_account)
		if problem:
			frappe.msgprint(
				"Fee Plan {0}: leaving the income account blank. {1}".format(
					plan_name, frappe.utils.strip_html(problem)
				)
			)
			plan.income_account = None

		try:
			plan.flags.ignore_permissions = True
			plan.insert()
			plan.submit()
		except Exception:
			frappe.log_error(
				title="Fee Plan migration failed for {0}".format(structure.name),
				message=frappe.get_traceback(),
			)
			frappe.db.rollback()
