from django.urls import path

from .view_delete_tree import delete_tree
from .views import (RegisterView, load_tress, create_block, create_link_on_block,
                    CopyBlockView, edit_block, load_empty_blocks, AccessBlockView, BlockSearchAPIView, move_block,
                    TaskStatusView, create_new_tree, ImportBlocksView)
from .views_group import MyGroupsView, GroupCreateView, GroupDeleteView, GroupAddMemberView, GroupRemoveMemberView, \
    GroupMembersView
from .views_history import BlockHistoryListView, BlockHistoryUndoView
from .views_url import create_url, check_slug, get_urls, delete_url, block_url, load_tree

app_name = 'api'

urlpatterns = [
    path('load-trees/', load_tress, name='root-block'),
    path('load-empty/', load_empty_blocks, name='load-empty'),
    path('load-tree/', load_tree, name='load-tree'),
    path('import/', ImportBlocksView.as_view(), name='import-json'),
    path('delete-tree/<uuid:tree_id>/', delete_tree, name='delete-tree'),
    path('new-block/<uuid:parent_id>/', create_block, name='new-block'),
    path('new-tree/', create_new_tree, name='new-tree'),
    path('create-link-block/<uuid:parent_id>/<uuid:source_id>/', create_link_on_block, name='create-link-block'),
    path('move-block/<uuid:old_parent_id>/<uuid:new_parent_id>/<uuid:child_id>/', move_block, name='move-block'),
    path('copy-block/', CopyBlockView.as_view(), name='copy-block'),
    path('edit-block/<uuid:block_id>/', edit_block, name='edit-block'),

    path('create-url/<uuid:block_id>/', create_url, name='create-url'),
    path('check-url/<slug:slug>/', check_slug, name='check-url'),
    path('get-urls/<uuid:block_id>/', get_urls, name='get-urls'),
    path('delete-url/<uuid:block_id>/<slug:slug>/', delete_url, name='delete-url'),
    path('block/<slug:slug>/', block_url, name='block-url'),

    path('access/<uuid:block_id>/', AccessBlockView.as_view(), name='access-list'),
    path('groups/', MyGroupsView.as_view(), name='my_groups'),
    path('groups/<int:group_id>/members/', GroupMembersView.as_view(), name='group_members'),
    path('groups/create/', GroupCreateView.as_view(), name='create_group'),
    path('groups/<int:group_id>/delete/', GroupDeleteView.as_view(), name='delete_group'),
    path('groups/<int:group_id>/add_member/', GroupAddMemberView.as_view(), name='add_member'),
    path('groups/<int:group_id>/remove_member/<str:username>', GroupRemoveMemberView.as_view(), name='remove_member'),

    path('tasks/<str:task_id>/', TaskStatusView.as_view(), name='task_status'),
    path('register/', RegisterView.as_view(), name='register'),
    path('search-block/', BlockSearchAPIView.as_view(), name='search-block'),

    path('blocks/<uuid:block_id>/history/', BlockHistoryListView.as_view(), name='block-history-list'),
    path('undo/', BlockHistoryUndoView.as_view(), name='block-history-undo'),
]
