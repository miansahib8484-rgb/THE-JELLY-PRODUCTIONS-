from django.urls import path

from . import views

app_name = 'website'

urlpatterns = [
    path('', views.home, name='home'),
    path('courses/', views.courses, name='courses'),
    path('courses/<slug:slug>/', views.course_detail, name='course_detail'),
    path('courses/<slug:slug>/review/', views.submit_review, name='submit_review'),
    path('enroll/', views.enroll, name='enroll'),
    path('enroll/promo/', views.promo_check, name='promo_check'),
    path('about/', views.about, name='about'),
    path('contact/', views.contact, name='contact'),
    path('blog/', views.blog_list, name='blog'),
    path('blog/<slug:slug>/', views.blog_detail, name='blog_detail'),
    path('newsletter/subscribe/', views.newsletter_subscribe, name='newsletter_subscribe'),
    path('newsletter/unsubscribe/<uuid:token>/', views.newsletter_unsubscribe, name='newsletter_unsubscribe'),
    path('terms/', views.legal, {'slug': 'terms'}, name='terms'),
    path('privacy/', views.legal, {'slug': 'privacy'}, name='privacy'),
    path('refund-policy/', views.legal, {'slug': 'refund'}, name='refund'),
    path('verify/<str:code>/', views.verify_certificate, name='verify_certificate'),
]
