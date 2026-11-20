from django.urls import path

from core import portal
from core.permissions import role_required

from . import views

app_name = 'teacher'
T = role_required('teacher')

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('api/summary/', views.api_summary, name='api_summary'),
    path('students/', views.students, name='students'),
    path('students/<uuid:pk>/', views.student_detail, name='student_detail'),
    path('reviews/', views.reviews, name='reviews'),
    path('performance/', views.performance, name='performance'),
    path('announcements/', views.announcements, name='announcements'),
    path('announcements/<int:pk>/delete/', views.announcement_delete, name='announcement_delete'),

    # shared with the admin dashboard (core/portal.py), scoped to this teacher's own courses
    path('courses/', T(portal.courses_list), name='courses'),
    path('courses/new/', T(portal.course_form), name='course_create'),
    path('courses/<slug:slug>/edit/', T(portal.course_form), name='course_edit'),
    path('courses/<slug:slug>/toggle/', T(portal.course_toggle), name='course_toggle'),
    path('courses/<slug:slug>/delete/', T(portal.course_delete), name='course_delete'),
    path('courses/<slug:slug>/builder/', T(portal.builder), name='builder'),
    path('courses/<slug:slug>/modules/add/', T(portal.module_add), name='module_add'),
    path('courses/<slug:slug>/batches/add/', T(portal.batch_add), name='batch_add'),
    path('modules/<int:pk>/delete/', T(portal.module_delete), name='module_delete'),
    path('modules/<int:pk>/lectures/add/', T(portal.lecture_add), name='lecture_add'),
    path('lectures/<int:pk>/delete/', T(portal.lecture_delete), name='lecture_delete'),
    path('batches/<int:pk>/delete/', T(portal.batch_delete), name='batch_delete'),
    path('batches/<int:pk>/edit/', T(portal.batch_edit), name='batch_edit'),
    path('modules/<int:pk>/edit/', T(portal.module_edit), name='module_edit'),
    path('lectures/<int:pk>/edit/', T(portal.lecture_edit), name='lecture_edit'),

    path('live/', T(portal.live_list), name='live'),
    path('live/new/', T(portal.live_form), name='live_create'),
    path('live/<int:pk>/edit/', T(portal.live_form), name='live_edit'),
    path('live/<int:pk>/delete/', T(portal.live_delete), name='live_delete'),
    path('live/<int:pk>/end/', T(portal.live_end), name='live_end'),
    path('live/<int:pk>/recording/', T(portal.recording), name='recording'),
    path('live/<int:pk>/recording/delete/', T(portal.recording_delete), name='recording_delete'),

    path('certificates/', T(portal.certificates), name='certificates'),
    path('certificates/<int:pk>/delete/', T(portal.certificate_delete), name='certificate_delete'),
]
