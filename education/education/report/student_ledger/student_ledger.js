// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.query_reports['Student Ledger'] = {
  filters: [
    {
      fieldname: 'student',
      label: __('Student'),
      fieldtype: 'Link',
      options: 'Student',
      reqd: 1,
    },
    {
      fieldname: 'company',
      label: __('Company'),
      fieldtype: 'Link',
      options: 'Company',
      default: frappe.defaults.get_user_default('Company'),
    },
  ],

  formatter: function (value, row, column, data, default_formatter) {
    value = default_formatter(value, row, column, data)
    if (data && data.is_group) {
      value = `<span style="font-weight:600">${value}</span>`
    }
    return value
  },
}
