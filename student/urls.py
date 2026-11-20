from django.urls import path

from . import views

app_name = 'student'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('api/summary/', views.api_summary, name='api_summary'),
    path('courses/', views.courses, name='courses'),
    path('courses/<slug:slug>/learn/', views.player, name='player'),
    path('lectures/<int:pk>/toggle/', views.toggle_lecture, name='toggle_lecture'),
    path('live/', views.live, name='live'),
    path('recordings/<int:pk>/', views.watch, name='watch'),
    path('certificates/', views.certificates, name='certificates'),
    path('reviews/', views.reviews, name='reviews'),
    path('reviews/<int:pk>/edit/', views.review_edit, name='review_edit'),
    path('reviews/<int:pk>/delete/', views.review_delete, name='review_delete'),
    path('billing/', views.billing, name='billing'),
    path('billing/<uuid:enrollment_id>/pay/', views.pay, name='pay'),
    path('billing/<uuid:enrollment_id>/cancel/', views.cancel_reservation, name='cancel_reservation'),
]
