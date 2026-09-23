// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on('Fee Generation Tool', {
  onload: function (frm) {
    frm.set_query('academic_term', function (doc) {
      return { filters: { academic_year: doc.academic_year } }
    })
    frm.set_query('student_group', function (doc) {
      return { filters: { program: doc.program || undefined } }
    })

    if (!frm.doc.posting_date) frm.set_value('posting_date', frappe.datetime.get_today())
    if (!frm.doc.due_date) {
      frappe.call('education.education.doctype.fee_generation_tool.fee_generation_tool.get_default_due_date').then((r) => {
        if (r.message) frm.set_value('due_date', r.message)
      })
    }
  },

  refresh: function (frm) {
    frm.disable_save()
    frm.page.clear_indicator()

    if (frm.doc.students && frm.doc.students.length) {
      frm.page.set_primary_action(__('Generate Fees'), function () {
        frappe.confirm(
          __('Create fees for {0} selected student(s)?', [frm.doc.total_students || 0]),
          function () {
            frm.call('generate_fees').then(() => frm.reload_doc())
          }
        )
      })
    } else {
      frm.page.set_primary_action(__('Get Students'), function () {
        frm.trigger('get_students')
      })
    }

    frappe.realtime.on('education_fee_generation_done', function (data) {
      frappe.show_alert({
        message: __('{0} fee(s) created, {1} failed', [data.created, data.failed]),
        indicator: data.failed ? 'orange' : 'green',
      })
      frm.reload_doc()
    })
  },

  get_students: function (frm) {
    frm.call('fetch_students').then((r) => {
      frm.refresh_field('students')
      frm.refresh_fields(['total_students', 'total_amount'])
      frappe.show_alert({
        message: __('{0} unbilled registration(s) found', [r.message || 0]),
        indicator: r.message ? 'green' : 'orange',
      })
      frm.refresh()
    })
  },

  academic_year: function (frm) {
    frm.set_value('academic_term', '')
    frm.set_value('students', [])
  },

  academic_term: function (frm) {
    frm.set_value('students', [])
  },

  program: function (frm) {
    frm.set_value('students', [])
  },
})

frappe.ui.form.on('Fee Generation Tool Student', {
  select_student: function (frm) {
    let selected = (frm.doc.students || []).filter((row) => row.select_student)
    frm.set_value('total_students', selected.length)
    frm.set_value(
      'total_amount',
      selected.reduce((sum, row) => sum + flt(row.grand_total), 0)
    )
  },
})
