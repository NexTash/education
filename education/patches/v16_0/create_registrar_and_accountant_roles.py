"""Create the Registrar and Accountant roles with their permissions.

The university runs two desks. The registrar owns the academic configuration —
programs, courses, study schemes and who is enrolled in what. The accountant
owns the money — rate cards, billing runs, challans and payments. Each role
gets full control of its own side and read-only visibility into the other, so
neither has to ask the other for a screenshot.

Permissions are applied as Custom DocPerms, which means the doctypes touched
here stop tracking their app's shipped permissions. That is deliberate and is
the same mechanism education.install.create_permissions already uses.
"""

import frappe
from frappe.permissions import add_permission, update_permission_property

REGISTRAR = "Registrar"
ACCOUNTANT = "Accountant"

# Permission sets, smallest to largest.
READ = ("read", "print", "email", "export", "report", "share")
MANAGE = (*READ, "create", "write", "delete")
TRANSACT = (*MANAGE, "submit", "cancel", "amend")
SINGLE = ("read", "write", "print", "email", "export", "share")

PERMISSIONS = {
	REGISTRAR: {
		# academic masters the registrar owns outright
		"Program": MANAGE,
		"Course": MANAGE,
		"Academic Year": MANAGE,
		"Academic Term": MANAGE,
		"Student Category": MANAGE,
		"Student": MANAGE,
		"Course Enrollment": MANAGE,
		# the two submittable academic documents
		"Program Enrollment": TRANSACT,
		"Course Registration": TRANSACT,
		# read-only into the money side: enough to answer "has this been billed?"
		"Fee Plan": READ,
		"Fee Category": READ,
		"Fees": READ,
		"Education Settings": ("read",),
	},
	ACCOUNTANT: {
		# fee configuration and billing, owned outright
		"Fee Category": MANAGE,
		"Fee Plan": TRANSACT,
		"Fees": TRANSACT,
		"Fee Generation Tool": SINGLE,
		# payments
		"Payment Entry": TRANSACT,
		"Payment Request": TRANSACT,
		# read-only academic context needed to bill correctly
		"Student": READ,
		"Program": READ,
		"Course": READ,
		"Academic Year": READ,
		"Academic Term": READ,
		"Student Category": READ,
		"Program Enrollment": READ,
		"Course Registration": READ,
		"Course Enrollment": READ,
		"Education Settings": ("read",),
	},
}

REPORT_ROLES = {
	REGISTRAR: [
		"Student and Guardian Contact Details",
	],
	ACCOUNTANT: [
		"Student Ledger",
		"Student Fee Collection",
		"Program wise Fee Collection",
	],
}


def execute():
	for role in (REGISTRAR, ACCOUNTANT):
		create_role(role)

	for role, doctypes in PERMISSIONS.items():
		for doctype, ptypes in doctypes.items():
			apply_permissions(doctype, role, ptypes)

	for role, reports in REPORT_ROLES.items():
		for report in reports:
			grant_report_access(report, role)

	frappe.clear_cache()


def create_role(role_name):
	if frappe.db.exists("Role", role_name):
		return
	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": role_name,
			"desk_access": 1,
			"two_factor_auth": 0,
		}
	).insert(ignore_permissions=True)


def apply_permissions(doctype, role, ptypes):
	if not frappe.db.exists("DocType", doctype):
		# Payment Request and friends are only present when ERPNext is installed.
		return

	meta = frappe.get_meta(doctype)
	ptypes = [p for p in ptypes if is_applicable(meta, p)]
	if not ptypes:
		return

	existing = frappe.db.exists(
		"Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}
	)
	if not existing:
		# add_permission msgprints and returns if the rule already exists, so
		# only call it when we know it does not.
		add_permission(doctype, role, 0)

	for ptype in ptypes:
		update_permission_property(doctype, role, 0, ptype, 1)


def is_applicable(meta, ptype):
	"""Frappe rejects submit/cancel/amend on a non-submittable doctype."""
	if ptype in ("submit", "cancel", "amend") and not meta.is_submittable:
		return False
	if ptype in ("create", "delete") and meta.issingle:
		return False
	return True


def grant_report_access(report, role):
	if not frappe.db.exists("Report", report):
		return
	doc = frappe.get_doc("Report", report)
	if any(r.role == role for r in doc.roles):
		return
	doc.append("roles", {"role": role})
	doc.save(ignore_permissions=True)
