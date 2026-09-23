// Copyright (c) 2015, Frappe Technologies and contributors
// For license information, please see license.txt

frappe.ui.form.on('Program Course', {
  courses_add: function (frm) {
    frm.fields_dict['courses'].grid.get_field('course').get_query = function (
      doc
    ) {
      var courses_list = []
      $.each(doc.courses, function (idx, val) {
        if (val.course) courses_list.push(val.course)
      })
      return { filters: [['Course', 'name', 'not in', courses_list]] }
    }
  },
})

frappe.ui.form.on('Program', {
  refresh: function (frm) {
    if (frm.is_new()) return

    if ((frm.doc.courses || []).length && !(frm.doc.study_scheme || []).length) {
      frm.add_custom_button(__('Build Study Scheme from Courses'), function () {
        frappe.prompt(
          {
            fieldname: 'semester',
            fieldtype: 'Int',
            label: __('Put all courses in semester'),
            default: 1,
            reqd: 1,
          },
          function (values) {
            frm.call('build_scheme_from_courses', { semester: values.semester }).then((r) => {
              frm.refresh_field('study_scheme')
              frappe.show_alert({
                message: __('{0} course(s) added to the study scheme', [r.message || 0]),
                indicator: r.message ? 'green' : 'orange',
              })
            })
          },
          __('Build Study Scheme'),
          __('Build')
        )
      })
    }

    frm.add_custom_button(__('Fee Plans'), function () {
      frappe.set_route('List', 'Fee Plan', { program: frm.doc.name })
    }, __('View'))
  },
})

frappe.ui.form.on('Program Study Scheme', {
  study_scheme_add: function (frm, cdt, cdn) {
    // Keep adding to the semester the registrar is currently working on.
    const rows = frm.doc.study_scheme || []
    if (rows.length > 1) {
      const row = locals[cdt][cdn]
      row.semester = rows[rows.length - 2].semester
      frm.refresh_field('study_scheme')
    }
  },
})
