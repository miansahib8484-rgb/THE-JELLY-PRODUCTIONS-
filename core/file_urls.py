from django.urls import path

from . import files

app_name = 'files'
urlpatterns = [
    path('certificate/<int:pk>/', files.certificate_file, name='certificate'),
    path('payment-proof/<uuid:pk>/', files.payment_proof, name='proof'),
    path('recording/<int:pk>/', files.recording_file, name='recording'),
]
