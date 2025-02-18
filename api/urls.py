from django.urls import path
from .views import (RegisterView, delete_tree, load_tress, create_block, delete_child_block, create_link_on_block,
                    CopyBlockView, edit_block, load_empty_blocks, AccessBlockView, BlockSearchAPIView, move_block,
                    create_url, block_url, check_slug,
                    TaskStatusView, get_urls, delete_url)

app_name = 'api'

urlpatterns = [
    path('load-trees/', load_tress, name='root-block'),
    path('load-empty/', load_empty_blocks, name='load-empty'),
    path('delete-tree/<uuid:tree_id>/', delete_tree, name='delete-tree'),

    path('create-url/<uuid:block_id>/', create_url, name='create-url'),
    path('check-url/<slug:slug>/', check_slug, name='check-url'),
    path('get-urls/<uuid:block_id>/', get_urls, name='get-urls'),
    path('delete-url/<uuid:block_id>/<slug:slug>/', delete_url, name='delete-url'),
    path('block/<slug:slug>/', block_url, name='block-url'),

    path('access/<uuid:block_id>/', AccessBlockView.as_view(), name='access-list'),
    path('new-block/<uuid:parent_id>/', create_block, name='new-block'),
    path('delete-child/<uuid:parent_id>/<uuid:child_id>/', delete_child_block, name='delete-child'),
    path('create-link-block/<uuid:parent_id>/<uuid:source_id>/', create_link_on_block, name='create-link-block'),
    path('move-block/<uuid:old_parent_id>/<uuid:new_parent_id>/<uuid:child_id>/', move_block, name='move-block'),
    path('copy-block/', CopyBlockView.as_view(), name='copy-block'),
    path('edit-block/<uuid:block_id>/', edit_block, name='edit-block'),

    path('tasks/<str:task_id>/', TaskStatusView.as_view(), name='task_status'),
    path('register/', RegisterView.as_view(), name='register'),
    path('search-block/', BlockSearchAPIView.as_view(), name='search-block')
]
