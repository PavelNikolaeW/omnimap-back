from django.urls import path
from .views import RegisterView, RootBlockView, NewBlockView, \
    CopyBlockView, EditBlockView, CreateLinkBlockView, LoadEmptyView, AccessBlockView, BlockSearchAPIView, MoveBlockView

app_name = 'api'

urlpatterns = [
    path('create-link-block/', CreateLinkBlockView.as_view(), name='create-link-block'),

    path('copy-block/', CopyBlockView.as_view(), name='copy-block'),
    path('remove-block/', EditBlockView.as_view(), name='remove-block'),
    path('move-block/<uuid:block_id>/', MoveBlockView.as_view(), name='move-block'),
    path('new-block/<uuid:block_id>/', NewBlockView.as_view(), name='new-block'),
    path('root-block/', RootBlockView.as_view(), name='root-block'),
    path('edit-block/<uuid:block_id>/', EditBlockView.as_view(), name='edit-block'),
    path('register/', RegisterView.as_view(), name='register'),
    path('load-empty/', LoadEmptyView.as_view(), name='load-empty'),
    path('access/<uuid:block_id>/', AccessBlockView.as_view(), name='access-list'),
    path('search-block/', BlockSearchAPIView.as_view(), name='search-block')
]
