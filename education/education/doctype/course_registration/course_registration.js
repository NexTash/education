// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on('Course Registration', {
  onload: function (frm) {
    frm.set_query('program_enrollment', function (doc) {
      return { filters: { student: doc.student, docstatus: 1 } }
    })

    frm.set_query('academic_term', function (doc) {
      return { filters: { academic_year: doc.academic_year } }
    })
  },

  refresh: function (frm) {
    if (frm.doc.docstatus === 0 && frm.doc.program_enrollment) {
      frm.add_custom_button(__('Fetch Scheme Courses'), function () {
        frm.call('fetch_scheme_courses', { semester: frm.doc.semester }).then((r) => {
          frm.refresh_field('courses')
          frappe.show_alert({
            message: __('{0} course(s) added from the study scheme', [r.message || 0]),
            indicator: r.message ? 'green' : 'orange',
          })
        })
      })

      frm.add_custom_button(__('Add Event Charge'), function () {
        frm.events.add_event_charge(frm)
      })
    }

    if (frm.doc.docstatus === 1 && !frm.doc.fee) {
      frm.add_custom_button(__('Create Fee'), function () {
        frm.call('create_fee').then(() => frm.reload_doc())
      }).addClass('btn-primary')
    }

    if (frm.doc.fee) {
      frm.add_custom_button(__('Fee'), function () {
        frappe.set_route('Form', 'Fees', frm.doc.fee)
      }, __('View'))
    }

    if (frm.doc.docstatus === 1) {
      frm.add_custom_button(__('Course Enrollments'), function () {
        frappe.set_route('List', 'Course Enrollment', {
          program_enrollment: frm.doc.program_enrollment,
        })
      }, __('View'))
    }
  },

  add_event_charge: function (frm) {
    const d = new frappe.ui.Dialog({
      title: __('Add Event Charge'),
      fields: [
        {
          fieldname: 'event',
          fieldtype: 'Select',
          label: __('Event'),
          reqd: 1,
          options: [
            'Semester Change',
            'Program Change',
            'Late Registration',
            'Course Repeat',
            'Re-sit Examination',
            'Transcript',
            'Hostel',
            'Transport',
            'Identity Card',
            'Migration',
          ],
        },
      ],
      primary_action_label: __('Add'),
      primary_action(values) {
        const current = (frm.doc.event_charges || '')
          .split(',')
          .map((e) => e.trim())
          .filter(Boolean)
        if (!current.includes(values.event)) current.push(values.event)
        frm.set_value('event_charges', current.join(', '))
        d.hide()
      },
    })
    d.show()
  },

  program_enrollment: function (frm) {
    if (!frm.doc.program_enrollment) return
    frappe.db
      .get_value('Program Enrollment', frm.doc.program_enrollment, [
        'fee_plan',
        'current_semester',
        'academic_year',
        'program',
      ])
      .then((r) => {
        if (!r.message) return
        frm.set_value('fee_plan', r.message.fee_plan)
        frm.set_value('program', r.message.program)
        if (!frm.doc.semester) frm.set_value('semester', r.message.current_semester || 1)
        if (!frm.doc.academic_year) frm.set_value('academic_year', r.message.academic_year)
      })
  },

  semester: function (frm) {
    frm.trigger('recalculate')
  },

  event_charges: function (frm) {
    frm.trigger('recalculate')
  },

  recalculate: function (frm) {
    if (frm.doc.docstatus === 0 && frm.doc.fee_plan) frm.dirty()
  },
})

frappe.ui.form.on('Course Registration Course', {
  courses_remove: function (frm) {
    frm.dirty()
  },
  is_repeat: function (frm) {
    frm.dirty()
  },
  status: function (frm) {
    frm.dirty()
  },
  credit_hours: function (frm) {
    frm.dirty()
  },
})
