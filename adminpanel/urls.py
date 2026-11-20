from django.urls import path

from core import portal
from core.permissions import role_required

from . import views

app_name = 'adminpanel'
A = role_required('admin')

urlpatterns = [
    path('', views.overview, name='overview'),
    path('reports/', views.reports, name='reports'),
    path('reports/export/<slug:kind>/', views.export, name='export'),
    path('api/summary/', views.api_summary, name='api_summary'),

    path('teachers/', views.teachers, name='teachers'),
    path('students/', views.students, name='students'),
    path('users/<int:pk>/', views.user_detail, name='user_detail'),
    path('users/<int:pk>/toggle/', views.user_toggle, name='user_toggle'),
    path('users/<int:pk>/role/', views.user_set_role, name='user_set_role'),
    path('users/<int:pk>/password-reset/', views.user_password_reset, name='user_password_reset'),
    path('enrollments/', views.enrollments, name='enrollments'),
    path('enrollments/<uuid:pk>/mark-paid/', views.enrollment_mark_paid, name='enrollment_mark_paid'),
    path('enrollments/<uuid:pk>/cancel/', views.enrollment_cancel, name='enrollment_cancel'),
    path('enrollments/<uuid:pk>/refund/', views.enrollment_refund, name='enrollment_refund'),
    path('payments/', views.payments, name='payments'),
    path('payments/<uuid:pk>/review/', views.payment_review, name='payment_review'),
    path('contact/', views.contact, name='contact'),
    path('contact/<int:pk>/approve-teacher/', views.teacher_application_approve, name='teacher_application_approve'),
    path('contact/<int:pk>/update/', views.contact_update, name='contact_update'),
    path('newsletter/', views.newsletter, name='newsletter'),
    path('newsletter/export/', views.newsletter_export, name='newsletter_export'),
    path('newsletter/<int:pk>/delete/', views.newsletter_delete, name='newsletter_delete'),

    path('reviews/', views.reviews, name='reviews'),
    path('reviews/<int:pk>/delete/', views.review_delete, name='review_delete'),

    path('settings/', views.settings_view, name='settings'),
    path('content/', views.content, name='content'),
    path('content/legal/', views.legal_save, name='legal_save'),
    path('content/sample/', views.sample_content, name='sample_content'),
    path('content/<slug:kind>/save/', views.content_item_save, name='content_item_add'),
    path('content/<slug:kind>/<int:pk>/save/', views.content_item_save, name='content_item_save'),
    path('content/<slug:kind>/<int:pk>/delete/', views.content_item_delete, name='content_item_delete'),
    path('blog/', views.blog_list, name='blog'),
    path('blog/new/', views.blog_edit, name='blog_new'),
    path('blog/<int:pk>/', views.blog_edit, name='blog_edit'),
    path('blog/<int:pk>/toggle/', views.blog_toggle, name='blog_toggle'),
    path('blog/<int:pk>/delete/', views.blog_delete, name='blog_delete'),
    path('settings/stats/save/', views.stat_save, name='stat_add'),
    path('settings/stats/<int:pk>/save/', views.stat_save, name='stat_save'),
    path('settings/stats/<int:pk>/delete/', views.stat_delete, name='stat_delete'),
    path('settings/testimonials/add/', views.testimonial_add, name='testimonial_add'),
    path('settings/testimonials/<int:pk>/save/', views.testimonial_save, name='testimonial_save'),
    path('settings/testimonials/<int:pk>/toggle/', views.testimonial_toggle, name='testimonial_toggle'),
    path('settings/testimonials/<int:pk>/delete/', views.testimonial_delete, name='testimonial_delete'),
    path('settings/promo/add/', views.promo_add, name='promo_add'),
    path('settings/promo/<int:pk>/toggle/', views.promo_toggle, name='promo_toggle'),
    path('settings/promo/<int:pk>/save/', views.promo_save, name='promo_save'),
    path('settings/promo/<int:pk>/delete/', views.promo_delete, name='promo_delete'),

    # shared with the teacher dashboard (core/portal.py), unscoped for admins
    path('courses/', A(portal.courses_list), name='courses'),
    path('courses/new/', A(portal.course_form), name='course_create'),
    path('courses/<slug:slug>/edit/', A(portal.course_form), name='course_edit'),
    path('courses/<slug:slug>/toggle/', A(portal.course_toggle), name='course_toggle'),
    path('courses/<slug:slug>/delete/', A(portal.course_delete), name='course_delete'),
    path('courses/<slug:slug>/builder/', A(portal.builder), name='builder'),
    path('courses/<slug:slug>/modules/add/', A(portal.module_add), name='module_add'),
    path('courses/<slug:slug>/batches/add/', A(portal.batch_add), name='batch_add'),
    path('modules/<int:pk>/delete/', A(portal.module_delete), name='module_delete'),
    path('modules/<int:pk>/lectures/add/', A(portal.lecture_add), name='lecture_add'),
    path('lectures/<int:pk>/delete/', A(portal.lecture_delete), name='lecture_delete'),
    path('batches/<int:pk>/delete/', A(portal.batch_delete), name='batch_delete'),
    path('batches/<int:pk>/edit/', A(portal.batch_edit), name='batch_edit'),
    path('modules/<int:pk>/edit/', A(portal.module_edit), name='module_edit'),
    path('lectures/<int:pk>/edit/', A(portal.lecture_edit), name='lecture_edit'),

    path('live/', A(portal.live_list), name='live'),
    path('live/new/', A(portal.live_form), name='live_create'),
    path('live/<int:pk>/edit/', A(portal.live_form), name='live_edit'),
    path('live/<int:pk>/delete/', A(portal.live_delete), name='live_delete'),
    path('live/<int:pk>/end/', A(portal.live_end), name='live_end'),
    path('live/<int:pk>/recording/', A(portal.recording), name='recording'),
    path('live/<int:pk>/recording/delete/', A(portal.recording_delete), name='recording_delete'),

    path('certificates/', A(portal.certificates), name='certificates'),
    path('certificates/<int:pk>/delete/', A(portal.certificate_delete), name='certificate_delete'),
]
