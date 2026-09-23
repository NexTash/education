// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on('Fee Plan', {
  onload: function (frm) {
    frm.set_query('receivable_account', function (doc) {
      return {
        filters: { account_type: 'Receivable', is_group: 0, company: doc.company },
      }
    })

    frm.set_query('income_account', function (doc) {
      return {
        filters: { root_type: 'Income', is_group: 0, company: doc.company },
      }
    })

    frm.set_query('cost_center', function (doc) {
      return { filters: { is_group: 0, company: doc.company } }
    })
  },

  refresh: function (frm) {
    if (frm.doc.docstatus === 1) {
      frm.add_custom_button(__('Preview Pricing'), function () {
        frm.events.preview_pricing(frm)
      })
    }
  },

  preview_pricing: function (frm) {
    const d = new frappe.ui.Dialog({
      title: __('Preview Pricing'),
      fields: [
        { fieldname: 'course', fieldtype: 'Link', options: 'Course', label: __('Course'), reqd: 1 },
        { fieldname: 'credit_hours', fieldtype: 'Float', label: __('Credit Hours') },
        {
          fieldname: 'course_type',
          fieldtype: 'Select',
          label: __('Course Type'),
          options: ['Core', 'Elective', 'Lab', 'Project', 'Internship'],
          default: 'Core',
        },
        { fieldname: 'is_repeat', fieldtype: 'Check', label: __('Is Repeat') },
      ],
      primary_action_label: __('Resolve'),
      primary_action(values) {
        frappe.call({
          method: 'education.education.doctype.fee_plan.fee_plan.get_course_fee',
          args: {
            plan: frm.doc.name,
            course: values.course,
            credit_hours: values.credit_hours,
            course_type: values.course_type,
            is_repeat: values.is_repeat,
          },
          callback: function (r) {
            if (!r.message) return
            frappe.msgprint({
              title: __('Resolved Fee'),
              message: __('{0} — {1} ({2})', [
                format_currency(r.message.amount, frm.doc.currency),
                r.message.fee_basis,
                r.message.rate_source,
              ]),
              indicator: 'green',
            })
          },
        })
      },
    })
    d.show()
  },
})
